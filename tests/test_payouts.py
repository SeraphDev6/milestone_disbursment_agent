"""Paying the payee: connected PayPal (partner onboarding) vs the Payouts fallback, the dispute window, contests,
refunds, and acting for the payee on PayPal (PayPal-Auth-Assertion)."""

import base64
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.db import utcnow
from app.integrations.paypal import PayPalError, SandboxPayPal
from app.models import Disbursement, Milestone
from app.services.settlement import disburse_due
from tests.helpers import connect_paypal, create_active, h
from tests.test_escrow import agreement, auth_id, fund, funding, gw, release, verify, webhook
from tests.test_orgs import active_with_reviewer


def tranche(client, mid) -> Milestone:
    with client.app.state.sessionmaker() as s:
        m = s.get(Milestone, mid)
        s.expunge(m)
        return m


def run_payouts(client, at):
    with client.app.state.sessionmaker() as s:
        return disburse_due(s, gw(client), at)


def captured(client, aid, keys, mid):
    fund(client, mid, keys)
    verify(client, mid, keys)
    r = release(client, mid, keys)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def windowed(client, draft):
    """Escrow with a 3-day dispute window; the payee connected PayPal."""
    aid, mids, ctx = create_active(client, {**draft, "dispute_window_days": 3})
    connect_paypal(client, ctx)
    return aid, mids, ctx


# --- connecting PayPal -----------------------------------------------------------------


def test_connect_paypal(client, draft):
    aid, _, ctx = create_active(client, draft)
    before = client.get(f"/orgs/{ctx['payee_org']}/paypal", headers=h(ctx["payee"])).json()
    assert before["ready"] is False and before["issues"] == ["PayPal isn't connected"]
    payee = next(p for p in agreement(client, aid, ctx)["parties"] if p["role"] == "payee")
    assert payee["paypal_connected"] is False

    status = connect_paypal(client, ctx)
    assert status["ready"] and status["merchant_id"] and status["issues"] == []
    parties = {p["role"]: p["paypal_connected"] for p in agreement(client, aid, ctx)["parties"]}
    assert parties == {"payer": None, "payee": True}
    r = client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    assert r.status_code == 409 and "already connected" in r.json()["detail"]


def test_only_org_admins_connect(client, draft):
    _, _, ctx = create_active(client, draft)
    assert client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payer"])).status_code == 404


