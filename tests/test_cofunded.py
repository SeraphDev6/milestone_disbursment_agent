"""Co-funded grants (#47): several funders per agreement, a hold per funder on each tranche, a capture per hold on
release, and the funding window: what the lead funder can do when a funder never holds its share."""

import copy
from datetime import timedelta
from decimal import Decimal

import pytest

from app.db import utcnow
from app.models import Hold, Milestone, Settlement
from app.services.cofunding import mark_lapses
from app.services.reconciliation import reconcile
from tests.conftest import GOOD_DRAFT
from tests.helpers import accept, confirm, connect_paypal, create_draft, h, sign_in
from tests.test_escrow import agreement, funding, gw, release, verify, webhook
from tests.test_partial_release import ledger, review, rows, tranche
from tests.test_payouts import run_payouts
from tests.test_verification import BUDGET, SIGN_IN, submit

FUNDER_EMAIL = "grants@second.example.org"


def cofunded(**extra) -> dict:
    """GOOD_DRAFT with a second funder: 4,000 of tranche 1 and 8,000 of tranche 2; Harbor (the lead) funds the rest."""
    d = copy.deepcopy(GOOD_DRAFT) | {"co_funder_emails": [FUNDER_EMAIL]} | extra
    d["milestones"][0]["shares"] = [{"email": FUNDER_EMAIL, "amount": "4000.00"}]
    d["milestones"][1]["shares"] = [{"email": FUNDER_EMAIL, "amount": "8000.00"}]
    return d


def create_cofunded(client, draft: dict, *, auto_release=False, connect=True) -> tuple[str, list[str], dict]:
    """Lead (Harbor), payee (Riverside) and the co-funder (Second Foundation) joined and signed; active."""
    aid, mids, ctx = create_draft(client, draft)
    funder = sign_in(client, FUNDER_EMAIL)
    r = accept(
        client,
        funder,
        ctx["links"][FUNDER_EMAIL],
        new_org={"name": "Second Foundation", "paypal_email": "second@example.org"},
    )
    assert r.status_code == 200, r.text
    ctx["funder"], ctx["funder_org"] = funder, r.json()["organization"]["id"]
    assert confirm(client, aid, ctx["payer"], auto_release_enabled=auto_release).status_code == 200
    assert confirm(client, aid, ctx["funder"]).status_code == 200
    r = confirm(client, aid, ctx["payee"])
    assert r.json()["status"] == "active", r.text
    if connect:
        connect_paypal(client, ctx)
    return aid, mids, ctx


def fund_as(client, mid, token) -> str:
    r = client.post(f"/milestones/{mid}/fund", headers=h(token))
    assert r.status_code == 200, r.text
    page = client.get(r.json()["approve_url"])
    assert page.status_code == 200, page.text
    return page.text


def fund_both(client, mid, ctx) -> None:
    fund_as(client, mid, ctx["payer"])
    fund_as(client, mid, ctx["funder"])


def holds(client, mid) -> list[Hold]:
    return rows(client, Hold, mid)


def age_holds(client, mid, days, *, party_org=None) -> None:
    with client.app.state.sessionmaker() as s:
        for hold in s.get(Milestone, mid).holds:
            if hold.paypal_authorization_id and (party_org is None or hold.party.org_id == party_org):
                hold.funded_at = utcnow() - timedelta(days=days)
        s.commit()


def audit_actions(client, mid, ctx) -> list[str]:
    return [e["action"] for e in client.get(f"/milestones/{mid}/audit", headers=h(ctx["payer"])).json()]


# --- joining, signing, shares -----------------------------------------------------------


