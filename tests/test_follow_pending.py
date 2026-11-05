"""The payouts job asks PayPal about money still in flight instead of relying only on webhooks: escrow captures
left PENDING, pay-now payments left PENDING, and platform invoices paid or cancelled in PayPal. No webhook
arrives in any test here."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlmodel import select

from app.db import utcnow
from app.integrations.paypal import PayPalError
from app.models import AuditEvent, Settlement
from app.services.settlement import expire_stale_invoices, follow_pending
from tests.helpers import connect_paypal, create_active
from tests.test_escrow import agreement, fund, gw, release, verify
from tests.test_payouts import open_pay_link, run_payouts, tranche
from tests.test_reporting import complete_tranche_1


@pytest.fixture
def direct(client, draft):
    """Escrow; the payee connected PayPal, so captures go straight to them."""
    aid, mids, ctx = create_active(client, draft)
    connect_paypal(client, ctx)
    return aid, mids, ctx


@pytest.fixture
def platform(client, draft):
    """Escrow; the payee never connected PayPal, so captures land with the platform and Payouts pays them on."""
    return create_active(client, draft)


@pytest.fixture
def paynow(client, draft):
    """Invoice mode with a connected payee: tranche 1 verified and waiting on the payer's pay-now link."""
    aid, mids, ctx = create_active(client, {**draft, "settlement_mode": "invoice_on_verify"}, auto_release=True)
    connect_paypal(client, ctx)
    complete_tranche_1(client, mids[0], ctx)
    return aid, mids, ctx


@pytest.fixture
def platform_invoice(client, draft):
    """Invoice mode, payee not connected: tranche 1 verified and invoiced from the platform account."""
    aid, mids, ctx = create_active(client, {**draft, "settlement_mode": "invoice_on_verify"}, auto_release=True)
    complete_tranche_1(client, mids[0], ctx)
    return aid, mids, ctx


def follow(client):
    with client.app.state.sessionmaker() as s:
        return follow_pending(s, gw(client), utcnow())


def pending_capture(client, agreement_fixture):
    aid, (m1, _), keys = agreement_fixture
    fund(client, m1, keys)
    verify(client, m1, keys)
    gw(client).next_capture_status = "PENDING"
    s = release(client, m1, keys).json()
    assert s["milestone_status"] == "settling"
    return aid, m1, keys, s["paypal_ref"]


# --- escrow captures -------------------------------------------------------------------


def test_pending_capture_completed_at_paypal_settles_without_a_webhook(client, direct):
    aid, _, keys, capture_id = pending_capture(client, direct)
    assert follow(client)["captures_followed"] == 0  # still PENDING at PayPal: nothing to do

    gw(client).finish_capture(capture_id)
    assert run_payouts(client, utcnow())["captures_followed"] == 1
    a = agreement(client, aid, keys)
    assert a["milestones"][0]["status"] == "settled" and a["funding"]["released"] == "10000.00"
    assert not gw(client).payouts  # direct route: the capture itself paid the payee

    assert follow(client)["captures_followed"] == 0  # already settled: not followed again


def test_pending_platform_capture_completed_then_paid_on_by_payouts(client, platform):
    aid, _, keys, capture_id = pending_capture(client, platform)
    gw(client).finish_capture(capture_id)
    counts = run_payouts(client, utcnow())
    assert counts["captures_followed"] == 1 and counts["paid_out"] == 1  # same run: captured, then paid on
    assert agreement(client, aid, keys)["milestones"][0]["status"] == "settled"
    [payout] = gw(client).payouts.values()
    assert payout["email"] == "grantee@example.org"


def test_pending_capture_declined_at_paypal_fails_the_settlement(client, direct):
    _, m1, _, capture_id = pending_capture(client, direct)
    gw(client).finish_capture(capture_id, "DECLINED")
    assert follow(client)["captures_followed"] == 1
    assert tranche(client, m1).status == "settlement_failed"


def test_paypal_unreachable_leaves_the_capture_waiting(client, direct):
    _, m1, _, capture_id = pending_capture(client, direct)
    gw(client).finish_capture(capture_id)
    gw(client).fail_next = PayPalError("PayPal 503", status=503, ambiguous=True)
    assert follow(client) == {"captures_followed": 0, "payments_followed": 0, "invoices_followed": 0}
    assert tranche(client, m1).status == "settling"
    assert follow(client)["captures_followed"] == 1  # next run catches up