def test_unready_account_is_explained_and_falls_back_to_payouts(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    r = client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    tracking = parse_qs(urlsplit(r.json()["onboarding_url"]).query)["tracking_id"][0]
    gw(client).complete_onboarding(tracking, primary_email_confirmed=False)
    page = client.get(f"/paypal/onboarded?merchantId={tracking}")
    assert page.status_code == 409 and "email isn" in page.text
    assert client.get(f"/orgs/{ctx['payee_org']}/paypal", headers=h(ctx["payee"])).json()["ready"] is False

    captured(client, aid, ctx, m1)
    assert tranche(client, m1).payout_route == "platform"


def test_return_url_is_not_trusted(client, draft):
    _, _, _ctx = create_active(client, draft)
    page = client.get("/paypal/onboarded?merchantId=someone-elses-tracking-id&merchantIdInPayPal=X")
    assert page.status_code == 404


def test_onboarding_webhook_then_status_read_at_funding(client, draft):
    _aid, (m1, _), ctx = create_active(client, draft)
    r = client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    tracking = parse_qs(urlsplit(r.json()["onboarding_url"]).query)["tracking_id"][0]
    mid = gw(client).complete_onboarding(tracking)  # payee finished in PayPal but closed the tab
    event = {
        "id": "WH-ONB",
        "event_type": "MERCHANT.ONBOARDING.COMPLETED",
        "resource": {"merchant_id": mid, "tracking_id": tracking},
    }
    assert webhook(client, event).json() == {"outcome": "onboarding completed"}
    fund(client, m1, ctx)  # funding reads the account's status from PayPal before choosing the route
    assert tranche(client, m1).payout_route == "direct"


def test_consent_revoked(client, draft):
    _aid, (m1, _m2), ctx = create_active(client, draft)
    mid = connect_paypal(client, ctx)["merchant_id"]
    event = {"id": "WH-REV", "event_type": "MERCHANT.PARTNER-CONSENT.REVOKED", "resource": {"merchant_id": mid}}
    assert webhook(client, event).json() == {"outcome": "consent revoked"}
    status = client.get(f"/orgs/{ctx['payee_org']}/paypal", headers=h(ctx["payee"])).json()
    assert status["ready"] is False and "revoked" in status["issues"][0]
    fund(client, m1, ctx)
    assert tranche(client, m1).payout_route == "platform"


# --- payout routes ---------------------------------------------------------------------


def test_direct_route_pays_at_capture_without_a_window(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    connect_paypal(client, ctx)
    s = captured(client, aid, ctx, m1)
    assert s["milestone_status"] == "settled"
    [cap] = gw(client).captures.values()
    assert cap["disbursed"] and not cap["delayed"] and not gw(client).payouts  # one capture, no payout


def test_platform_route_pays_with_payouts(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)  # payee never connects
    s = captured(client, aid, ctx, m1)
    assert s["milestone_status"] == "settled"
    [order] = gw(client).orders.values()
    assert order["payee"] is None  # the platform account receives the capture
    [payout] = gw(client).payouts.values()
    assert payout["email"] == "grantee@example.org" and payout["sender_item_id"] == f"{m1}-payout-1"
    a = agreement(client, aid, ctx)
    assert a["funding"] == funding(released="10000.00")


def test_unclaimed_payout_settles_when_claimed(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    gw(client).next_payout_status = "UNCLAIMED"
    assert captured(client, aid, ctx, m1)["milestone_status"] == "held"
    event = {
        "id": "WH-PO",
        "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
        "resource": {"payout_batch_id": "B-1", "payout_item": {"sender_item_id": f"{m1}-payout-1"}},
    }
    assert webhook(client, event).json() == {"outcome": "payout success"}
    assert tranche(client, m1).status == "settled"


def test_failed_payout_is_retried(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    gw(client).next_payout_status = "FAILED"
    assert captured(client, aid, ctx, m1)["milestone_status"] == "payout_failed"
    assert agreement(client, aid, ctx)["funding"] == funding(window="10000.00")  # captured, not yet paid
    gw(client).next_payout_status = "SUCCESS"
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.status_code == 200 and r.json()["attempt"] == 2 and r.json()["milestone_status"] == "settled"


def test_lost_payout_response_resumes_with_same_key(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    gw(client).drop_next_response = True
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.status_code == 502
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.json()["idempotency_key"] == f"{m1}-payout-1" and r.json()["milestone_status"] == "settled"
    assert agreement(client, aid, ctx)["funding"]["released"] == "10000.00"


def test_reused_payouts_batch_id_waits_for_webhook(client, draft):
    """Payouts answers a replayed sender_batch_id with an error; the webhook (matched by our key) settles it."""
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})
    captured(client, aid, ctx, m1)
    gw(client).drop_next_response = True
    assert client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"])).status_code == 502
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    event = {
        "id": "WH-PO2",
        "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
        "resource": {"payout_batch_id": "B-2", "payout_item": {"sender_item_id": f"{m1}-payout-1"}},
    }
    assert webhook(client, event).json() == {"outcome": "payout success"}
    assert tranche(client, m1).status == "settled" and len(gw(client).payouts) == 1


# --- dispute window --------------------------------------------------------------------


def test_window_holds_the_money_then_pays(client, windowed):
    aid, (m1, _), ctx = windowed
    s = captured(client, aid, ctx, m1)
    assert s["milestone_status"] == "held"
    [cap] = gw(client).captures.values()
    assert cap["delayed"] and not cap["disbursed"]  # PayPal holds it for us; the payee's balance waits
    m = tranche(client, m1)
    assert m.release_due_at - m.captured_at == timedelta(days=3)
    assert agreement(client, aid, ctx)["funding"] == funding(window="10000.00")

    assert run_payouts(client, utcnow())["paid_out"] == 0
    assert run_payouts(client, utcnow() + timedelta(days=4))["paid_out"] == 1
    assert tranche(client, m1).status == "settled" and cap["disbursed"]
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_payer_can_pay_now(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    assert client.post(f"/milestones/{m1}/payout", headers=h(ctx["payee"])).status_code == 403
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.json()["kind"] == "referenced_payout" and r.json()["milestone_status"] == "settled"


def test_contest_then_payer_withdraws(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    assert client.post(f"/milestones/{m1}/contest", json={"reason": "x"}, headers=h(ctx["payee"])).status_code == 403
    r = client.post(f"/milestones/{m1}/contest", json={"reason": "photos don't match"}, headers=h(ctx["payer"]))
    assert r.json()["milestones"][0]["status"] == "contested"
    assert run_payouts(client, utcnow() + timedelta(days=4))["paid_out"] == 0  # window closed, still paused

    body = {"decision": "release", "note": "resolved by phone"}
    assert client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["payee"])).status_code == 403
    r = client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["payer"]))
    assert r.json()["milestone_status"] == "settled"


def test_contest_then_payee_accepts_refund(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    client.post(f"/milestones/{m1}/contest", json={"reason": "work not delivered"}, headers=h(ctx["payer"]))
    body = {"decision": "refund"}
    assert client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["payer"])).status_code == 403
    r = client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["payee"]))
    assert r.json()["kind"] == "refund" and r.json()["milestone_status"] == "refunded"
    [cap] = gw(client).captures.values()
    assert cap["refunded"] and not cap["disbursed"]
    assert agreement(client, aid, ctx)["funding"] == funding(refunded="10000.00", unfunded="30000.00")