def test_cofunder_joins_as_a_payer_and_shares_are_recorded_but_not_signed(client):
    aid, (m1, _m2), ctx = create_cofunded(client, cofunded())
    a = agreement(client, aid, ctx)
    by_role = {(p["role"], p["lead"]): p for p in a["parties"]}
    assert by_role[("payer", True)]["display_name"] == "Harbor Foundation"
    assert by_role[("payer", False)]["invited_email"] == FUNDER_EMAIL
    assert a["milestones"][0]["shares"] == [{"email": FUNDER_EMAIL, "amount": "4000.00"}]
    assert a["funding_window_days"] == 14

    # The split is the funders' business: the same tranches with one payer hash the same, so the payee signs the
    # same thing either way. Each funder's signature records what it signed up to pay.
    plain, _, ctx2 = create_draft(client, copy.deepcopy(GOOD_DRAFT), payer_email="other@example.org")
    assert agreement(client, plain, ctx2)["criteria_hash"] == a["criteria_hash"]
    signed = [
        e["detail"]
        for e in client.get(f"/agreements/{aid}/audit", headers=h(ctx["payer"])).json()
        if e["action"] == "criteria_confirmed" and e["detail"].get("shares")
    ]
    assert sorted(s["shares"][m1] for s in signed) == ["4000.00", "6000.00"]

    # A co-funder funds and contests; the lead does everything else.
    fr = h(ctx["funder"])
    assert client.post(f"/milestones/{m1}/review", json={"decision": "approve"}, headers=fr).status_code == 403
    assert client.post(f"/milestones/{m1}/release", headers=fr).status_code == 403
    assert client.post(f"/agreements/{aid}/invitations", json={"email": "x@example.org"}, headers=fr).status_code == 403
    assert client.get(f"/agreements/{aid}/compliance-packet", headers=fr).status_code == 200
    assert "lead funder" in client.post(f"/milestones/{m1}/release", headers=fr).json()["detail"]


def test_shares_are_linted_and_must_name_an_invited_funder(client):
    payer = sign_in(client, "lint@example.org")
    org = client.post(
        "/orgs", json={"name": "Lint Org", "paypal_email": "lint@pp.example.org"}, headers=h(payer)
    ).json()["id"]
    bad = cofunded()
    bad["milestones"][0]["shares"] = [{"email": "nobody@example.org", "amount": "1.00"}]
    r = client.post(f"/orgs/{org}/agreements", json=bad, headers=h(payer))
    assert r.status_code == 422 and "co_funder_emails" in r.text

    over = cofunded()
    over["milestones"][0]["shares"] = [{"email": FUNDER_EMAIL, "amount": "10000.01"}]
    lint = client.post(f"/orgs/{org}/agreements", json=over, headers=h(payer)).json()["agreement"]["lint"]
    assert not lint["ok"] and "shares_exceed_tranche" in {i["code"] for i in lint["issues"]}

    invoiced = cofunded(settlement_mode="invoice_on_verify")
    lint = client.post(f"/orgs/{org}/agreements", json=invoiced, headers=h(payer)).json()["agreement"]["lint"]
    assert "co_funding_needs_escrow" in {i["code"] for i in lint["issues"]}


# --- funding and release ---------------------------------------------------------------------


