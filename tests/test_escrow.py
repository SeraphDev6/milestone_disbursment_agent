from datetime import timedelta
from decimal import Decimal

import pytest

from app.db import MilestoneRow, utcnow
from app.paypal import MOCK_WEBHOOK_SIGNATURE, PayPalError


def h(key):
    return {"X-API-Key": key}


@pytest.fixture
def active(client, draft):
    """An active escrow agreement: created and confirmed by both parties."""
    r = client.post("/agreements", json=draft)
    body = r.json()
    a, keys = body["agreement"], {k["role"]: k["api_key"] for k in body["party_keys"]}
    for role in ("payer", "payee"):
        client.post(f"/agreements/{a['id']}/confirm", json={"criteria_hash": a["criteria_hash"]}, headers=h(keys[role]))
    return a["id"], [m["id"] for m in a["milestones"]], keys


def gw(client):
    return client.app.state.paypal


def fund(client, mid, keys):
    r = client.post(f"/milestones/{mid}/fund", headers=h(keys["payer"]))
    assert r.status_code == 200, r.text
    page = client.get(r.json()["approve_url"])  # mock approval page redirects to /paypal/return
    assert page.status_code == 200 and "Tranche funded" in page.text, page.text
    return r.json()["order_id"]


def agreement(client, aid, keys):
    return client.get(f"/agreements/{aid}", headers=h(keys["payer"])).json()


def verify(client, mid, keys):
    assert client.post(f"/mock/milestones/{mid}/mark-verified", headers=h(keys["payer"])).status_code == 200


def release(client, mid, keys, role="payer"):
    return client.post(f"/milestones/{mid}/release", headers=h(keys[role]))


def age_hold(client, mid, days):
    with client.app.state.sessionmaker() as s:
        m = s.get(MilestoneRow, mid)
        m.funded_at = utcnow() - timedelta(days=days)
        s.commit()


def webhook(client, event, sig=MOCK_WEBHOOK_SIGNATURE):
    return client.post("/webhooks/paypal", json=event, headers={"paypal-transmission-sig": sig})


def test_fund_verify_release_happy_path(client, active):
    aid, (m1, _), keys = active
    fund(client, m1, keys)
    a = agreement(client, aid, keys)
    assert a["milestones"][0]["status"] == "funded"
    assert a["funding"] == {"held": "10000.00", "invoiced": "0.00", "released": "0.00", "unfunded": "20000.00"}

    verify(client, m1, keys)
    r = release(client, m1, keys)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["status"] == "succeeded" and s["milestone_status"] == "settled" and s["idempotency_key"] == f"{m1}-1"

    a = agreement(client, aid, keys)
    assert a["funding"] == {"held": "0.00", "invoiced": "0.00", "released": "10000.00", "unfunded": "20000.00"}
    order = gw(client).orders[next(iter(gw(client).orders))]
    assert order["payee"] == "grantee@example.org"  # funds go straight to the payee


def test_cannot_fund_before_both_confirm(client, draft):
    body = client.post("/agreements", json=draft).json()
    keys = {k["role"]: k["api_key"] for k in body["party_keys"]}
    mid = body["agreement"]["milestones"][0]["id"]
    r = client.post(f"/milestones/{mid}/fund", headers=h(keys["payer"]))
    assert r.status_code == 409 and "confirm" in r.json()["detail"]


def test_release_requires_verified_and_funded(client, active):
    _, (m1, m2), keys = active
    fund(client, m1, keys)
    assert release(client, m1, keys).status_code == 409  # funded but not verified
    verify(client, m2, keys)
    r = release(client, m2, keys)  # verified but never funded
    assert r.status_code == 409 and "fund" in r.json()["detail"]


def test_roles(client, active):
    _, (m1, _), keys = active
    assert client.post(f"/milestones/{m1}/fund", headers=h(keys["payee"])).status_code == 403
    fund(client, m1, keys)
    verify(client, m1, keys)
    assert release(client, m1, keys, role="payee").status_code == 403


def test_reauthorizes_after_honor_period(client, active):
    _, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    age_hold(client, m1, 5)
    assert release(client, m1, keys).json()["status"] == "succeeded"
    auths = gw(client).authorizations
    assert any("reauthorized_from" in a for a in auths.values())


