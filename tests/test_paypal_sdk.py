"""The sandbox/live gateway's PayPal Server SDK path: what goes on the wire for orders, authorizations, captures,
voids and refunds, how responses are read, and how the SDK's errors map to PayPalError. PayPal is a mocked httpx
transport shared with the REST calls the SDK doesn't cover."""

import json
from decimal import Decimal

import httpx
import pytest

from app.integrations.paypal import Capture, PayPalError, SandboxPayPal

TOKEN = {"access_token": "tok", "token_type": "Bearer", "expires_in": 3600}


def gateway(handler, **kwargs) -> tuple[SandboxPayPal, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json=TOKEN)
        return handler(request)

    gw = SandboxPayPal(
        client_id="client-123",
        client_secret="secret",
        base_url="https://paypal.test",
        webhook_id=None,
        **kwargs,
    )
    gw._client = httpx.Client(base_url="https://paypal.test", transport=httpx.MockTransport(wrapped))
    return gw, seen


ORDER = {
    "id": "ORDER-1",
    "status": "PAYER_ACTION_REQUIRED",
    "links": [
        {"rel": "self", "href": "https://paypal.test/v2/checkout/orders/ORDER-1"},
        {"rel": "payer-action", "href": "https://paypal.test/approve?token=ORDER-1"},
    ],
}


def test_create_order_goes_through_the_sdk_with_our_headers():
    gw, seen = gateway(lambda r: httpx.Response(201, json=ORDER), bn_code="BN-CODE")
    order = gw.create_authorize_order(
        reference_id="m-1",
        description="Grant: tranche 1",
        amount=Decimal("10000.00"),
        currency="USD",
        payee_merchant_id="MERCHANT9",
        return_url="https://engine.example/paypal/return",
        cancel_url="https://engine.example/paypal/cancel",
        request_id="m-1-order-abc",
    )
    assert order.order_id == "ORDER-1" and order.approve_url == "https://paypal.test/approve?token=ORDER-1"

    token, create = seen
    assert token.url.path == "/v1/oauth2/token" and token.headers["authorization"].startswith("Basic ")
    assert create.url == "https://paypal.test/v2/checkout/orders"  # our base URL, not the SDK's environment
    assert create.headers["authorization"] == "Bearer tok"
    assert create.headers["paypal-request-id"] == "m-1-order-abc"
    assert create.headers["paypal-partner-attribution-id"] == "BN-CODE"
    assert create.headers["prefer"] == "return=representation"
    body = json.loads(create.content)
    assert body["intent"] == "AUTHORIZE"
    [unit] = body["purchase_units"]
    assert unit["amount"] == {"currency_code": "USD", "value": "10000.00"}
    assert unit["payee"] == {"merchant_id": "MERCHANT9"}
    assert unit["reference_id"] == unit["custom_id"] == "m-1"
    context = body["payment_source"]["paypal"]["experience_context"]
    assert context["return_url"] == "https://engine.example/paypal/return"
    assert context["cancel_url"] == "https://engine.example/paypal/cancel"
    assert context["shipping_preference"] == "NO_SHIPPING" and context["user_action"] == "CONTINUE"


def test_platform_route_order_names_no_payee_and_pay_now_orders_capture():
    gw, seen = gateway(lambda r: httpx.Response(201, json=ORDER))
    gw.create_payment_order(
        reference_id="m-2",
        description="d",
        amount=Decimal(5),
        currency="EUR",
        payee_merchant_id=None,
        return_url="https://e/paid",
        cancel_url="https://e/cancel",
        request_id="k",
    )
    body = json.loads(seen[-1].content)
    assert body["intent"] == "CAPTURE" and "payee" not in body["purchase_units"][0]
    assert body["purchase_units"][0]["amount"]["value"] == "5.00"


def test_authorize_and_capture_order_read_the_nested_payment():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/authorize"):
            payments = {"authorizations": [{"id": "AUTH-1", "status": "CREATED"}]}
        else:
            payments = {"captures": [{"id": "CAP-1", "status": "COMPLETED"}]}
        return httpx.Response(201, json={"id": "ORDER-1", "purchase_units": [{"payments": payments}]})

    gw, seen = gateway(handler)
    auth = gw.authorize_order("ORDER-1", request_id="ORDER-1-authorize")
    assert (auth.authorization_id, auth.status) == ("AUTH-1", "CREATED")
    cap = gw.capture_order("ORDER-1", request_id="ORDER-1-capture")
    assert (cap.capture_id, cap.status) == ("CAP-1", "COMPLETED")
    assert [r.url.path for r in seen[1:]] == [
        "/v2/checkout/orders/ORDER-1/authorize",
        "/v2/checkout/orders/ORDER-1/capture",
    ]
    assert seen[1].headers["paypal-request-id"] == "ORDER-1-authorize"