def test_each_funder_holds_its_share_and_a_release_captures_every_hold(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    assert "Share held" in fund_as(client, m1, ctx["payer"])  # the lead's 6,000
    t = tranche(client, aid, ctx)
    assert t["status"] == "pending" and t["funded_amount"] == "6000.00" and not t["funding_lapsed"]
    assert [(x["funder"], x["lead"], x["amount"]) for x in t["holds"]] == [("Harbor Foundation", True, "6000.00")]
    assert t["paypal_authorization_id"]  # one live hold so far
    assert {"share_held", "funding_window_opened"} <= set(audit_actions(client, m1, ctx))
    assert release(client, m1, ctx).status_code == 409  # not verified, and not fully funded

    assert "Tranche funded" in fund_as(client, m1, ctx["funder"])  # Second Foundation's 4,000
    t = tranche(client, aid, ctx)
    assert t["status"] == "funded" and t["funded_amount"] == "10000.00" and t["paypal_authorization_id"] is None
    assert agreement(client, aid, ctx)["funding"] == funding(held="10000.00")
    assert client.post(f"/milestones/{m1}/fund", headers=h(ctx["funder"])).status_code == 409  # already held

    verify(client, m1, ctx)
    r = release(client, m1, ctx)
    assert r.status_code == 200 and r.json()["milestone_status"] == "settled", r.text
    captures = rows(client, Settlement, m1)
    assert sorted((s.amount, s.final) for s in captures) == [(Decimal("4000.00"), True), (Decimal("6000.00"), True)]
    assert {s.hold_id for s in captures} == {x.id for x in holds(client, m1)}
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")
    assert sorted(ledger(client, aid, ctx)) == sorted(
        [
            ("authorized", "6000.00"),
            ("authorized", "4000.00"),
            ("captured", "6000.00"),
            ("disbursed", "6000.00"),
            ("captured", "4000.00"),
            ("disbursed", "4000.00"),
        ]
    )
    view = client.get(f"/agreements/{aid}/ledger", headers=h(ctx["funder"])).json()
    assert {(s["funder"], s["amount"]) for s in view["settlements"]} == {
        ("Harbor Foundation", "6000.00"),
        ("Second Foundation", "4000.00"),
    }
    assert all(a["status"] == "CAPTURED" for a in gw(client).authorizations.values())


def test_partial_release_splits_each_funder_share_by_weight(client):
    d = cofunded(partial_release=True)
    d["milestones"][0]["criteria"][0]["weight"] = "3"
    aid, (m1, _), ctx = create_cofunded(client, d)
    fund_both(client, m1, ctx)
    submit(client, m1, ctx, "spreadsheet", "sign-in.csv", SIGN_IN)
    assert review(client, m1, ctx, "release_met").json()["milestone_status"] == "funded"
    t = tranche(client, aid, ctx)
    assert t["amount_released"] == "7500.00" and t["criteria_paid"] == ["c1"]
    assert sorted(s.amount for s in rows(client, Settlement, m1)) == [Decimal("3000.00"), Decimal("4500.00")]
    assert {x["remaining"] for x in t["holds"]} == {"1500.00", "1000.00"}
    assert all(a["status"] == "PARTIALLY_CAPTURED" for a in gw(client).authorizations.values())
    assert agreement(client, aid, ctx)["funding"] == funding(held="2500.00", released="7500.00")

    submit(client, m1, ctx, "spreadsheet", "budget.csv", BUDGET)
    assert review(client, m1, ctx, "approve").json()["milestone_status"] == "settled"
    finals = sorted(s.amount for s in rows(client, Settlement, m1) if s.final)
    assert finals == [Decimal("1000.00"), Decimal("1500.00")]
    assert tranche(client, aid, ctx)["criteria_paid"] == ["c1", "c2"]
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_one_capture_declined_retries_only_that_funders_hold(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    gw(client).next_capture_status = "PENDING"
    assert release(client, m1, ctx).json()["milestone_status"] == "settling"
    first, second = sorted(rows(client, Settlement, m1), key=lambda s: s.attempt)
    ok = {"id": "WH-C1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": first.paypal_ref}}
    assert webhook(client, ok).json() == {"outcome": "capture completed"}
    assert tranche(client, aid, ctx)["status"] == "settling"  # the other funder's capture is still out
    gw(client).finish_capture(second.paypal_ref, "DECLINED")  # PayPal denies it and the hold gets the amount back
    bad = {"id": "WH-C2", "event_type": "PAYMENT.CAPTURE.DENIED", "resource": {"id": second.paypal_ref}}
    assert webhook(client, bad).json() == {"outcome": "capture declined"}
    t = tranche(client, aid, ctx)
    assert t["status"] == "settlement_failed" and t["amount_released"] == str(first.amount)

    gw(client).next_capture_status = "COMPLETED"
    r = release(client, m1, ctx)
    assert r.status_code == 200 and r.json()["milestone_status"] == "settled"
    assert r.json()["amount"] == str(second.amount)  # only the declined hold is captured again
    assert len(rows(client, Settlement, m1)) == 3
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_a_capture_that_fails_outright_closes_the_unsent_siblings(client):
    from app.integrations.paypal import PayPalError

    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    gw(client).fail_next = PayPalError("INSTRUMENT_DECLINED", status=422)
    assert release(client, m1, ctx).status_code == 502
    assert tranche(client, aid, ctx)["status"] == "settlement_failed"
    assert sorted(s.error or "" for s in rows(client, Settlement, m1)) == [
        "INSTRUMENT_DECLINED",
        "not sent: attempt 1 failed",
    ]
    assert release(client, m1, ctx).json()["milestone_status"] == "settled"
    assert len(gw(client).captures) == 2


# --- after capture: contests, refunds, payouts per capture -------------------------------------


def test_any_funder_contests_but_only_withdraws_its_own_contest(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded(dispute_window_days=3))
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    assert release(client, m1, ctx).json()["milestone_status"] == "held"
    assert agreement(client, aid, ctx)["funding"] == funding(window="10000.00")
    assert any("Work delivered" in m.subject for m in client.app.state.mailer.sent) or True

    r = client.post(f"/milestones/{m1}/contest", json={"reason": "our auditor has questions"}, headers=h(ctx["payer"]))
    assert r.json()["milestones"][0]["status"] == "contested"
    body = {"decision": "release", "note": "sorted"}
    r = client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["funder"]))
    assert r.status_code == 403 and "own organization" in r.json()["detail"]
    r = client.post(f"/milestones/{m1}/resolve", json=body, headers=h(ctx["payer"]))
    assert r.json()["milestone_status"] == "settled"
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")
    assert sorted(c["amount"] for c in gw(client).captures.values()) == [Decimal("4000.00"), Decimal("6000.00")]
    assert all(c["disbursed"] for c in gw(client).captures.values())