def test_hold_expires_after_29_days(client, active):
    aid, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    age_hold(client, m1, 30)
    r = release(client, m1, keys)
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    a = agreement(client, aid, keys)
    assert a["milestones"][0]["status"] == "verified"  # verification kept, hold gone
    assert a["funding"]["held"] == "0.00"
    fund(client, m1, keys)  # payer funds again; release now works
    assert release(client, m1, keys).json()["status"] == "succeeded"


def test_expiry_job(client, active):
    from app.escrow import expire_stale_holds

    _, (m1, m2), keys = active
    fund(client, m1, keys)
    fund(client, m2, keys)
    age_hold(client, m1, 30)
    with client.app.state.sessionmaker() as s:
        assert expire_stale_holds(s, utcnow()) == 1
        assert s.get(MilestoneRow, m1).status == "pending"
        assert s.get(MilestoneRow, m2).status == "funded"


def test_ambiguous_failure_resumes_with_same_key(client, active):
    aid, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).drop_next_response = True  # PayPal captured, but we never heard back
    r = release(client, m1, keys)
    assert r.status_code == 502 and "same idempotency key" in r.json()["detail"]
    r = release(client, m1, keys)
    assert r.json()["status"] == "succeeded" and r.json()["idempotency_key"] == f"{m1}-1"
    assert agreement(client, aid, keys)["funding"]["released"] == "10000.00"  # paid once


def test_definitive_failure_opens_new_attempt(client, active):
    _, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).fail_next = PayPalError("INSTRUMENT_DECLINED", status=422)
    r = release(client, m1, keys)
    assert r.status_code == 502
    r = release(client, m1, keys)
    assert r.json()["status"] == "succeeded" and r.json()["idempotency_key"] == f"{m1}-2"


def test_declined_capture_then_retry(client, active):
    _, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).next_capture_status = "DECLINED"
    assert release(client, m1, keys).json()["milestone_status"] == "settlement_failed"


def test_pending_capture_settled_by_webhook_once(client, active):
    aid, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).next_capture_status = "PENDING"
    s = release(client, m1, keys).json()
    assert s["milestone_status"] == "settling"
    assert release(client, m1, keys).status_code == 409  # waiting on PayPal

    event = {"id": "WH-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": s["paypal_ref"]}}
    assert webhook(client, event).json() == {"outcome": "capture completed"}
    assert webhook(client, event).json() == {"outcome": "duplicate"}
    a = agreement(client, aid, keys)
    assert a["milestones"][0]["status"] == "settled" and a["funding"]["released"] == "10000.00"


def test_lost_capture_response_matched_by_authorization(client, active):
    _, (m1, _), keys = active
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).drop_next_response = True
    release(client, m1, keys)
    with client.app.state.sessionmaker() as s:
        auth_id = s.get(MilestoneRow, m1).paypal_authorization_id
    event = {
        "id": "WH-2",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAP-X", "supplementary_data": {"related_ids": {"authorization_id": auth_id}}},
    }
    assert webhook(client, event).json()["outcome"] == "capture completed"


def test_webhook_signature_required(client):
    r = webhook(client, {"id": "WH-3", "event_type": "PAYMENT.CAPTURE.COMPLETED"}, sig="forged")
    assert r.status_code == 401


def test_void_returns_to_pending(client, active):
    _, (m1, _), keys = active
    fund(client, m1, keys)
    r = client.post(f"/milestones/{m1}/void", headers=h(keys["payer"]))
    assert r.status_code == 200
    a = r.json()
    assert a["milestones"][0]["status"] == "pending" and a["funding"]["held"] == "0.00"
    verify(client, m1, keys)
    assert client.post(f"/milestones/{m1}/void", headers=h(keys["payer"])).status_code == 409


def test_return_url_is_idempotent(client, active):
    _, (m1, _), keys = active
    order_id = fund(client, m1, keys)
    again = client.get(f"/paypal/return?token={order_id}")
    assert again.status_code == 200
    with client.app.state.sessionmaker() as s:
        from sqlalchemy import func, select

        from app.db import LedgerRow

        assert s.scalar(select(func.count()).select_from(LedgerRow).where(LedgerRow.kind == "authorized")) == 1


def test_amounts_are_decimal_strings(client, active):
    aid, _, keys = active
    assert Decimal(agreement(client, aid, keys)["funding"]["unfunded"]) == Decimal("30000.00")
