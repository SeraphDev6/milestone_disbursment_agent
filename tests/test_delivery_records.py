"""Who saw what (#8) and the payee's record for a chargeback (#10)."""

from sqlmodel import select

from app.models import AuditEvent, LedgerEntry
from tests.helpers import h
from tests.test_escrow import fund, webhook
from tests.test_verified_delivery import setup, upload


def views(client, mid) -> list[dict]:
    with client.app.state.sessionmaker() as s:
        q = select(AuditEvent).where(AuditEvent.milestone_id == mid, AuditEvent.action == "evidence_viewed")
        return [e.detail | {"actor": e.actor} for e in s.exec(q)]


def captured_and_revealed(client, draft, **kw):
    aid, m2, ctx = setup(client, draft, **kw)
    fund(client, m2, ctx)
    eid = upload(client, m2, ctx)
    r = client.post(f"/milestones/{m2}/review", json={"decision": "approve"}, headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    return aid, m2, ctx, eid


def test_each_side_s_reads_are_recorded_filtered_or_full(client, draft):
    _, m2, ctx = setup(client, draft, reviewer=True)
    eid = upload(client, m2, ctx)
    for who in ("payer", "payee", "reviewer", "payer"):  # the second payer read within the hour isn't logged
        assert client.get(f"/milestones/{m2}/evidence", headers=h(ctx[who])).status_code == 200
    seen = {(v["side"], v["view"]) for v in views(client, m2)}
    assert seen == {("payer", "filtered"), ("payee", "full"), ("reviewer", "full")}
    assert len(views(client, m2)) == 3 and all(v["evidence_ids"] == [eid] for v in views(client, m2))


def test_reviewer_and_payee_see_what_the_payer_sees(client, draft):
    _, m2, ctx = setup(client, draft, reviewer=True)
    upload(client, m2, ctx)
    full = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["reviewer"])).json()
    shown = client.get(f"/milestones/{m2}/payer-view", headers=h(ctx["reviewer"])).json()
    assert full[0]["excerpt"] and not full[0]["withheld"]
    assert shown["withheld"] and shown["evidence"][0]["withheld"] and shown["evidence"][0]["excerpt"] is None
    assert shown == client.get(f"/milestones/{m2}/payer-view", headers=h(ctx["payee"])).json()
    payer_own = client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"])).json()
    assert payer_own == shown["evidence"]


def test_lint_suggests_reviewer_approval_when_the_payer_cant_see_the_work(client, draft):
    # By default the reviewer decides withheld tranches (#8), so there's nothing to warn about...
    aid, _, ctx = setup(client, draft, reviewer=True)
    codes = [i["code"] for i in client.get(f"/agreements/{aid}", headers=h(ctx["payer"])).json()["lint"]["issues"]]
    assert "payer_reviews_unseen_work" not in codes
    # ...unless that's turned off and nothing else routes review to the reviewer.
    aid, _, ctx = setup(client, draft, reviewer=True, reviewer_decides_withheld=False)
    codes = [i["code"] for i in client.get(f"/agreements/{aid}", headers=h(ctx["payer"])).json()["lint"]["issues"]]
    assert "payer_reviews_unseen_work" in codes
    aid, _, ctx = setup(client, draft, reviewer=True, reviewer_decides_withheld=False, require_reviewer_approval=True)
    codes = [i["code"] for i in client.get(f"/agreements/{aid}", headers=h(ctx["payer"])).json()["lint"]["issues"]]
    assert "payer_reviews_unseen_work" not in codes


def test_dispute_pack_has_the_commitment_reveal_and_payer_access(client, draft):
    _, m2, ctx, eid = captured_and_revealed(client, draft)
    client.get(f"/milestones/{m2}/evidence", headers=h(ctx["payer"]))
    client.get(f"/evidence/{eid}/file", headers=h(ctx["payer"]))

    assert client.get(f"/milestones/{m2}/dispute-pack", headers=h(ctx["payer"])).status_code == 403
    pack = client.get(f"/milestones/{m2}/dispute-pack", headers=h(ctx["payee"])).json()
    d = pack["dispute"]
    assert pack["kind"] == "Dispute pack" and [m["id"] for m in pack["milestones"]] == [m2]
    assert d["revealed_at"] and d["criteria_hash"] == pack["agreement"]["criteria_hash"]
    assert d["commitments"] and d["commitments"][0]["commitment"].startswith("sha256:")
    actions = [(x["action"], x["view"]) for x in d["payer_access"]]
    assert actions == [("evidence_viewed", "full"), ("deliverable_downloaded", "full")]
    assert all(p["confirmed_criteria_hash"] for p in pack["parties"])  # both sides' signatures
    pdf = client.get(f"/milestones/{m2}/dispute-pack?format=pdf", headers=h(ctx["payee"]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_a_chargeback_on_revealed_work_sends_the_payee_to_its_dispute_pack(client, draft):
    _, m2, ctx, _ = captured_and_revealed(client, draft)
    with client.app.state.sessionmaker() as s:
        capture = next(e.paypal_ref for e in s.exec(select(LedgerEntry).where(LedgerEntry.milestone_id == m2)))
    event = {
        "id": "WH-CB1",
        "event_type": "CUSTOMER.DISPUTE.CREATED",
        "resource": {
            "dispute_id": "PP-D-9",
            "reason": "MERCHANDISE_OR_SERVICE_NOT_AS_DESCRIBED",
            "status": "OPEN",
            "disputed_transactions": [{"seller_transaction_id": capture}],
        },
    }
    assert webhook(client, event).status_code == 200
    assert webhook(client, event | {"id": "WH-CB2"}).status_code == 200  # the same dispute again: one notice
    sent = [m for m in client.app.state.mailer.sent if "Payment disputed" in m.subject]
    assert len(sent) == 1 and "dispute pack" in sent[0].body
    pack = client.get(f"/milestones/{m2}/dispute-pack", headers=h(ctx["payee"])).json()
    assert [x["dispute_id"] for x in pack["dispute"]["paypal_disputes"]][:1] == ["PP-D-9"]