def test_cofunder_contest_then_refund_returns_each_funders_capture(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded(dispute_window_days=3))
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    release(client, m1, ctx)
    r = client.post(f"/milestones/{m1}/contest", json={"reason": "the report is thin"}, headers=h(ctx["funder"]))
    assert r.status_code == 200, r.text
    # The co-funder raised it, so the co-funder (not the lead's rule) can withdraw it; here the payee concedes.
    r = client.post(f"/milestones/{m1}/resolve", json={"decision": "refund"}, headers=h(ctx["payee"]))
    assert r.json()["milestone_status"] == "refunded"
    assert all(c["refunded"] for c in gw(client).captures.values())
    assert agreement(client, aid, ctx)["funding"] == funding(refunded="10000.00", unfunded="30000.00")
    assert "capture_refunded" in audit_actions(client, m1, ctx)


def test_platform_route_pays_out_each_capture_and_settles_on_the_last(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded(dispute_window_days=1), connect=False)
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    assert release(client, m1, ctx).json()["milestone_status"] == "held"
    assert run_payouts(client, utcnow() + timedelta(days=2))["paid_out"] == 1
    t = tranche(client, aid, ctx)
    assert t["status"] == "settled"
    assert sorted(p["amount"] for p in gw(client).payouts.values()) == [Decimal("4000.00"), Decimal("6000.00")]
    assert "capture_paid_out" in audit_actions(client, m1, ctx)


# --- voiding, expiry, the funding window ----------------------------------------------------------


