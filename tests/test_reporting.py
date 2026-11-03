import copy
import hashlib
import hmac
import io
import json
from datetime import timedelta

import pytest
from pypdf import PdfReader

from app.db.models import SettlementRow, utcnow
from app.integrations.paypal import MOCK_WEBHOOK_SIGNATURE, PayPalError
from app.services.outbound import MAX_ATTEMPTS, DestinationError, check_destination, deliver_due
from app.services.settlement import expire_stale_invoices
from tests.test_verification import BUDGET, SIGN_IN, create_active, h, status_of, submit


def funding(client, aid, keys):
    return client.get(f"/agreements/{aid}", headers=h(keys["payer"])).json()["funding"]


def complete_tranche_1(client, m1, keys):
    submit(client, m1, keys, "spreadsheet", "sign-in.csv", SIGN_IN)
    submit(client, m1, keys, "spreadsheet", "budget.csv", BUDGET)


@pytest.fixture
def invoice_agreement(client, draft):
    d = copy.deepcopy(draft)
    d["settlement_mode"] = "invoice_on_verify"
    return create_active(client, d, auto_release=True)


# --- invoice mode ----------------------------------------------------------------------


def test_verified_tranche_is_invoiced_then_paid(client, invoice_agreement):
    aid, (m1, _), keys = invoice_agreement
    assert client.post(f"/milestones/{m1}/fund", headers=h(keys["payer"])).status_code == 409  # nothing to fund
    complete_tranche_1(client, m1, keys)
    assert status_of(client, aid, keys) == "settling"
    assert funding(client, aid, keys)["invoiced"] == "10000.00"

    [inv_id] = client.app.state.paypal.invoices
    inv = client.app.state.paypal.invoices[inv_id]
    assert (inv["invoicer"], inv["recipient"]) == ("grantee@example.org", "funder@example.org")
    assert client.post(f"/milestones/{m1}/release", headers=h(keys["payer"])).status_code == 409  # already out

    page = client.post(f"/mock/paypal/invoices/{inv_id}/pay")
    assert "PAID" in page.text
    assert status_of(client, aid, keys) == "settled"
    assert funding(client, aid, keys) == {
        "held": "0.00",
        "invoiced": "0.00",
        "released": "10000.00",
        "unfunded": "20000.00",
    }


def test_cancelled_invoice_can_be_reissued(client, invoice_agreement):
    aid, (m1, _), keys = invoice_agreement
    complete_tranche_1(client, m1, keys)
    [inv_id] = client.app.state.paypal.invoices
    event = {"id": "WH-C1", "event_type": "INVOICING.INVOICE.CANCELLED", "resource": {"id": inv_id}}
    client.post("/webhooks/paypal", json=event, headers={"paypal-transmission-sig": MOCK_WEBHOOK_SIGNATURE})
    assert status_of(client, aid, keys) == "settlement_failed"
    assert funding(client, aid, keys)["invoiced"] == "0.00"
    r = client.post(f"/milestones/{m1}/release", headers=h(keys["payer"])).json()
    assert r["idempotency_key"] == f"{m1}-2" and r["milestone_status"] == "settling"


def test_unpaid_invoices_expire(client, invoice_agreement):
    aid, (m1, _), keys = invoice_agreement
    complete_tranche_1(client, m1, keys)
    with client.app.state.sessionmaker() as s:
        assert expire_stale_invoices(s, client.app.state.paypal, utcnow()) == 0
        assert expire_stale_invoices(s, client.app.state.paypal, utcnow() + timedelta(days=31)) == 1
        assert s.query(SettlementRow).one().status == "expired"
    assert status_of(client, aid, keys) == "settlement_failed"


def test_lost_invoice_response_resumes_with_same_key(client, invoice_agreement):
    aid, (m1, _), keys = invoice_agreement
    client.app.state.paypal.drop_next_response = True
    complete_tranche_1(client, m1, keys)  # gate tries to invoice; PayPal sends it but the response is lost
    assert status_of(client, aid, keys) == "settling"
    r = client.post(f"/milestones/{m1}/release", headers=h(keys["payer"])).json()
    assert r["idempotency_key"] == f"{m1}-1"
    assert len(client.app.state.paypal.invoices) == 1  # one invoice, not two


def test_definitive_invoice_failure(client, invoice_agreement):
    aid, (m1, _), keys = invoice_agreement
    client.app.state.paypal.fail_next = PayPalError("PERMISSION_DENIED", status=403)
    complete_tranche_1(client, m1, keys)
    assert status_of(client, aid, keys) == "settlement_failed"


# --- outbound webhooks -----------------------------------------------------------------


PUBLIC = lambda host: ["93.184.216.34"]


def test_destination_rules():
    check_destination("https://hooks.example.com/x", allow_http=False, resolve=PUBLIC)
    for url, resolver in [
        ("http://hooks.example.com/x", PUBLIC),
        ("https://internal.example.com", lambda h: ["10.0.0.5"]),
        ("https://meta.example.com", lambda h: ["169.254.169.254"]),
        ("https://v6.example.com", lambda h: ["::1"]),
        ("ftp://x.example.com", PUBLIC),
    ]:
        with pytest.raises(DestinationError):
            check_destination(url, allow_http=False, resolve=resolver)


def subscribe(client, aid, keys, events=None, role="payee"):
    client.app.state.resolve = PUBLIC
    body = {"url": "https://hooks.example.com/mde"} | ({"events": events} if events else {})
    r = client.post(f"/agreements/{aid}/subscriptions", json=body, headers=h(keys[role]))
    assert r.status_code == 200, r.text
    return r.json()


