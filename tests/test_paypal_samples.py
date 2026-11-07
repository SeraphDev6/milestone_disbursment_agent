"""PayPal's own sample webhook payloads (from its webhook simulator in sandbox, Nov 5 2026) run through the handler.

The fixtures in tests/fixtures/paypal_simulator/ are PayPal's canned samples, not events from a real flow, so ids
are swapped for ones this test database knows; every other field, and where it sits, is PayPal's. Regenerate with
POST /v1/notifications/simulate-event (resource_version 2.0 for authorization and capture events). Live events
from a real flow are still issue #34.
"""

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.settlement import CAPTURE_EVENTS, DISPUTE_EVENTS, PAYOUT_ITEM_EVENTS, REFERENCED_PAYOUT_EVENTS
from tests.helpers import connect_paypal, create_active, h
from tests.test_escrow import auth_id, fund, gw, webhook
from tests.test_payouts import captured, tranche

FIXTURES = Path(__file__).parent / "fixtures" / "paypal_simulator"
HANDLED = {
    *CAPTURE_EVENTS,
    *PAYOUT_ITEM_EVENTS,
    *REFERENCED_PAYOUT_EVENTS,
    *DISPUTE_EVENTS,
    "PAYMENT.AUTHORIZATION.CREATED",
    "PAYMENT.AUTHORIZATION.VOIDED",
    "PAYMENT.CAPTURE.REFUNDED",
    "MERCHANT.ONBOARDING.COMPLETED",
    "MERCHANT.PARTNER-CONSENT.REVOKED",
    "INVOICING.INVOICE.PAID",
    "INVOICING.INVOICE.CANCELLED",
}


def sample(event_type: str, event_id: str) -> dict:
    event = copy.deepcopy(json.loads((FIXTURES / f"{event_type}.json").read_text()))
    event["id"] = event_id
    return event


def test_there_is_a_sample_for_every_event_we_handle():
    assert {p.stem for p in FIXTURES.glob("*.json")} == HANDLED


@pytest.mark.parametrize("event_type", sorted(HANDLED))
def test_unknown_ids_in_every_sample_are_ignored_not_errors(client, event_type):
    r = webhook(client, sample(event_type, f"WH-S-{event_type}"))
    assert r.status_code == 200 and r.json()["outcome"].startswith("ignored"), r.text


def test_dispute_sample_pauses_a_held_tranche(client, draft):
    aid, (m1, _), ctx = create_active(client, {**draft, "dispute_window_days": 3})
    connect_paypal(client, ctx)
    s = captured(client, aid, ctx, m1)
    event = sample("CUSTOMER.DISPUTE.CREATED", "WH-S-D1")
    event["resource"]["disputed_transactions"][0]["seller_transaction_id"] = s["paypal_ref"]
    assert webhook(client, event).json() == {"outcome": "dispute opened: payout paused"}
    assert tranche(client, m1).status == "contested"


def test_authorization_sample_without_related_ids_matches_the_hold_we_have(client, draft):
    _, (m1, _), ctx = create_active(client, draft)
    fund(client, m1, ctx)
    event = sample("PAYMENT.AUTHORIZATION.CREATED", "WH-S-A1")
    assert "supplementary_data" not in event["resource"]  # as PayPal's v2 sample has it
    event["resource"]["id"] = auth_id(client, m1)
    assert webhook(client, event).json() == {"outcome": "already funded"}


def test_onboarding_sample_without_tracking_id_finds_the_account_through_paypal(client, draft):
    _, _, ctx = create_active(client, draft)
    r = client.post(f"/orgs/{ctx['payee_org']}/paypal/connect", headers=h(ctx["payee"]))
    tracking_id = parse_qs(urlsplit(r.json()["onboarding_url"]).query)["tracking_id"][0]
    merchant_id = gw(client).complete_onboarding(tracking_id)  # finished on PayPal, never came back to us
    event = sample("MERCHANT.ONBOARDING.COMPLETED", "WH-S-O1")
    assert "tracking_id" not in event["resource"]  # as PayPal's sample has it
    event["resource"]["merchant_id"] = merchant_id
    assert webhook(client, event).json() == {"outcome": "onboarding completed"}
    again = sample("MERCHANT.ONBOARDING.COMPLETED", "WH-S-O2")
    again["resource"]["merchant_id"] = merchant_id
    assert webhook(client, again).json() == {"outcome": "onboarding already recorded"}