def test_reauthorize_capture_void_refund_act_for_the_payee():
    gw, seen = gateway(lambda r: httpx.Response(201, json={"id": "X-1", "status": "COMPLETED"}))
    auth = gw.reauthorize("AUTH-1", amount=Decimal(10), currency="USD", payee_merchant_id="M9", request_id="r1")
    assert auth.authorization_id == "X-1"
    cap = gw.capture_authorization(
        "AUTH-1", amount=Decimal(10), currency="USD", payee_merchant_id="M9", delayed=False, request_id="r2"
    )
    assert cap.capture_id == "X-1" and cap.status == "COMPLETED"
    gw.void_authorization("AUTH-1", payee_merchant_id="M9", request_id="r3")
    refund = gw.refund_capture("CAP-1", amount=Decimal(10), currency="USD", payee_merchant_id=None, request_id="r4")
    assert refund.refund_id == "X-1"

    reauth, capture, void, refund_req = seen[1:]
    assert reauth.url.path == "/v2/payments/authorizations/AUTH-1/reauthorize"
    assert json.loads(reauth.content) == {"amount": {"currency_code": "USD", "value": "10.00"}}
    assert json.loads(capture.content) == {"amount": {"currency_code": "USD", "value": "10.00"}, "final_capture": True}
    assert void.url.path == "/v2/payments/authorizations/AUTH-1/void"
    assert refund_req.url.path == "/v2/payments/captures/CAP-1/refund"
    for r, key in ((reauth, "r1"), (capture, "r2"), (void, "r3"), (refund_req, "r4")):
        assert r.headers["paypal-request-id"] == key
    assert all("paypal-auth-assertion" in r.headers for r in (reauth, capture, void))
    assert "paypal-auth-assertion" not in refund_req.headers  # platform's own capture


def test_sdk_errors_map_like_rest_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/void"):
            return httpx.Response(
                422,
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "The requested action could not be performed.",
                    "debug_id": "deadbeef",
                    "details": [{"issue": "AUTHORIZATION_ALREADY_CAPTURED", "description": "..."}],
                },
            )
        if request.url.path.endswith("/capture"):
            return httpx.Response(503, text="upstream unavailable", headers={"PayPal-Debug-Id": "dbg-503"})
        raise httpx.ConnectTimeout("timed out")

    gw, _ = gateway(handler)
    with pytest.raises(PayPalError) as e:
        gw.void_authorization("A", payee_merchant_id=None, request_id="v")
    assert str(e.value) == "PayPal 422: AUTHORIZATION_ALREADY_CAPTURED"
    assert e.value.status == 422 and not e.value.ambiguous and e.value.debug_id == "deadbeef"

    with pytest.raises(PayPalError) as e:
        gw.capture_authorization(
            "A", amount=Decimal(1), currency="USD", payee_merchant_id=None, delayed=False, request_id="c"
        )
    assert e.value.status == 503 and e.value.ambiguous and e.value.debug_id == "dbg-503"

    with pytest.raises(PayPalError) as e:
        gw.reauthorize("A", amount=Decimal(1), currency="USD", payee_merchant_id=None, request_id="r")
    assert e.value.ambiguous and "unreachable" in str(e.value)


def test_order_without_an_approval_link_is_an_error():
    gw, _ = gateway(lambda r: httpx.Response(201, json={"id": "ORDER-1", "links": []}))
    with pytest.raises(PayPalError, match="no approval link"):
        gw.create_authorize_order(
            reference_id="m",
            description="d",
            amount=Decimal(1),
            currency="USD",
            payee_merchant_id=None,
            return_url="https://e/r",
            cancel_url="https://e/c",
            request_id="k",
        )


def test_sdk_environment_follows_the_api_host():
    from paypalserversdk.configuration import Environment

    gw, _ = gateway(lambda r: httpx.Response(200))
    assert gw._sdk.config.environment is Environment.PRODUCTION  # "https://paypal.test" has no "sandbox"
    live = SandboxPayPal(client_id="i", client_secret="s", base_url="https://api-m.sandbox.paypal.com", webhook_id=None)
    assert live._sdk.config.environment is Environment.SANDBOX


def test_status_lookups_read_captures_orders_and_invoices():
    """The follow-up job's lookups: GET a capture and an order through the SDK, an invoice over REST."""
    responses = {
        "/v2/payments/captures/CAP-1": {"id": "CAP-1", "status": "COMPLETED"},
        "/v2/checkout/orders/ORDER-1": {
            "id": "ORDER-1",
            "status": "COMPLETED",
            "purchase_units": [{"payments": {"captures": [{"id": "CAP-2", "status": "PENDING"}]}}],
        },
        "/v2/checkout/orders/ORDER-2": {"id": "ORDER-2", "status": "APPROVED", "purchase_units": [{}]},
        "/v2/invoicing/invoices/INV-1": {"id": "INV-1", "status": "MARKED_AS_PAID"},
        "/v2/invoicing/invoices/INV-2": {"id": "INV-2", "status": "SENT"},
    }
    gw, seen = gateway(lambda r: httpx.Response(200, json=responses[r.url.path]))

    assert gw.capture_status("CAP-1") == Capture("CAP-1", "COMPLETED")
    assert gw.order_capture("ORDER-1") == Capture("CAP-2", "PENDING")
    assert gw.order_capture("ORDER-2") is None  # approved but not captured yet
    assert gw.invoice_status("INV-1") == "PAID"
    assert gw.invoice_status("INV-2") == "OPEN"
    assert all(r.method == "GET" for r in seen if r.url.path != "/v1/oauth2/token")


def test_status_lookup_errors_map_to_paypal_error():
    gw, _ = gateway(lambda r: httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "message": "no capture"}))
    with pytest.raises(PayPalError) as e:
        gw.capture_status("CAP-404")
    assert e.value.status == 404 and not e.value.ambiguous