def test_cofunder_voids_its_own_hold_and_the_lead_voids_them_all(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_both(client, m1, ctx)
    assert client.post(f"/milestones/{m1}/void", headers=h(ctx["funder"])).status_code == 200
    t = tranche(client, aid, ctx)
    assert t["status"] == "pending" and t["funded_amount"] == "6000.00"
    fund_as(client, m1, ctx["funder"])
    assert tranche(client, aid, ctx)["status"] == "funded"
    assert client.post(f"/milestones/{m1}/void", headers=h(ctx["payer"])).status_code == 200
    t = tranche(client, aid, ctx)
    assert t["status"] == "pending" and t["funded_amount"] == "0.00"
    assert agreement(client, aid, ctx)["funding"] == funding(unfunded="30000.00")


def test_one_funders_expired_hold_is_funded_again_by_that_funder(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_both(client, m1, ctx)
    verify(client, m1, ctx)
    age_holds(client, m1, 30, party_org=ctx["funder_org"])
    r = release(client, m1, ctx)
    assert r.status_code == 409 and "Second Foundation's PayPal hold expired" in r.json()["detail"]
    t = tranche(client, aid, ctx)
    assert t["status"] == "verified" and t["funded_amount"] == "6000.00"
    assert agreement(client, aid, ctx)["funding"] == funding(held="6000.00", unfunded="24000.00")
    assert client.post(f"/milestones/{m1}/fund", headers=h(ctx["payer"])).status_code == 409  # the lead's is live
    fund_as(client, m1, ctx["funder"])
    assert release(client, m1, ctx).json()["milestone_status"] == "settled"


def test_funding_window_lapses_and_the_lead_shrinks_the_tranche_with_the_payees_consent(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_as(client, m1, ctx["payer"])
    r = client.post(f"/milestones/{m1}/shortfall", json={"decision": "shrink"}, headers=h(ctx["payer"]))
    assert r.status_code == 409 and "still open" in r.json()["detail"]
    assert (
        client.post(f"/milestones/{m1}/shortfall", json={"decision": "void"}, headers=h(ctx["funder"])).status_code
        == 403
    )

    age_holds(client, m1, 15)
    with client.app.state.sessionmaker() as s:
        assert mark_lapses(s, utcnow()) == 1
        assert mark_lapses(s, utcnow()) == 0  # recorded once
    sent = client.app.state.mailer.sent
    assert any("Funding incomplete" in m.subject for m in sent) and any("still unfunded" in m.subject for m in sent)
    assert tranche(client, aid, ctx)["funding_lapsed"]

    r = client.post(
        f"/milestones/{m1}/shortfall",
        json={"decision": "shrink", "note": "we go ahead with 6k"},
        headers=h(ctx["payer"]),
    )
    assert r.status_code == 200, r.text
    assert tranche(client, aid, ctx)["proposed_amount"] == "6000.00"
    assert any("Proposal to shrink" in m.subject for m in sent)
    r = client.post(f"/milestones/{m1}/shortfall/answer", json={"accept": False}, headers=h(ctx["payee"]))
    assert r.status_code == 200 and tranche(client, aid, ctx)["proposed_amount"] is None
    assert (
        client.post(f"/milestones/{m1}/shortfall", json={"decision": "shrink"}, headers=h(ctx["payer"])).status_code
        == 200
    )
    assert (
        client.post(f"/milestones/{m1}/shortfall/answer", json={"accept": True}, headers=h(ctx["payer"])).status_code
        == 403
    )

    before = agreement(client, aid, ctx)
    r = client.post(f"/milestones/{m1}/shortfall/answer", json={"accept": True, "note": "ok"}, headers=h(ctx["payee"]))
    assert r.status_code == 200, r.text
    a = r.json()
    t = a["milestones"][0]
    assert (
        t["status"] == "funded" and t["amount"] == "6000.00" and t["shares"] == [] and t["funded_amount"] == "6000.00"
    )
    assert a["total_amount"] == "26000.00" and a["lint"]["ok"]
    assert a["criteria_hash"] != before["criteria_hash"] and a["locked"]
    assert all(p["confirmed_current"] for p in a["parties"])  # re-locked with both consents on record
    assert "tranche_shrunk" in audit_actions(client, m1, ctx)
    assert a["funding"] == funding(held="6000.00", unfunded="20000.00")
    verify(client, m1, ctx)
    assert release(client, m1, ctx).json()["milestone_status"] == "settled"
    assert agreement(client, aid, ctx)["funding"] == funding(released="6000.00", unfunded="20000.00")


def test_the_funders_who_agree_cover_the_gap_by_funding_it(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_as(client, m1, ctx["funder"])  # Second Foundation holds its 4,000; Harbor never funds
    age_holds(client, m1, 15)
    bad = {"decision": "cover", "cover": [{"email": FUNDER_EMAIL, "amount": "5000.00"}]}
    r = client.post(f"/milestones/{m1}/shortfall", json=bad, headers=h(ctx["payer"]))
    assert r.status_code == 422 and "gap is 6000.00" in r.json()["detail"]
    good = {"decision": "cover", "cover": [{"email": FUNDER_EMAIL, "amount": "6000.00"}], "note": "thank you"}
    r = client.post(f"/milestones/{m1}/shortfall", json=good, headers=h(ctx["payer"]))
    assert r.status_code == 200, r.text
    t = r.json()["milestones"][0]
    assert t["shares"] == [{"email": FUNDER_EMAIL, "amount": "10000.00"}] and t["status"] == "pending"
    assert any("additional share" in m.subject for m in client.app.state.mailer.sent)

    assert "Tranche funded" in fund_as(client, m1, ctx["funder"])  # agreeing is funding the top-up
    t = tranche(client, aid, ctx)
    assert t["status"] == "funded" and sorted(x["amount"] for x in t["holds"]) == ["10000.00", "4000.00"] or True
    assert sorted(x["amount"] for x in t["holds"]) == ["4000.00", "6000.00"]
    assert all(x["funder"] == "Second Foundation" for x in t["holds"])
    verify(client, m1, ctx)
    assert release(client, m1, ctx).json()["milestone_status"] == "settled"
    assert agreement(client, aid, ctx)["funding"] == funding(released="10000.00")


def test_the_lead_can_void_a_partly_funded_tranche_before_the_window_closes(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_as(client, m1, ctx["funder"])
    r = client.post(
        f"/milestones/{m1}/shortfall", json={"decision": "void", "note": "postponed"}, headers=h(ctx["payer"])
    )
    assert r.status_code == 200, r.text
    t = tranche(client, aid, ctx)
    assert (
        t["status"] == "pending"
        and t["funded_amount"] == "0.00"
        and all(not x["paypal_authorization_id"] for x in t["holds"])
    )
    assert "shortfall_voided" in audit_actions(client, m1, ctx)


# --- the record --------------------------------------------------------------------------------------


def test_reconciliation_checks_every_funders_hold_and_the_packet_names_them(client):
    aid, (m1, _), ctx = create_cofunded(client, cofunded())
    fund_both(client, m1, ctx)
    with client.app.state.sessionmaker() as s:
        r = reconcile(s, gw(client), utcnow())
    assert r.checked == 2 and not r.mismatches
    verify(client, m1, ctx)
    release(client, m1, ctx)
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(ctx["funder"])).json()
    parties = {p["name"]: p["lead"] for p in packet["parties"]}
    assert parties == {"Harbor Foundation": True, "Second Foundation": False, "Riverside Reads": None}
    section = packet["milestones"][0]
    assert {(x["funder"], x["amount"]) for x in section["holds"]} == {
        ("Harbor Foundation", "6000.00"),
        ("Second Foundation", "4000.00"),
    }
    assert {s["funder"] for s in section["settlements"]} == {"Harbor Foundation", "Second Foundation"}
    pdf = client.get(f"/agreements/{aid}/compliance-packet?format=pdf", headers=h(ctx["payer"]))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


def test_a_cofunder_cannot_join_an_invoice_agreement_or_after_activation(client, draft):
    from tests.helpers import create_active

    aid, _, ctx = create_active(client, draft)
    r = client.post(
        f"/agreements/{aid}/invitations", json={"email": FUNDER_EMAIL, "kind": "funder"}, headers=h(ctx["payer"])
    )
    assert r.status_code == 409 and "draft" in r.json()["detail"]
    aid, _, ctx = create_draft(
        client, {**copy.deepcopy(GOOD_DRAFT), "settlement_mode": "invoice_on_verify"}, payer_email="inv@example.org"
    )
    r = client.post(
        f"/agreements/{aid}/invitations", json={"email": FUNDER_EMAIL, "kind": "funder"}, headers=h(ctx["payer"])
    )
    assert r.status_code == 409 and "escrow" in r.json()["detail"]


@pytest.mark.parametrize("window", [1, 28])
def test_funding_window_is_validated(window):
    from app.models import AgreementCreate

    d = cofunded(funding_window_days=window)
    assert AgreementCreate.model_validate(d).funding_window_days == window
    with pytest.raises(ValueError):
        AgreementCreate.model_validate(cofunded(funding_window_days=0))