# --- pay-now links (connected payee) ---------------------------------------------------


def test_pending_payment_completed_at_paypal_settles_without_a_webhook(client, paynow):
    aid, (m1, _), ctx = paynow
    url = open_pay_link(client, aid, ctx)
    order_id = parse_qs(urlsplit(url).query)["token"][0]
    gw(client).next_capture_status = "PENDING"
    assert "processing" in client.get(url).text
    assert follow(client)["payments_followed"] == 0

    [capture_id] = [cid for cid, c in gw(client).captures.items() if c.get("order_id") == order_id]
    gw(client).finish_capture(capture_id)
    assert follow(client)["payments_followed"] == 1
    assert tranche(client, m1).status == "settled"


def test_unapproved_pay_link_order_is_left_alone(client, paynow):
    aid, (m1, _), ctx = paynow
    open_pay_link(client, aid, ctx)  # order created, payer never approves
    assert follow(client)["payments_followed"] == 0
    assert tranche(client, m1).status == "settling"


# --- platform invoices (payee not connected) -------------------------------------------


def paid_in_paypal(client) -> str:
    [inv_id] = gw(client).invoices
    gw(client).invoices[inv_id]["status"] = "PAID"  # the payer paid, and the webhook never came
    return inv_id


def test_invoice_paid_in_paypal_pays_the_payee_without_a_webhook(client, platform_invoice):
    aid, _, keys = platform_invoice
    assert follow(client)["invoices_followed"] == 0  # still open
    paid_in_paypal(client)

    counts = run_payouts(client, utcnow())
    assert counts["invoices_followed"] == 1 and counts["paid_out"] == 1
    a = agreement(client, aid, keys)
    assert a["milestones"][0]["status"] == "settled" and a["funding"]["released"] == "10000.00"
    [payout] = gw(client).payouts.values()
    assert payout["email"] == "grantee@example.org"


def test_invoice_cancelled_in_paypal_closes_it(client, platform_invoice):
    _, (m1, _), _ = platform_invoice
    [inv_id] = gw(client).invoices
    gw(client).invoices[inv_id]["status"] = "CANCELLED"
    assert follow(client)["invoices_followed"] == 1
    assert tranche(client, m1).status == "settlement_failed"


def test_expiry_settles_a_paid_invoice_instead_of_cancelling_it(client, platform_invoice):
    _, (m1, _), _ = platform_invoice
    inv_id = paid_in_paypal(client)
    later = utcnow() + timedelta(days=31)
    with client.app.state.sessionmaker() as s:
        assert expire_stale_invoices(s, gw(client), later) == 0
        assert s.exec(select(Settlement)).one().status == "succeeded"
        assert not s.exec(select(AuditEvent).where(AuditEvent.action == "invoice_cancelled")).all()
    assert gw(client).invoices[inv_id]["status"] == "PAID"  # not cancelled at PayPal
    assert tranche(client, m1).status == "held"  # collected by the platform; the payouts job pays the payee
    run_payouts(client, later)
    assert tranche(client, m1).status == "settled"


def test_expiry_does_not_cancel_when_paypal_cant_say(client, platform_invoice):
    _, (m1, _), _ = platform_invoice
    gw(client).fail_next = PayPalError("PayPal 503", status=503, ambiguous=True)
    with client.app.state.sessionmaker() as s:
        assert expire_stale_invoices(s, gw(client), utcnow() + timedelta(days=31)) == 0
    [inv] = gw(client).invoices.values()
    assert inv["status"] == "SENT" and tranche(client, m1).status == "settling"


def test_expiry_closes_an_invoice_paypal_no_longer_has(client, platform_invoice):
    _, (m1, _), _ = platform_invoice
    gw(client).invoices.clear()  # PayPal answers 404 for the status and for the cancel
    with client.app.state.sessionmaker() as s:
        assert expire_stale_invoices(s, gw(client), utcnow() + timedelta(days=31)) == 1
    assert tranche(client, m1).status == "settlement_failed"
