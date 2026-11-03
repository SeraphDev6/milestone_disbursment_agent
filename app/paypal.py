"""PayPal gateway: the only code that talks to PayPal.

Escrow uses Orders v2 with intent=AUTHORIZE, one order per tranche, with the payee named on the
order so funds go straight from payer to payee on capture and the platform never holds them.

Two implementations share one interface:
- MockPayPal: in-memory, deterministic MOCK ids, approval simulated by our own /mock route.
- SandboxPayPal: PayPal REST over httpx. Kept behind this interface so it can be swapped for the
  APIMatic-generated PayPal Server SDK once the day-one check confirms it covers Authorizations.
"""

import itertools
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

import httpx


class PayPalError(Exception):
    """A PayPal call failed.

    ambiguous=True means we can't know whether PayPal acted (network error, timeout, 5xx), so the
    caller must retry with the same idempotency key rather than start a new attempt.
    """

    def __init__(
        self, message: str, *, status: int | None = None, ambiguous: bool = False, debug_id: str | None = None
    ):
        super().__init__(message)
        self.status = status
        self.ambiguous = ambiguous
        self.debug_id = debug_id


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    approve_url: str


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    status: str  # CREATED, CAPTURED, DENIED, PENDING, ...


@dataclass(frozen=True)
class Capture:
    capture_id: str
    status: str  # COMPLETED, PENDING, DECLINED, FAILED


class PayPalGateway(Protocol):
    def create_authorize_order(
        self,
        *,
        reference_id: str,
        description: str,
        amount: Decimal,
        currency: str,
        payee_email: str,
        return_url: str,
        cancel_url: str,
        request_id: str,
    ) -> CreatedOrder: ...

    def authorize_order(self, order_id: str, *, request_id: str) -> Authorization: ...

    def reauthorize(
        self, authorization_id: str, *, amount: Decimal, currency: str, request_id: str
    ) -> Authorization: ...

    def capture_authorization(
        self, authorization_id: str, *, amount: Decimal, currency: str, request_id: str
    ) -> Capture: ...

    def void_authorization(self, authorization_id: str, *, request_id: str) -> None: ...

    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool: ...


def _money(amount: Decimal, currency: str) -> dict:
    return {"currency_code": currency, "value": f"{amount:.2f}"}


# --- Mock ------------------------------------------------------------------------------


MOCK_WEBHOOK_SIGNATURE = "mock-valid"


@dataclass
class MockPayPal:
    """Deterministic stand-in. Honors idempotency keys like PayPal does."""

    base_url: str
    orders: dict[str, dict] = field(default_factory=dict)
    authorizations: dict[str, dict] = field(default_factory=dict)
    next_capture_status: str = "COMPLETED"  # tests flip this to PENDING / DECLINED
    fail_next: PayPalError | None = None  # tests inject a failure for the next call (PayPal did nothing)
    drop_next_response: bool = False  # PayPal acts, but the response is lost (timeout after success)
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))
    _seen: dict[str, object] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _id(self, prefix: str) -> str:
        return f"MOCK-{prefix}-{next(self._seq):05d}"

    def _maybe_fail(self) -> None:
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err

    def _idempotent(self, request_id: str, fn):
        with self._lock:
            if request_id in self._seen:
                return self._seen[request_id]
            self._maybe_fail()
            result = fn()
            self._seen[request_id] = result
            if self.drop_next_response:
                self.drop_next_response = False
                raise PayPalError("timed out waiting for PayPal", ambiguous=True)
            return result

    def approve(self, order_id: str) -> None:
        """What the payer does on PayPal's approval page."""
        order = self.orders.get(order_id)
        if order is None:
            raise PayPalError(f"unknown order {order_id}", status=404)
        order["status"] = "APPROVED"

    def create_authorize_order(self, *, reference_id, description, amount, currency, payee_email, return_url,
                               cancel_url, request_id) -> CreatedOrder:  # fmt: skip
        def create():
            oid = self._id("ORDER")
            self.orders[oid] = {"status": "CREATED", "amount": amount, "currency": currency, "payee": payee_email,
                                "return_url": return_url, "reference_id": reference_id}  # fmt: skip
            return CreatedOrder(oid, f"{self.base_url}/mock/paypal/approve?token={oid}")

        return self._idempotent(request_id, create)

    def authorize_order(self, order_id, *, request_id) -> Authorization:
        def authorize():
            order = self.orders.get(order_id)
            if order is None or order["status"] != "APPROVED":
                raise PayPalError("ORDER_NOT_APPROVED: payer has not approved the order", status=422)
            aid = self._id("AUTH")
            order["status"] = "COMPLETED"
            self.authorizations[aid] = {"status": "CREATED", "order_id": order_id, "amount": order["amount"]}
            return Authorization(aid, "CREATED")

        return self._idempotent(request_id, authorize)

    def reauthorize(self, authorization_id, *, amount, currency, request_id) -> Authorization:
        def reauth():
            old = self.authorizations.get(authorization_id)
            if old is None or old["status"] != "CREATED":
                raise PayPalError("AUTHORIZATION_NOT_REAUTHORIZABLE", status=422)
            aid = self._id("AUTH")
            self.authorizations[aid] = {**old, "reauthorized_from": authorization_id}
            return Authorization(aid, "CREATED")

        return self._idempotent(request_id, reauth)

    def capture_authorization(self, authorization_id, *, amount, currency, request_id) -> Capture:
        def capture():
            auth = self.authorizations.get(authorization_id)
            if auth is None:
                raise PayPalError("RESOURCE_NOT_FOUND: authorization", status=404)
            if auth["status"] == "CAPTURED":
                raise PayPalError("AUTHORIZATION_ALREADY_CAPTURED", status=422)
            if auth["status"] == "VOIDED":
                raise PayPalError("AUTHORIZATION_VOIDED", status=422)
            status = self.next_capture_status
            if status in ("COMPLETED", "PENDING"):
                auth["status"] = "CAPTURED"
            return Capture(self._id("CAPTURE"), status)

        return self._idempotent(request_id, capture)

    def void_authorization(self, authorization_id, *, request_id) -> None:
        def void():
            auth = self.authorizations.get(authorization_id)
            if auth is None or auth["status"] != "CREATED":
                raise PayPalError("AUTHORIZATION_NOT_VOIDABLE", status=422)
            auth["status"] = "VOIDED"

        self._idempotent(request_id, void)

    def verify_webhook(self, headers, event) -> bool:
        return headers.get("paypal-transmission-sig") == MOCK_WEBHOOK_SIGNATURE


