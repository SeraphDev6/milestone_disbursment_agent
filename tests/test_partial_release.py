"""Partial release (#46): with `partial_release` on, a tranche pays the share its met criteria account for by weight,
the hold keeps the remainder (final_capture=false), and the tranche settles with a final capture once everything is
met. Money is tracked per capture: every settlement and disbursement carries its own amount."""

import copy
from decimal import Decimal

import pytest

from app.db import utcnow
from app.domain.criteria import share_amount
from app.integrations.paypal import MockPayPal, PayPalError
from app.models import Disbursement, Milestone, Settlement
from app.services.settlement import expire_stale_holds
from tests.conftest import GOOD_DRAFT
from tests.helpers import connect_paypal, create_active, create_draft, h
from tests.test_escrow import age_hold, agreement, fund, funding, gw, webhook
from tests.test_payouts import run_payouts
from tests.test_verification import BUDGET, SIGN_IN, submit


def weighted(**extra) -> dict:
    """GOOD_DRAFT with partial release on; tranche 1's attendance criterion weighs three times its budget check."""
    d = copy.deepcopy(GOOD_DRAFT) | {"partial_release": True} | extra
    d["milestones"][0]["criteria"][0]["weight"] = "3"
    return d


@pytest.fixture
def partial(client):
    """Active, partial release on, payee connected (direct route), no dispute window, auto-release off."""
    aid, mids, ctx = create_active(client, weighted())
    connect_paypal(client, ctx)
    return aid, mids, ctx


def review(client, mid, ctx, decision, role="payer"):
    return client.post(f"/milestones/{mid}/review", json={"decision": decision}, headers=h(ctx[role]))


def tranche(client, aid, ctx, index=0) -> dict:
    return agreement(client, aid, ctx)["milestones"][index]


def rows(client, model, mid):
    with client.app.state.sessionmaker() as s:
        out = s.exec(__import__("sqlmodel").select(model).where(model.milestone_id == mid)).all()
        for r in out:
            s.expunge(r)
        return out


def ledger(client, aid, ctx) -> list[tuple[str, str]]:
    view = client.get(f"/agreements/{aid}/ledger", headers=h(ctx["payer"])).json()
    return [(e["kind"], e["amount"]) for e in view["entries"]]


# --- the share -------------------------------------------------------------------------


def test_share_amount_rounds_down_and_the_final_capture_pays_the_rest():
    from app.models import Criterion

    cs = [
        Criterion.model_validate({"id": f"c{i}", **GOOD_DRAFT["milestones"][0]["criteria"][0], "weight": 1})
        for i in range(1, 4)
    ]
    hundred = Decimal("100.00")
    assert share_amount(cs, {"c1"}, hundred) == Decimal("33.33")
    assert share_amount(cs, {"c1", "c2"}, hundred) == Decimal("66.66")
    assert share_amount(cs, {"c1", "c2", "c3"}, hundred) == hundred  # the remainder, 33.34, settles it to the cent
    assert share_amount(cs, set(), hundred) == Decimal("0.00")


def test_partial_release_is_signed_and_linted(client):
    aid, _, ctx = create_draft(client, weighted())
    plain, _, ctx2 = create_draft(client, copy.deepcopy(GOOD_DRAFT), payer_email="other@example.org")
    a, b = agreement(client, aid, ctx), agreement(client, plain, ctx2)
    assert a["partial_release"] and not b["partial_release"]
    assert a["criteria_hash"] != b["criteria_hash"]  # both parties sign the setting
    codes = {i["code"] for i in a["lint"]["issues"]}
    assert "weights_not_paid" not in codes  # weights differ, but partial release pays them

    windowed, _, ctx3 = create_draft(client, weighted(dispute_window_days=3), payer_email="w@example.org")
    assert "partial_shares_skip_window" in {i["code"] for i in agreement(client, windowed, ctx3)["lint"]["issues"]}
    invoiced, _, ctx4 = create_draft(client, weighted(settlement_mode="invoice_on_verify"), payer_email="i@example.org")
    lint = agreement(client, invoiced, ctx4)["lint"]
    assert not lint["ok"] and "partial_release_needs_escrow" in {i["code"] for i in lint["issues"]}