def test_reviewer_decides_a_contest(client, draft):
    aid, (m1, _), ctx = active_with_reviewer(client, draft, dispute_window_days=2)
    connect_paypal(client, ctx)
    captured(client, aid, ctx, m1)
    rv = h(ctx["reviewer"])
    assert client.post(f"/milestones/{m1}/contest", json={"reason": "audit hold"}, headers=rv).status_code == 200
    [item] = client.get(f"/orgs/{ctx['reviewer_org']}/review-queue", headers=rv).json()
    assert item["status"] == "contested"
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=rv)
    assert r.json()["milestone_status"] == "refunded"


def test_contest_only_inside_the_window(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    with client.app.state.sessionmaker() as s:
        s.get(Milestone, m1).release_due_at = utcnow() - timedelta(minutes=1)
        s.commit()
    r = client.post(f"/milestones/{m1}/contest", json={"reason": "late"}, headers=h(ctx["payer"]))
    assert r.status_code == 409 and "closed" in r.json()["detail"]


def test_paypal_releases_contested_direct_capture_after_28_days(client, windowed):
    aid, (m1, _), ctx = windowed
    captured(client, aid, ctx, m1)
    client.post(f"/milestones/{m1}/contest", json={"reason": "unresolved"}, headers=h(ctx["payer"]))
    assert run_payouts(client, utcnow() + timedelta(days=27))["auto_released"] == 0
    assert run_payouts(client, utcnow() + timedelta(days=29))["auto_released"] == 1
    assert tranche(client, m1).status == "settled"
    assert agreement(client, aid, ctx)["funding"]["released"] == "10000.00"


# --- acting for the connected payee on PayPal ------------------------------------------


def test_mock_refuses_void_without_acting_for_payee(client, draft):
    """Mirrors the sandbox: PayPal returned PERMISSION_DENIED for a void on a connected payee's hold."""
    _aid, (m1, _), ctx = create_active(client, draft)
    connect_paypal(client, ctx)
    fund(client, m1, ctx)
    with pytest.raises(PayPalError, match="PERMISSION_DENIED"):
        gw(client).void_authorization(auth_id(client, m1), payee_merchant_id=None, request_id="x")
    assert client.post(f"/milestones/{m1}/void", headers=h(ctx["payer"])).status_code == 200


def test_sandbox_client_acts_for_the_payee():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(201, json={"id": "X", "status": "COMPLETED"})

    gw_ = SandboxPayPal(client_id="client-123", client_secret="s", base_url="https://paypal.test", webhook_id=None)
    gw_._client = httpx.Client(base_url="https://paypal.test", transport=httpx.MockTransport(handler))
    from decimal import Decimal

    gw_.void_authorization("A1", payee_merchant_id="MERCHANT9", request_id="r1")
    gw_.capture_authorization(
        "A1", amount=Decimal(5), currency="USD", payee_merchant_id="MERCHANT9", delayed=True, request_id="r2"
    )
    gw_.void_authorization("A2", payee_merchant_id=None, request_id="r3")  # platform's own hold

    void, capture, own = seen[1], seen[2], seen[3]
    header, claims, signature = void.headers["PayPal-Auth-Assertion"].split(".")
    pad = lambda part: part + "=" * (-len(part) % 4)
    assert json.loads(base64.urlsafe_b64decode(pad(header))) == {"alg": "none"} and signature == ""
    assert json.loads(base64.urlsafe_b64decode(pad(claims))) == {"iss": "client-123", "payer_id": "MERCHANT9"}
    assert json.loads(capture.content)["payment_instruction"] == {"disbursement_mode": "DELAYED"}
    assert "PayPal-Auth-Assertion" in capture.headers and "PayPal-Auth-Assertion" not in own.headers


def test_disbursement_rows_record_every_attempt(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    gw(client).next_payout_status = "FAILED"
    captured(client, aid, ctx, m1)
    gw(client).next_payout_status = "SUCCESS"
    client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    with client.app.state.sessionmaker() as s:
        from sqlmodel import select

        rows = s.exec(select(Disbursement).where(Disbursement.milestone_id == m1)).all()
        assert [(d.attempt, d.status) for d in rows] == [(1, "failed"), (2, "succeeded")]


# --- invoice mode with a connected payee: pay-now link ---------------------------------


@pytest.fixture
def pay_link_agreement(client, draft):
    from tests.test_reporting import complete_tranche_1

    aid, (m1, m2), ctx = create_active(client, {**draft, "settlement_mode": "invoice_on_verify"}, auto_release=True)
    connect_paypal(client, ctx)
    complete_tranche_1(client, m1, ctx)  # verifies and auto-requests payment
    return aid, (m1, m2), ctx


def open_pay_link(client, aid, ctx) -> str:
    path = agreement(client, aid, ctx)["milestones"][0]["pay_path"]
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"]  # PayPal approval page (mock)


def test_pay_link_pays_the_payee_directly(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    a = agreement(client, aid, ctx)
    assert a["milestones"][0]["status"] == "settling" and a["funding"]["invoiced"] == "10000.00"
    assert not gw(client).invoices  # no PayPal invoice: PayPal won't let us invoice as the payee

    page = client.get(open_pay_link(client, aid, ctx))  # payer approves; PayPal returns to /paypal/paid
    assert page.status_code == 200 and "Paid" in page.text
    a = agreement(client, aid, ctx)
    assert a["milestones"][0]["status"] == "settled" and a["milestones"][0]["pay_path"] is None
    assert a["funding"] == funding(released="10000.00")
    [order] = gw(client).orders.values()
    assert order["intent"] == "CAPTURE" and order["payee"].startswith("MOCKMERCHANT") and not gw(client).payouts

    again = client.get(open_path(client, m1), follow_redirects=False)
    assert again.status_code == 409 and "already paid" in again.text


def settlement_id(client, mid) -> str:
    with client.app.state.sessionmaker() as s:
        from sqlmodel import select

        from app.models import Settlement

        return s.exec(select(Settlement).where(Settlement.milestone_id == mid)).one().id


def open_path(client, mid, at=None) -> str:
    """A signed pay link for the tranche's settlement, as the app would issue it at `at`."""
    return client.app.state.pay_links.path(settlement_id(client, mid), at or utcnow())


def test_pay_links_are_signed_and_expire(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    sid = settlement_id(client, m1)
    # Unsigned, tampered and foreign-signature links say nothing about the request.
    for path in (f"/pay/{sid}", f"/pay/{sid}?t=junk", f"/pay/{sid}?t=9999999999.{'0' * 32}"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 404 and "is not valid" in r.text, path
    # The signature is bound to the settlement id.
    other = client.app.state.pay_links.path("some-other-settlement", utcnow()).split("?t=")[1]
    assert client.get(f"/pay/{sid}?t={other}", follow_redirects=False).status_code == 404
    # A link issued 8 days ago has expired (links last pay_link_valid_days, 7 by default)...
    r = client.get(open_path(client, m1, utcnow() - timedelta(days=8)), follow_redirects=False)
    assert r.status_code == 410 and "expired" in r.text
    # ...while the one the payer reads from the tranche is fresh and the audit log never carries a signed link.
    assert client.get(open_pay_link(client, aid, ctx)).status_code == 200
    audit = client.get(f"/agreements/{aid}/audit", headers=h(ctx["payer"])).json()
    requested = next(e for e in audit if e["action"] == "payment_requested")
    assert requested["detail"]["pay_path"] == f"/pay/{sid}"
    assert all("?t=" not in json.dumps(e.get("detail") or {}) for e in audit)


def test_payer_side_can_reissue_a_pay_link_and_it_is_emailed(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    before = [m for m in client.app.state.mailer.sent if "Payment due" in m.subject]
    r = client.post(f"/milestones/{m1}/pay-link", headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    assert "?t=" in r.json()["pay_url"]
    [mail] = [m for m in client.app.state.mailer.sent if "Payment due" in m.subject][len(before) :]
    assert mail.link == r.json()["pay_url"]
    path = r.json()["pay_url"].replace(client.app.state.settings.api_base_url, "")
    assert client.get(path, follow_redirects=False).status_code == 303
    # The payee can't send the payer links, and a paid tranche has nothing to reissue.
    assert client.post(f"/milestones/{m1}/pay-link", headers=h(ctx["payee"])).status_code == 403
    client.get(open_pay_link(client, aid, ctx))
    assert client.post(f"/milestones/{m1}/pay-link", headers=h(ctx["payer"])).status_code == 409


def test_only_the_latest_order_completes_payment(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    first = open_pay_link(client, aid, ctx)
    second = open_pay_link(client, aid, ctx)
    page = client.get(first)
    assert page.status_code == 404 and "superseded" in page.text
    assert client.get(second).status_code == 200
    assert tranche(client, m1).status == "settled"


def test_release_points_at_the_pay_link(client, pay_link_agreement):
    _aid, (m1, _), ctx = pay_link_agreement
    r = client.post(f"/milestones/{m1}/release", headers=h(ctx["payer"]))
    assert r.status_code == 409 and "/pay/" in r.json()["detail"]


def test_pending_payment_settles_by_webhook(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    url = open_pay_link(client, aid, ctx)
    order_id = parse_qs(urlsplit(url).query)["token"][0]
    gw(client).next_capture_status = "PENDING"
    assert "processing" in client.get(url).text
    event = {
        "id": "WH-PAY",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-9", "supplementary_data": {"related_ids": {"order_id": order_id}}},
    }
    assert webhook(client, event).json() == {"outcome": "invoice paid"}
    assert tranche(client, m1).status == "settled"


def test_declined_payment_can_be_retried(client, pay_link_agreement):
    aid, (m1, _), ctx = pay_link_agreement
    gw(client).next_capture_status = "DECLINED"
    page = client.get(open_pay_link(client, aid, ctx))
    assert page.status_code == 402 and "declined" in page.text
    gw(client).next_capture_status = "COMPLETED"
    assert client.get(open_pay_link(client, aid, ctx)).status_code == 200
    assert tranche(client, m1).status == "settled"


def test_unpaid_pay_link_expires_without_calling_paypal(client, pay_link_agreement):
    from app.services.settlement import expire_stale_invoices

    aid, (m1, _), ctx = pay_link_agreement
    with client.app.state.sessionmaker() as s:
        assert expire_stale_invoices(s, gw(client), utcnow() + timedelta(days=31)) == 1
    assert tranche(client, m1).status == "settlement_failed"
    r = client.get(open_path(client, m1))
    assert r.status_code == 410
    assert agreement(client, aid, ctx)["funding"]["invoiced"] == "0.00"