# --- Sandbox / live REST ---------------------------------------------------------------


class SandboxPayPal:
    def __init__(self, *, client_id: str, client_secret: str, base_url: str, webhook_id: str | None,
                 timeout: float = 20.0):  # fmt: skip
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._auth = (client_id, client_secret)
        self._webhook_id = webhook_id
        self._token: str | None = None
        self._token_expires = 0.0
        self._lock = threading.Lock()

    def _access_token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._token_expires - 60:
                return self._token
            r = self._client.post("/v1/oauth2/token", data={"grant_type": "client_credentials"}, auth=self._auth)
            if r.status_code != 200:
                raise PayPalError(f"PayPal auth failed: {r.text[:200]}", status=r.status_code)
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.monotonic() + int(body.get("expires_in", 300))
            return self._token

    def _call(self, method: str, path: str, *, request_id: str | None = None, json: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            r = self._client.request(method, path, headers=headers, json=json if json is not None else {})
        except httpx.HTTPError as e:
            raise PayPalError(f"PayPal unreachable: {e}", ambiguous=True) from e
        if r.status_code >= 400:
            debug_id = r.headers.get("PayPal-Debug-Id")
            try:
                body = r.json()
                detail = body.get("details", [{}])[0].get("issue") or body.get("name") or body.get("message")
            except ValueError:
                detail = r.text[:200]
            raise PayPalError(
                f"PayPal {r.status_code}: {detail}", status=r.status_code, ambiguous=r.status_code >= 500,
                debug_id=debug_id,
            )  # fmt: skip
        return r.json() if r.content else {}

    def create_authorize_order(self, *, reference_id, description, amount, currency, payee_email, return_url,
                               cancel_url, request_id) -> CreatedOrder:  # fmt: skip
        body = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "custom_id": reference_id,
                    "description": description[:127],
                    "amount": _money(amount, currency),
                    "payee": {"email_address": payee_email},
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "return_url": return_url,
                        "cancel_url": cancel_url,
                        "user_action": "CONTINUE",
                        "shipping_preference": "NO_SHIPPING",
                    }
                }
            },
        }
        data = self._call("POST", "/v2/checkout/orders", request_id=request_id, json=body)
        url = next((link["href"] for link in data.get("links", []) if link["rel"] in ("payer-action", "approve")), None)
        if url is None:
            raise PayPalError("PayPal returned no approval link")
        return CreatedOrder(data["id"], url)

    def authorize_order(self, order_id, *, request_id) -> Authorization:
        data = self._call("POST", f"/v2/checkout/orders/{order_id}/authorize", request_id=request_id)
        auth = data["purchase_units"][0]["payments"]["authorizations"][0]
        return Authorization(auth["id"], auth["status"])

    def reauthorize(self, authorization_id, *, amount, currency, request_id) -> Authorization:
        data = self._call(
            "POST", f"/v2/payments/authorizations/{authorization_id}/reauthorize", request_id=request_id,
            json={"amount": _money(amount, currency)},
        )  # fmt: skip
        return Authorization(data["id"], data["status"])

    def capture_authorization(self, authorization_id, *, amount, currency, request_id) -> Capture:
        data = self._call(
            "POST", f"/v2/payments/authorizations/{authorization_id}/capture", request_id=request_id,
            json={"amount": _money(amount, currency), "final_capture": True},
        )  # fmt: skip
        return Capture(data["id"], data["status"])

    def void_authorization(self, authorization_id, *, request_id) -> None:
        self._call("POST", f"/v2/payments/authorizations/{authorization_id}/void", request_id=request_id)

    def verify_webhook(self, headers, event) -> bool:
        if not self._webhook_id:
            return False
        try:
            data = self._call(
                "POST",
                "/v1/notifications/verify-webhook-signature",
                json={
                    "auth_algo": headers.get("paypal-auth-algo"),
                    "cert_url": headers.get("paypal-cert-url"),
                    "transmission_id": headers.get("paypal-transmission-id"),
                    "transmission_sig": headers.get("paypal-transmission-sig"),
                    "transmission_time": headers.get("paypal-transmission-time"),
                    "webhook_id": self._webhook_id,
                    "webhook_event": event,
                },
            )
        except PayPalError:
            return False
        return data.get("verification_status") == "SUCCESS"