# --- paying a share, then the rest ------------------------------------------------------


def test_payer_pays_the_met_share_then_the_rest_settles(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    assert tranche(client, aid, ctx)["status"] == "needs_review"  # c1 met, c2 (budget) still missing

    assert review(client, m1, ctx, "release_met", role="payee").status_code == 403
    r = review(client, m1, ctx, "release_met")
    assert r.status_code == 200, r.text
    assert r.json()["milestone_status"] == "funded" and [x["criterion_id"] for x in r.json()["missing"]] == ["c2"]

    t = tranche(client, aid, ctx)
    assert t["amount_released"] == "7500.00" and t["criteria_paid"] == ["c1"]
    assert t["paypal_authorization_id"]  # the hold is still live for the remainder
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")
    assert ledger(client, aid, ctx) == [("authorized", "10000.00"), ("captured", "7500.00"), ("disbursed", "7500.00")]
    [auth] = gw(client).authorizations.values()
    assert auth["status"] == "PARTIALLY_CAPTURED" and auth["captured"] == Decimal("7500.00")
    [cap] = gw(client).captures.values()
    assert cap["amount"] == Decimal("7500.00") and cap["disbursed"] and not cap["delayed"]
    assert any("Share released" in m.subject and "7500.00" in m.body for m in client.app.state.mailer.sent)

    # The same verdict can't be paid twice.
    assert review(client, m1, ctx, "release_met").status_code == 409

    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert tranche(client, aid, ctx)["status"] == "needs_review"  # everything met; auto-release is off
    assert review(client, m1, ctx, "approve").json()["milestone_status"] == "settled"
    t = tranche(client, aid, ctx)
    assert t["amount_released"] == "10000.00" and t["criteria_paid"] == ["c1", "c2"]
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")
    settlements = rows(client, Settlement, m1)
    assert [(s.amount, s.final, s.criterion_ids) for s in settlements] == [
        (Decimal("7500.00"), False, ["c1"]),
        (Decimal("2500.00"), True, ["c2"]),
    ]
    assert (
        gw(client).authorizations[t["paypal_authorization_id"] or next(iter(gw(client).authorizations))]["status"]
        == "CAPTURED"
    )


def test_auto_release_pays_each_share_as_it_is_met(client):
    aid, (m1, _), ctx = create_active(client, weighted(), auto_release=True)
    connect_paypal(client, ctx)
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    t = tranche(client, aid, ctx)
    assert t["status"] == "funded" and t["amount_released"] == "7500.00"
    audit = client.get(f"/milestones/{m1}/audit", headers=h(ctx["payer"])).json()
    assert [e["action"] for e in audit if e["action"] in ("share_verified", "partially_released")] == [
        "share_verified",
        "partially_released",
    ]
    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert tranche(client, aid, ctx)["status"] == "settled"
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_auto_release_waits_for_funding_before_paying_a_share(client):
    aid, (m1, _), ctx = create_active(client, weighted(), auto_release=True)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    assert tranche(client, aid, ctx)["status"] == "needs_review"
    assert any("waiting for funding" in m.subject for m in client.app.state.mailer.sent)
    fund(client, m1, ctx)
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    assert tranche(client, aid, ctx)["amount_released"] == "7500.00"


def test_release_met_needs_partial_release_on(client, draft):
    aid, (m1, _), ctx = create_active(client, draft)
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    r = review(client, m1, ctx, "release_met")
    assert r.status_code == 409 and "partial release is off" in r.json()["detail"]
    assert tranche(client, aid, ctx)["status"] == "needs_review"


def test_release_met_on_a_fully_met_verdict_is_the_final_capture(client, partial):
    _aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "settled"
    [s] = rows(client, Settlement, m1)
    assert s.final and s.amount == Decimal("10000.00") and s.criterion_ids == ["c1", "c2"]


# --- the platform route and PayPal's asynchronous outcomes ----------------------------


def test_platform_route_pays_a_share_with_payouts_and_retries_a_failure(client):
    aid, (m1, _), ctx = create_active(client, weighted())  # payee never connects PayPal
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    gw(client).next_payout_status = "FAILED"
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    t = tranche(client, aid, ctx)
    assert t["status"] == "funded" and t["amount_released"] == "7500.00"  # captured into the platform account
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", window="7500.00")
    audit = client.get(f"/milestones/{m1}/audit", headers=h(ctx["payer"])).json()
    assert "share_payout_failed" in {e["action"] for e in audit}
    assert any("Payout failed" in m.subject and "7500.00" in m.body for m in client.app.state.mailer.sent)

    gw(client).next_payout_status = "SUCCESS"
    r = client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    assert r.json()["attempt"] == 2 and r.json()["amount"] == "7500.00" and r.json()["milestone_status"] == "funded"
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")
    assert client.post(f"/milestones/{m1}/payout", headers=h(ctx["payer"])).status_code == 409  # paid already
    [cap] = rows(client, Settlement, m1)
    assert all(d.settlement_id == cap.id and d.amount == Decimal("7500.00") for d in rows(client, Disbursement, m1))
    [payout] = (p for p in gw(client).payouts.values() if p["status"] == "SUCCESS")
    assert payout["amount"] == Decimal("7500.00")


def test_pending_share_payout_is_followed_by_the_payouts_job(client):
    aid, (m1, _), ctx = create_active(client, weighted())
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    gw(client).next_payout_status = "PENDING"
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", window="7500.00")
    [bid] = gw(client).payouts
    gw(client).payouts[bid]["status"] = "SUCCESS"  # PayPal finished it without a webhook reaching us
    assert run_payouts(client, utcnow())["shares_followed"] == 1
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")


def test_pending_share_capture_completes_by_webhook(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    gw(client).next_capture_status = "PENDING"
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "settling"
    [s] = rows(client, Settlement, m1)
    event = {"id": "WH-P1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": s.paypal_ref}}
    assert webhook(client, event).json() == {"outcome": "capture completed"}
    t = tranche(client, aid, ctx)
    assert t["status"] == "funded" and t["amount_released"] == "7500.00"


def test_declined_share_goes_back_to_review_and_can_be_released_again(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    gw(client).next_capture_status = "DECLINED"
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "needs_review"
    t = tranche(client, aid, ctx)
    assert t["amount_released"] == "0.00" and t["paypal_authorization_id"]
    assert any("Share not released" in m.subject for m in client.app.state.mailer.sent)
    gw(client).next_capture_status = "COMPLETED"
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    assert [s.status for s in rows(client, Settlement, m1)] == ["failed", "succeeded"]


def test_lost_share_capture_response_resumes_with_the_same_key(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    gw(client).drop_next_response = True
    assert review(client, m1, ctx, "release_met").status_code == 502
    assert tranche(client, aid, ctx)["status"] == "settling"
    r = client.post(f"/milestones/{m1}/release", headers=h(ctx["payer"]))  # resumes the open attempt
    assert r.status_code == 200 and r.json()["milestone_status"] == "funded" and r.json()["amount"] == "7500.00"
    assert len(gw(client).captures) == 1  # PayPal replayed the first call


# --- the hold after a share ------------------------------------------------------------


def test_an_expired_hold_after_a_share_is_refunded_for_the_remainder_only(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    review(client, m1, ctx, "release_met")
    age_hold(client, m1, 30)
    with client.app.state.sessionmaker() as s:
        assert expire_stale_holds(s, utcnow()) == 1
    t = tranche(client, aid, ctx)
    assert t["status"] == "pending" and t["amount_released"] == "7500.00"
    assert agreement(client, aid, ctx)["funding"] == funding(released="7500.00", unfunded="22500.00")
    assert ("expired", "2500.00") in ledger(client, aid, ctx)
    fund(client, m1, ctx)  # the new hold covers only what's left
    orders = list(gw(client).orders.values())
    assert orders[-1]["amount"] == Decimal("2500.00")
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")


def test_reauthorizes_the_remainder_after_the_honor_period(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    review(client, m1, ctx, "release_met")
    age_hold(client, m1, 5)
    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert review(client, m1, ctx, "approve").json()["milestone_status"] == "settled"
    reauth = [a for a in gw(client).authorizations.values() if "reauthorized_from" in a]
    assert len(reauth) == 1 and reauth[0]["amount"] == Decimal("2500.00")
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_mock_paypal_refuses_capturing_more_than_authorized():
    pp = MockPayPal(base_url="http://x")
    o = pp.create_authorize_order(
        reference_id="m",
        description="d",
        amount=Decimal("100.00"),
        currency="USD",
        payee_merchant_id=None,
        return_url="r",
        cancel_url="c",
        request_id="o1",
    )
    pp.approve(o.order_id)
    auth = pp.authorize_order(o.order_id, request_id="a1")
    kw = {"currency": "USD", "payee_merchant_id": None, "delayed": False}
    pp.capture_authorization(auth.authorization_id, amount=Decimal("60.00"), request_id="c1", final=False, **kw)
    with pytest.raises(PayPalError, match="MAX_CAPTURE_AMOUNT_EXCEEDED"):
        pp.capture_authorization(auth.authorization_id, amount=Decimal("50.00"), request_id="c2", final=False, **kw)
    pp.capture_authorization(auth.authorization_id, amount=Decimal("40.00"), request_id="c3", final=True, **kw)
    with pytest.raises(PayPalError, match="ALREADY_CAPTURED"):
        pp.capture_authorization(auth.authorization_id, amount=Decimal("1.00"), request_id="c4", final=True, **kw)


def test_reconciliation_accepts_a_partially_captured_hold(client, partial):
    from app.services.reconciliation import reconcile

    _aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    review(client, m1, ctx, "release_met")
    with client.app.state.sessionmaker() as s:
        r = reconcile(s, gw(client), utcnow())
    assert r.checked == 2 and not r.mismatches  # the hold (PARTIALLY_CAPTURED) and the share's capture


# --- the dispute window, the record ----------------------------------------------------


def test_the_dispute_window_applies_to_the_final_capture_only(client):
    aid, (m1, _), ctx = create_active(client, weighted(dispute_window_days=3))
    connect_paypal(client, ctx)
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")  # paid at once
    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert review(client, m1, ctx, "approve").json()["milestone_status"] == "held"
    assert agreement(client, aid, ctx)["funding"] == funding(window="2500.00", released="7500.00")
    client.post(f"/milestones/{m1}/contest", json={"reason": "budget sheet looks off"}, headers=h(ctx["payer"]))
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.json()["milestone_status"] == "refunded" and r.json()["amount"] == "2500.00"
    t = tranche(client, aid, ctx)
    assert t["amount_released"] == "7500.00"  # the share stays with the payee; only the final capture came back
    assert agreement(client, aid, ctx)["funding"] == funding(
        released="7500.00", refunded="2500.00", unfunded="22500.00"
    )


def test_ledger_view_and_packet_show_the_split(client, partial):
    aid, (m1, _), ctx = partial
    fund(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    review(client, m1, ctx, "release_met")
    view = client.get(f"/agreements/{aid}/ledger", headers=h(ctx["payer"])).json()
    [s] = view["settlements"]
    assert (s["amount"], s["final"], s["criterion_ids"]) == ("7500.00", False, ["c1"])
    assert view["disbursements"] == []  # direct route, no window: the capture itself paid the payee
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["payer"])).json()
    t1 = packet["milestones"][0]
    assert t1["amount_released"] == "7500.00" and t1["criteria_paid"] == ["c1"]
    assert t1["settlements"][0]["amount"] == "7500.00" and t1["settlements"][0]["final"] is False
    assert packet["agreement"]["released"] == "7500.00"
    pdf = client.get(f"/agreements/{aid}/compliance-packet?format=pdf", headers=h(ctx["payer"]))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    with client.app.state.sessionmaker() as s:
        assert s.get(Milestone, m1).status == "funded"
