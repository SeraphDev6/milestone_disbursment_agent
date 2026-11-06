"""Reconciliation (#25): PayPal read back against our ledger; disagreements become audit events, once."""

from app.db import utcnow
from app.integrations.paypal import PayPalError
from app.services.reconciliation import reconcile
from tests.helpers import create_active, h
from tests.test_escrow import fund, gw, release, verify
from tests.test_payouts import run_payouts


def run(client):
    with client.app.state.sessionmaker() as s:
        return reconcile(s, gw(client), utcnow())


def mismatches(client, aid, keys):
    audit = client.get(f"/agreements/{aid}/audit", headers=h(keys["payer"])).json()
    return [e["detail"] for e in audit if e["action"] == "reconciliation_mismatch"]


def test_a_clean_flow_reconciles_and_a_hold_voided_behind_our_back_is_reported_once(client, draft):
    aid, (m1, _), keys = create_active(client, draft)
    fund(client, m1, keys)
    r = run(client)
    assert r.checked == 1 and not r.mismatches and r.errors == 0  # the live hold is CREATED, as we say

    a = client.get(f"/agreements/{aid}", headers=h(keys["payer"])).json()
    auth_id = a["milestones"][0]["paypal_authorization_id"]
    gw(client).authorizations[auth_id]["status"] = "VOIDED"  # someone voided it in PayPal's dashboard
    r = run(client)
    assert [m.as_dict() for m in r.mismatches] == [
        {
            "agreement_id": aid,
            "milestone_id": m1,
            "object": "authorization",
            "paypal_ref": auth_id,
            "ours": "hold (funded)",
            "paypal": "VOIDED",
        }
    ]
    run(client)  # the next day
    assert len(mismatches(client, aid, keys)) == 1  # reported once until it changes


def test_captures_and_payouts_are_compared_and_paypal_outages_are_counted(client, draft):
    aid, (m1, _), keys = create_active(client, draft, auto_release=True)
    fund(client, m1, keys)
    verify(client, m1, keys)
    assert release(client, m1, keys).status_code == 200
    run_payouts(client, utcnow())
    r = run(client)
    assert r.checked >= 2 and not r.mismatches  # the capture is COMPLETED and the payout SUCCESS, as we say

    ((capture_id, capture),) = gw(client).captures.items()
    capture["status"] = "DECLINED"  # PayPal now disagrees with our settled capture
    ((batch_id, payout),) = gw(client).payouts.items()
    payout["status"] = "FAILED"
    r = run(client)
    assert {(m.object, m.paypal_ref, m.paypal) for m in r.mismatches} == {
        ("capture", capture_id, "DECLINED"),
        ("payout", batch_id, "FAILED"),
    }
    assert len(mismatches(client, aid, keys)) == 2

    gw(client).fail_next = PayPalError("PayPal 503", status=503, ambiguous=True)
    r = run(client)
    assert r.errors >= 1  # unreachable: counted, nothing recorded, tried again next run


def test_the_reconcile_summary_reads_as_a_report(client, draft):
    from app.jobs import _summary

    _aid, (m1, _), keys = create_active(client, draft)
    fund(client, m1, keys)
    report = run(client)
    assert _summary(report).startswith("reconciled 1 PayPal object(s): 0 mismatch(es), 0 unreachable, 0 unchecked")