def test_events_are_signed_and_filtered(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    sub = subscribe(client, aid, keys, events=["milestone.status_changed"])
    submit(client, m1, keys, "spreadsheet", "sign-in.csv", SIGN_IN)

    sent = []
    with client.app.state.sessionmaker() as s:
        n = deliver_due(s, utcnow(), post=lambda url, body, hdrs: sent.append((url, body, hdrs)) or 200, resolve=PUBLIC)
    assert n == len(sent) > 0
    types = {json.loads(b)["data"]["to_status"] for _, b, _ in sent}
    assert {"evidence_received", "verifying", "needs_review"} <= types
    assert all(hdrs["X-MDE-Event"] == "milestone.status_changed" for _, _, hdrs in sent)

    _url, body, hdrs = sent[0]
    expected = hmac.new(sub["secret"].encode(), hdrs["X-MDE-Timestamp"].encode() + b"." + body, hashlib.sha256)
    assert hdrs["X-MDE-Signature"] == "sha256=" + expected.hexdigest()
    with client.app.state.sessionmaker() as s:
        assert deliver_due(s, utcnow(), post=lambda *a: 200, resolve=PUBLIC) == 0  # nothing left


def test_failed_deliveries_back_off_then_die(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    subscribe(client, aid, keys, events=["milestone.evidence_received", "milestone.status_changed"])
    submit(client, m1, keys, "spreadsheet", "sign-in.csv", SIGN_IN)
    now = utcnow()
    with client.app.state.sessionmaker() as s:
        assert deliver_due(s, now, post=lambda *a: 500, resolve=PUBLIC) == 0
        assert deliver_due(s, now, post=lambda *a: 200, resolve=PUBLIC) == 0  # backing off, not due yet
        later = now
        for _ in range(MAX_ATTEMPTS):
            later += timedelta(hours=7)
            deliver_due(s, later, post=lambda *a: 500, resolve=PUBLIC)
        from app.db.models import OutboxRow

        assert {r.status for r in s.query(OutboxRow)} == {"dead"}


def test_subscription_rules(client, draft):
    aid, _, keys = create_active(client, draft)
    client.app.state.resolve = lambda host: ["192.168.1.10"]
    r = client.post(
        f"/agreements/{aid}/subscriptions", json={"url": "https://router.example.com"}, headers=h(keys["payer"])
    )
    assert r.status_code == 422
    sub = subscribe(client, aid, keys, role="payee")
    listed = client.get(f"/agreements/{aid}/subscriptions", headers=h(keys["payer"])).json()
    assert [s["id"] for s in listed] == [sub["id"]] and "secret" not in listed[0]
    assert client.delete(f"/agreements/{aid}/subscriptions/{sub['id']}", headers=h(keys["payer"])).status_code == 204


# --- audit, packet, disputes -----------------------------------------------------------


def test_audit_endpoints(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    submit(client, m1, keys, "spreadsheet", "sign-in.csv", SIGN_IN)
    actions = [e["action"] for e in client.get(f"/milestones/{m1}/audit", headers=h(keys["payee"])).json()]
    assert actions[:3] == ["evidence_received", "verification_started", "sent_to_review"]
    full = client.get(f"/agreements/{aid}/audit", headers=h(keys["payer"])).json()
    assert full[0]["action"] == "agreement_created" and len(full) > len(actions)


def test_compliance_packet_json_and_pdf(client, draft):
    aid, (m1, _), keys = create_active(client, draft, auto_release=True)
    url = client.post(f"/milestones/{m1}/fund", headers=h(keys["payer"])).json()["approve_url"]
    client.get(url)
    complete_tranche_1(client, m1, keys)

    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(keys["payer"])).json()
    assert packet["agreement"]["released"] == "10000.00"
    assert all(p["confirmed_at"] for p in packet["parties"])
    t1 = packet["milestones"][0]
    assert t1["status"] == "settled" and len(t1["evidence"]) == 2
    assert t1["verdicts"][-1]["met"] and t1["verdicts"][-1]["results"][0]["evidence_ids"]
    assert t1["settlements"][0]["status"] == "succeeded"
    assert [e["kind"] for e in packet["ledger"]] == ["authorized", "captured"]

    pdf = client.get(f"/agreements/{aid}/compliance-packet?format=pdf", headers=h(keys["payer"]))
    assert pdf.headers["content-type"] == "application/pdf"
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Riverside Literacy Grant 2027" in text and "214 rows" in text
    assert client.get(f"/agreements/{aid}/compliance-packet", headers=h(keys["payee"])).status_code == 403


def test_dispute_then_payer_approves(client, draft):
    _aid, (m1, _), keys = create_active(client, draft)
    url = client.post(f"/milestones/{m1}/fund", headers=h(keys["payer"])).json()["approve_url"]
    client.get(url)
    reason = {"reason": "Sign-in sheets show 214 people; the budget sheet was attached separately."}
    assert client.post(f"/milestones/{m1}/dispute", json=reason, headers=h(keys["payee"])).status_code == 409

    submit(client, m1, keys, "spreadsheet", "sign-in.csv", SIGN_IN)
    client.post(f"/milestones/{m1}/review", json={"decision": "reject"}, headers=h(keys["payer"]))
    assert client.post(f"/milestones/{m1}/dispute", json=reason, headers=h(keys["payer"])).status_code == 403
    r = client.post(f"/milestones/{m1}/dispute", json=reason, headers=h(keys["payee"]))
    assert r.status_code == 200, r.text
    pack = r.json()
    assert pack["milestone_status"] == "disputed" and pack["pack"]["evidence"][0]["title"] == "sign-in.csv"
    assert pack["pack"]["reviews"][0]["action"] == "review_rejected"

    r = client.post(
        f"/milestones/{m1}/review", json={"decision": "approve", "note": "Agreed on appeal"}, headers=h(keys["payer"])
    )
    assert r.json()["milestone_status"] == "settled"
