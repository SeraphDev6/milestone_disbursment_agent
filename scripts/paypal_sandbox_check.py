"""Check the PayPal sandbox (or live) credentials in .env without running the app.

    uv run python scripts/paypal_sandbox_check.py            # read-only checks
    uv run python scripts/paypal_sandbox_check.py --order    # also create a $1 AUTHORIZE order and print its
                                                             # approval link (nothing is charged until approved;
                                                             # unapproved orders lapse on their own)

Checks, in order: settings load (MODE, keys, SECRET_KEY); an OAuth token; the platform's merchant id (prints the
value to put in PAYPAL_PARTNER_MERCHANT_ID when it was looked up); the webhook id's event list; optionally the
order. Secrets are never printed.
"""

import argparse
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: `uv run python scripts/...`

from app.config import Settings
from app.integrations.paypal import PayPalError, SandboxPayPal


def ok(label: str, detail: str = "") -> None:
    print(f"  ok   {label}" + (f": {detail}" if detail else ""))


def fail(label: str, detail: str) -> None:
    print(f"  FAIL {label}: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--order", action="store_true", help="create a $1 AUTHORIZE order and print its approve link")
    args = parser.parse_args()

    try:
        settings = Settings()
    except ValueError as e:
        fail("settings", str(e))
        return 1
    ok(
        "settings",
        f"MODE={settings.mode.value}, API {settings.paypal_api_url}, client id ends ...{settings.paypal_client_id[-4:]}",
    )

    gw = SandboxPayPal(
        client_id=settings.paypal_client_id,
        client_secret=settings.paypal_client_secret.get_secret_value(),
        base_url=settings.paypal_api_url,
        webhook_id=settings.paypal_webhook_id,
        partner_merchant_id=settings.paypal_partner_merchant_id,
        bn_code=settings.paypal_bn_code,
    )
    failures = 0

    try:
        gw._access_token()
        ok("oauth token")
    except PayPalError as e:
        fail("oauth token", str(e))
        return 1

    try:
        merchant = gw._partner()
        if settings.paypal_partner_merchant_id:
            ok("partner merchant id", "from PAYPAL_PARTNER_MERCHANT_ID")
        else:
            ok("partner merchant id", f"looked up: set PAYPAL_PARTNER_MERCHANT_ID={merchant}")
    except PayPalError as e:
        fail("partner merchant id", str(e))
        failures += 1

    if settings.paypal_webhook_id:
        try:
            hook = gw._call("GET", f"/v1/notifications/webhooks/{settings.paypal_webhook_id}")
            names = sorted(t.get("name", "") for t in hook.get("event_types", []))
            ok("webhook", f"{hook.get('url')} with {len(names)} event type(s)")
            wanted = {
                "PAYMENT.AUTHORIZATION.CREATED",
                "PAYMENT.AUTHORIZATION.VOIDED",
                "PAYMENT.CAPTURE.COMPLETED",
                "PAYMENT.CAPTURE.DENIED",
                "PAYMENT.CAPTURE.PENDING",
                "PAYMENT.CAPTURE.REFUNDED",
                "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
                "PAYMENT.PAYOUTS-ITEM.UNCLAIMED",
                "PAYMENT.PAYOUTS-ITEM.FAILED",
                "PAYMENT.PAYOUTS-ITEM.RETURNED",
                "PAYMENT.REFERENCED-PAYOUT-ITEM.COMPLETED",
                "PAYMENT.REFERENCED-PAYOUT-ITEM.FAILED",
                "INVOICING.INVOICE.PAID",
                "INVOICING.INVOICE.CANCELLED",
                "MERCHANT.ONBOARDING.COMPLETED",
                "MERCHANT.PARTNER-CONSENT.REVOKED",
                "CUSTOMER.DISPUTE.CREATED",
                "CUSTOMER.DISPUTE.UPDATED",
                "CUSTOMER.DISPUTE.RESOLVED",
            }
            missing = sorted(wanted - set(names)) if "*" not in names else []
            if missing:
                fail("webhook events", "not subscribed: " + ", ".join(missing))
                failures += 1
            expected = f"{settings.api_base_url}/webhooks/paypal"
            if str(hook.get("url", "")).rstrip("/") != expected:
                print(f"  note webhook URL {hook.get('url')} should be {expected}")
        except PayPalError as e:
            fail("webhook", str(e))
            failures += 1
    else:
        fail("webhook", "PAYPAL_WEBHOOK_ID is unset; PayPal webhooks will be rejected")
        failures += 1

    if args.order:
        try:
            order = gw.create_authorize_order(
                reference_id="sandbox-check",
                description="Milestone engine sandbox check",
                amount=Decimal("1.00"),
                currency="USD",
                payee_merchant_id=None,
                return_url=f"{settings.api_base_url}/paypal/return",
                cancel_url=f"{settings.api_base_url}/paypal/cancel",
                request_id=f"sandbox-check-{uuid.uuid4().hex[:12]}",
            )
            ok("authorize order", f"{order.order_id}; approve at {order.approve_url}")
        except PayPalError as e:
            fail("authorize order", str(e))
            failures += 1

    print("all checks passed" if not failures else f"{failures} check(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
