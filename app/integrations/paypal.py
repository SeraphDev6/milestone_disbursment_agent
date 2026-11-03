"""PayPal gateway: the only code that talks to PayPal.

Escrow uses Orders v2 with intent=AUTHORIZE, one order per tranche. Two payout routes:
- direct: the payee connected PayPal through partner onboarding, so the order names their merchant id and the
  money goes payer -> payee without touching the platform. Calls on the payee's authorizations and captures
  (void, reauthorize, capture, refund) carry PayPal-Auth-Assertion to act on their behalf; PayPal refuses a
  void without it. A dispute window captures with disbursement_mode=DELAYED and releases with a referenced
  payout; PayPal pays the payee automatically 28 days after capture if we never release.
- platform: the payee never connected, so the order pays the platform account and the payee is paid with
  PayPal Payouts to their email.

Invoice mode: a connected payee is paid through a pay-now order (intent=CAPTURE, payee = their merchant id); an
unconnected one through a PayPal invoice from the platform account, then Payouts.

Two implementations share one interface:
- MockPayPal: in-memory, deterministic MOCK ids, approval simulated by our own /mock route.
- SandboxPayPal: PayPal REST over httpx. Kept behind this interface so it can be swapped for the
  APIMatic-generated PayPal Server SDK once the day-one check confirms it covers Authorizations.
"""

import base64
import itertools
import json
import threading
import time
import uuid
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
        self,
        message: str,
        *,
        status: int | None = None,
        ambiguous: bool = False,
        debug_id: str | None = None,
        duplicate: bool = False,
    ):
        super().__init__(message)
        self.status = status
        self.ambiguous = ambiguous
        self.debug_id = debug_id
        # The request was already accepted under this idempotency key (PayPal answers some replays, e.g. a
        # Payouts sender_batch_id, with an error instead of the original response). The outcome arrives by webhook.
        self.duplicate = duplicate


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


@dataclass(frozen=True)
class Refund:
    refund_id: str
    status: str  # COMPLETED, PENDING, FAILED, CANCELLED


@dataclass(frozen=True)
class PayoutResult:
    """A payout to the payee. ref: referenced-payout item id, or Payouts batch id."""

    ref: str
    status: str  # SUCCESS, PENDING, UNCLAIMED, FAILED


@dataclass(frozen=True)
class MerchantStatus:
    merchant_id: str
    payments_receivable: bool
    primary_email_confirmed: bool
    permissions_granted: bool


PAYOUT_ITEM_STATUS = {  # Payouts item transaction_status -> ours
    "SUCCESS": "SUCCESS",
    "UNCLAIMED": "UNCLAIMED",
    "PENDING": "PENDING",
    "ONHOLD": "PENDING",
    "NEW": "PENDING",
    "PROCESSING": "PENDING",
}  # FAILED, RETURNED, BLOCKED, REFUNDED, REVERSED, DENIED -> FAILED

# What a connected payee must grant us (onboarding features PAYMENT, REFUND, DELAY_FUNDS_DISBURSEMENT).
REQUIRED_SCOPES = ("payments/refund", "payments/delay-funds-disbursement")


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    payer_view_url: str | None


class PayPalGateway(Protocol):
    def create_authorize_order(
        self,
        *,
        reference_id: str,
        description: str,
        amount: Decimal,
        currency: str,
        payee_merchant_id: str | None,  # None: the platform account is the payee
        return_url: str,
        cancel_url: str,
        request_id: str,
    ) -> CreatedOrder: ...

    def authorize_order(self, order_id: str, *, request_id: str) -> Authorization: ...

    def create_payment_order(
        self,
        *,
        reference_id: str,
        description: str,
        amount: Decimal,
        currency: str,
        payee_merchant_id: str,
        return_url: str,
        cancel_url: str,
        request_id: str,
    ) -> CreatedOrder: ...  # intent=CAPTURE: the payer pays the payee now (invoice mode, connected payee)

    def capture_order(self, order_id: str, *, request_id: str) -> Capture: ...

    def reauthorize(
        self, authorization_id: str, *, amount: Decimal, currency: str, payee_merchant_id: str | None, request_id: str
    ) -> Authorization: ...

    def capture_authorization(
        self,
        authorization_id: str,
        *,
        amount: Decimal,
        currency: str,
        payee_merchant_id: str | None,
        delayed: bool,
        request_id: str,
    ) -> Capture: ...

    def void_authorization(self, authorization_id: str, *, payee_merchant_id: str | None, request_id: str) -> None: ...

    def refund_capture(
        self, capture_id: str, *, amount: Decimal, currency: str, payee_merchant_id: str | None, request_id: str
    ) -> Refund: ...

    def release_delayed(self, capture_id: str, *, request_id: str) -> PayoutResult: ...

    def send_payout(
        self, *, email: str, amount: Decimal, currency: str, note: str, request_id: str
    ) -> PayoutResult: ...

    def payout_status(self, batch_id: str) -> PayoutResult: ...

    def create_partner_referral(self, *, tracking_id: str, return_url: str, email: str | None) -> str: ...

    def find_merchant(self, tracking_id: str) -> str | None: ...

    def merchant_status(self, merchant_id: str) -> MerchantStatus: ...

    def create_and_send_invoice(
        self,
        *,
        recipient_email: str,
        item_name: str,
        amount: Decimal,
        currency: str,
        note: str,
        reference: str,
        request_id: str,
    ) -> Invoice: ...

    def cancel_invoice(self, invoice_id: str, *, request_id: str) -> None: ...

    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool: ...


def _money(amount: Decimal, currency: str) -> dict:
    return {"currency_code": currency, "value": f"{amount:.2f}"}


# --- Mock ------------------------------------------------------------------------------


MOCK_WEBHOOK_SIGNATURE = "mock-valid"


@dataclass
class MockPayPal:
    """Deterministic stand-in. Honors idempotency keys like PayPal does, and refuses what PayPal refuses."""

    base_url: str
    orders: dict[str, dict] = field(default_factory=dict)
    authorizations: dict[str, dict] = field(default_factory=dict)
    captures: dict[str, dict] = field(default_factory=dict)
    payouts: dict[str, dict] = field(default_factory=dict)  # batch id -> payout
    invoices: dict[str, dict] = field(default_factory=dict)
    referrals: dict[str, dict] = field(default_factory=dict)  # tracking id -> {return_url, merchant_id}
    merchants: dict[str, MerchantStatus] = field(default_factory=dict)
    next_capture_status: str = "COMPLETED"  # tests flip this to PENDING / DECLINED
    next_payout_status: str = "SUCCESS"  # PENDING, UNCLAIMED, FAILED
    fail_next: PayPalError | None = None  # tests inject a failure for the next call (PayPal did nothing)
    drop_next_response: bool = False  # PayPal acts, but the response is lost (timeout after success)
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1))
    # Unique per process so ids never collide with rows a previous run left in a persistent database.
    _run: str = field(default_factory=lambda: uuid.uuid4().hex[:6].upper())
    _seen: dict[str, object] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _id(self, prefix: str) -> str:
        return f"MOCK-{prefix}-{self._run}-{next(self._seq):05d}"

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

    @staticmethod
    def _act_for(payee: str | None, payee_merchant_id: str | None) -> None:
        """PayPal refuses calls on a connected payee's money without PayPal-Auth-Assertion for that payee."""
        if payee is not None and payee_merchant_id != payee:
            raise PayPalError("PayPal 403: PERMISSION_DENIED", status=403)

    # --- simulation hooks (what people do on PayPal's pages) ---

    def approve(self, order_id: str) -> None:
        """What the payer does on PayPal's approval page."""
        order = self.orders.get(order_id)
        if order is None:
            raise PayPalError(f"unknown order {order_id}", status=404)
        order["status"] = "APPROVED"

    def complete_onboarding(self, tracking_id: str, **status) -> str:
        """What the payee does on PayPal's onboarding pages. Overrides (e.g. payments_receivable=False) simulate
        an account that isn't ready."""
        ref = self.referrals[tracking_id]
        mid = ref.get("merchant_id") or f"MOCKMERCHANT{next(self._seq):05d}"
        ref["merchant_id"] = mid
        self.merchants[mid] = MerchantStatus(
            mid,
            status.get("payments_receivable", True),
            status.get("primary_email_confirmed", True),
            status.get("permissions_granted", True),
        )
        return mid

    # --- orders and authorizations ---

    def create_authorize_order(
        self, *, reference_id, description, amount, currency, payee_merchant_id, return_url, cancel_url, request_id
    ) -> CreatedOrder:
        def create():
            oid = self._id("ORDER")
            self.orders[oid] = {
                "status": "CREATED",
                "amount": amount,
                "currency": currency,
                "payee": payee_merchant_id,  # None = platform
                "return_url": return_url,
                "reference_id": reference_id,
            }
            return CreatedOrder(oid, f"{self.base_url}/mock/paypal/approve?token={oid}")

        return self._idempotent(request_id, create)

    def create_payment_order(
        self, *, reference_id, description, amount, currency, payee_merchant_id, return_url, cancel_url, request_id
    ) -> CreatedOrder:
        def create():
            oid = self._id("ORDER")
            self.orders[oid] = {
                "status": "CREATED",
                "intent": "CAPTURE",
                "amount": amount,
                "currency": currency,
                "payee": payee_merchant_id,
                "return_url": return_url,
                "reference_id": reference_id,
            }
            return CreatedOrder(oid, f"{self.base_url}/mock/paypal/approve?token={oid}")

        return self._idempotent(request_id, create)

    def capture_order(self, order_id, *, request_id) -> Capture:
        def capture():
            order = self.orders.get(order_id)
            if order is None or order.get("intent") != "CAPTURE":
                raise PayPalError("RESOURCE_NOT_FOUND: order", status=404)
            if order["status"] == "COMPLETED":
                raise PayPalError("ORDER_ALREADY_CAPTURED", status=422)
            if order["status"] != "APPROVED":
                raise PayPalError("ORDER_NOT_APPROVED: payer has not approved the order", status=422)
            status = self.next_capture_status
            cid = self._id("CAPTURE")
            if status in ("COMPLETED", "PENDING"):
                order["status"] = "COMPLETED"
                self.captures[cid] = {
                    "order_id": order_id,
                    "amount": order["amount"],
                    "payee": order["payee"],
                    "delayed": False,
                    "disbursed": True,
                    "refunded": False,
                }
            return Capture(cid, status)

        return self._idempotent(request_id, capture)

    def authorize_order(self, order_id, *, request_id) -> Authorization:
        def authorize():
            order = self.orders.get(order_id)
            if order is None or order["status"] != "APPROVED":
                raise PayPalError("ORDER_NOT_APPROVED: payer has not approved the order", status=422)
            aid = self._id("AUTH")
            order["status"] = "COMPLETED"
            self.authorizations[aid] = {
                "status": "CREATED",
                "order_id": order_id,
                "amount": order["amount"],
                "payee": order["payee"],
            }
            return Authorization(aid, "CREATED")

        return self._idempotent(request_id, authorize)

    def reauthorize(self, authorization_id, *, amount, currency, payee_merchant_id, request_id) -> Authorization:
        def reauth():
            old = self.authorizations.get(authorization_id)
            if old is None or old["status"] != "CREATED":
                raise PayPalError("AUTHORIZATION_NOT_REAUTHORIZABLE", status=422)
            self._act_for(old["payee"], payee_merchant_id)
            aid = self._id("AUTH")
            self.authorizations[aid] = {**old, "reauthorized_from": authorization_id}
            return Authorization(aid, "CREATED")

        return self._idempotent(request_id, reauth)

    def capture_authorization(
        self, authorization_id, *, amount, currency, payee_merchant_id, delayed, request_id
    ) -> Capture:
        def capture():
            auth = self.authorizations.get(authorization_id)
            if auth is None:
                raise PayPalError("RESOURCE_NOT_FOUND: authorization", status=404)
            if auth["status"] == "CAPTURED":
                raise PayPalError("AUTHORIZATION_ALREADY_CAPTURED", status=422)
            if auth["status"] == "VOIDED":
                raise PayPalError("AUTHORIZATION_VOIDED", status=422)
            if delayed and auth["payee"] is None:
                raise PayPalError("DELAYED_DISBURSEMENT_NOT_SUPPORTED: platform is the payee", status=422)
            status = self.next_capture_status
            cid = self._id("CAPTURE")
            if status in ("COMPLETED", "PENDING"):
                auth["status"] = "CAPTURED"
                self.captures[cid] = {
                    "authorization_id": authorization_id,
                    "amount": amount,
                    "payee": auth["payee"],
                    "delayed": delayed,
                    "disbursed": auth["payee"] is not None and not delayed,
                    "refunded": False,
                }
            return Capture(cid, status)

        return self._idempotent(request_id, capture)

    def void_authorization(self, authorization_id, *, payee_merchant_id, request_id) -> None:
        def void():
            auth = self.authorizations.get(authorization_id)
            if auth is None or auth["status"] != "CREATED":
                raise PayPalError("AUTHORIZATION_NOT_VOIDABLE", status=422)
            self._act_for(auth["payee"], payee_merchant_id)
            auth["status"] = "VOIDED"

        self._idempotent(request_id, void)

    # --- after capture ---

    def refund_capture(self, capture_id, *, amount, currency, payee_merchant_id, request_id) -> Refund:
        def refund():
            cap = self.captures.get(capture_id)
            if cap is None:
                raise PayPalError("RESOURCE_NOT_FOUND: capture", status=404)
            self._act_for(cap["payee"], payee_merchant_id)
            if cap["refunded"]:
                raise PayPalError("CAPTURE_FULLY_REFUNDED", status=422)
            cap["refunded"] = True
            return Refund(self._id("REFUND"), "COMPLETED")

        return self._idempotent(request_id, refund)

    def release_delayed(self, capture_id, *, request_id) -> PayoutResult:
        def release():
            cap = self.captures.get(capture_id)
            if cap is None or not cap["delayed"]:
                raise PayPalError("INVALID_REFERENCE: not a delayed-disbursement capture", status=422)
            if cap["refunded"]:
                raise PayPalError("TRANSACTION_ALREADY_REVERSED", status=422)
            if cap["disbursed"]:
                raise PayPalError("PAYOUT_ALREADY_COMPLETED_FOR_REFERENCE", status=422)
            cap["disbursed"] = True
            return PayoutResult(self._id("REFPAYOUT"), "SUCCESS")

        return self._idempotent(request_id, release)

    def send_payout(self, *, email, amount, currency, note, request_id) -> PayoutResult:
        if request_id in self._seen:  # PayPal answers a reused sender_batch_id with an error, not a replay
            raise PayPalError("PayPal 400: sender_batch_id already used", status=400, duplicate=True)

        def payout():
            bid = self._id("PAYOUT")
            self.payouts[bid] = {
                "email": email,
                "amount": amount,
                "currency": currency,
                "sender_item_id": request_id,
                "status": self.next_payout_status,
            }
            return PayoutResult(bid, self.next_payout_status)

        return self._idempotent(request_id, payout)

    def payout_status(self, batch_id) -> PayoutResult:
        p = self.payouts.get(batch_id)
        if p is None:
            raise PayPalError("RESOURCE_NOT_FOUND: payout batch", status=404)
        return PayoutResult(batch_id, p["status"])

    # --- partner onboarding ---

    def create_partner_referral(self, *, tracking_id, return_url, email) -> str:
        self._maybe_fail()
        self.referrals.setdefault(tracking_id, {})["return_url"] = return_url
        return f"{self.base_url}/mock/paypal/onboard?tracking_id={tracking_id}"

    def find_merchant(self, tracking_id) -> str | None:
        return self.referrals.get(tracking_id, {}).get("merchant_id")

    def merchant_status(self, merchant_id) -> MerchantStatus:
        status = self.merchants.get(merchant_id)
        if status is None:
            raise PayPalError("RESOURCE_NOT_FOUND: merchant", status=404)
        return status

    # --- invoices and webhooks ---

    def create_and_send_invoice(
        self, *, recipient_email, item_name, amount, currency, note, reference, request_id
    ) -> Invoice:
        def create():
            iid = self._id("INV")
            self.invoices[iid] = {
                "status": "SENT",
                "amount": amount,
                "currency": currency,
                "invoicer": "platform",
                "recipient": recipient_email,
                "reference": reference,
            }
            return Invoice(iid, f"{self.base_url}/mock/paypal/invoices/{iid}")

        return self._idempotent(request_id, create)

    def cancel_invoice(self, invoice_id, *, request_id) -> None:
        def cancel():
            inv = self.invoices.get(invoice_id)
            if inv is None or inv["status"] != "SENT":
                raise PayPalError("INVOICE_NOT_CANCELLABLE", status=422)
            inv["status"] = "CANCELLED"

        self._idempotent(request_id, cancel)

    def verify_webhook(self, headers, event) -> bool:
        return headers.get("paypal-transmission-sig") == MOCK_WEBHOOK_SIGNATURE


# --- Sandbox / live REST ---------------------------------------------------------------


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()


class SandboxPayPal:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str,
        webhook_id: str | None,
        partner_merchant_id: str | None = None,
        bn_code: str | None = None,
        timeout: float = 20.0,
    ):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._client_id = client_id
        self._auth = (client_id, client_secret)
        self._webhook_id = webhook_id
        self._partner_id = partner_merchant_id
        self._bn_code = bn_code
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

    def _assertion(self, payee_merchant_id: str | None) -> dict[str, str]:
        """PayPal-Auth-Assertion: act on a connected payee's authorization or capture (unsigned JWT, per PayPal)."""
        if not payee_merchant_id:
            return {}
        claims = {"iss": self._client_id, "payer_id": payee_merchant_id}
        return {"PayPal-Auth-Assertion": f"{_b64({'alg': 'none'})}.{_b64(claims)}."}

    def _call(
        self,
        method: str,
        path: str,
        *,
        request_id: str | None = None,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        h = {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            **(headers or {}),
        }
        if request_id:
            h["PayPal-Request-Id"] = request_id
        if self._bn_code:
            h["PayPal-Partner-Attribution-Id"] = self._bn_code
        body = None if method == "GET" else (json if json is not None else {})
        try:
            r = self._client.request(method, path, headers=h, json=body, params=params)
        except httpx.HTTPError as e:
            raise PayPalError(f"PayPal unreachable: {e}", ambiguous=True) from e
        if r.status_code >= 400:
            debug_id = r.headers.get("PayPal-Debug-Id")
            try:
                err = r.json()
                first = (err.get("details") or [{}])[0]
                detail = first.get("issue") or err.get("name") or err.get("message")
                # Payouts: "Batch with given sender_batch_id already exists" (seen in sandbox)
                duplicate = "sender_batch_id" in f"{first} {err.get('message', '')}"
            except ValueError:
                detail, duplicate = r.text[:200], False
            raise PayPalError(
                f"PayPal {r.status_code}: {detail}",
                status=r.status_code,
                ambiguous=r.status_code >= 500,
                debug_id=debug_id,
                duplicate=duplicate and r.status_code == 400,
            )
        return r.json() if r.content else {}

    # --- orders and authorizations ---

    def create_authorize_order(
        self, *, reference_id, description, amount, currency, payee_merchant_id, return_url, cancel_url, request_id
    ) -> CreatedOrder:
        return self._create_order(
            "AUTHORIZE",
            reference_id,
            description,
            amount,
            currency,
            payee_merchant_id,
            return_url,
            cancel_url,
            request_id,
        )

    def create_payment_order(
        self, *, reference_id, description, amount, currency, payee_merchant_id, return_url, cancel_url, request_id
    ) -> CreatedOrder:
        return self._create_order(
            "CAPTURE",
            reference_id,
            description,
            amount,
            currency,
            payee_merchant_id,
            return_url,
            cancel_url,
            request_id,
        )

    def capture_order(self, order_id, *, request_id) -> Capture:
        data = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", request_id=request_id)
        capture = data["purchase_units"][0]["payments"]["captures"][0]
        return Capture(capture["id"], capture["status"])

    def _create_order(
        self, intent, reference_id, description, amount, currency, payee_merchant_id, return_url, cancel_url, request_id
    ) -> CreatedOrder:
        unit = {
            "reference_id": reference_id,
            "custom_id": reference_id,
            "description": description[:127],
            "amount": _money(amount, currency),
        }
        if payee_merchant_id:
            unit["payee"] = {"merchant_id": payee_merchant_id}
        body = {
            "intent": intent,
            "purchase_units": [unit],
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

    def reauthorize(self, authorization_id, *, amount, currency, payee_merchant_id, request_id) -> Authorization:
        data = self._call(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/reauthorize",
            request_id=request_id,
            json={"amount": _money(amount, currency)},
            headers=self._assertion(payee_merchant_id),
        )
        return Authorization(data["id"], data["status"])

    def capture_authorization(
        self, authorization_id, *, amount, currency, payee_merchant_id, delayed, request_id
    ) -> Capture:
        body: dict = {"amount": _money(amount, currency), "final_capture": True}
        if delayed:
            body["payment_instruction"] = {"disbursement_mode": "DELAYED"}
        data = self._call(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/capture",
            request_id=request_id,
            json=body,
            headers=self._assertion(payee_merchant_id),
        )
        return Capture(data["id"], data["status"])

    def void_authorization(self, authorization_id, *, payee_merchant_id, request_id) -> None:
        self._call(
            "POST",
            f"/v2/payments/authorizations/{authorization_id}/void",
            request_id=request_id,
            headers=self._assertion(payee_merchant_id),
        )

    # --- after capture ---

    def refund_capture(self, capture_id, *, amount, currency, payee_merchant_id, request_id) -> Refund:
        data = self._call(
            "POST",
            f"/v2/payments/captures/{capture_id}/refund",
            request_id=request_id,
            json={"amount": _money(amount, currency)},
            headers=self._assertion(payee_merchant_id),
        )
        return Refund(data["id"], data["status"])

    def release_delayed(self, capture_id, *, request_id) -> PayoutResult:
        data = self._call(
            "POST",
            "/v1/payments/referenced-payouts-items",
            request_id=request_id,
            json={"reference_id": capture_id, "reference_type": "TRANSACTION_ID"},
        )
        state = (data.get("processing_state") or {}).get("status", "PENDING")
        return PayoutResult(data.get("item_id", ""), state if state in ("SUCCESS", "PENDING") else "FAILED")

    def send_payout(self, *, email, amount, currency, note, request_id) -> PayoutResult:
        data = self._call(
            "POST",
            "/v1/payments/payouts",
            request_id=request_id,
            json={
                "sender_batch_header": {"sender_batch_id": request_id, "email_subject": "You've been paid"},
                "items": [
                    {
                        "recipient_type": "EMAIL",
                        "receiver": email,
                        "amount": {"value": f"{amount:.2f}", "currency": currency},
                        "sender_item_id": request_id,
                        "note": note[:4000],
                    }
                ],
            },
        )
        batch_id = data["batch_header"]["payout_batch_id"]
        return PayoutResult(batch_id, "PENDING")  # items are processed asynchronously; see payout_status

    def payout_status(self, batch_id) -> PayoutResult:
        data = self._call("GET", f"/v1/payments/payouts/{batch_id}")
        items = data.get("items") or []
        raw = items[0].get("transaction_status") if items else None
        if raw is None:
            return PayoutResult(batch_id, "PENDING")
        return PayoutResult(batch_id, PAYOUT_ITEM_STATUS.get(raw, "FAILED"))

    # --- partner onboarding ---

    def _partner(self) -> str:
        """The platform's own merchant id. Configured, or read once from an unapproved order (which PayPal
        returns with the platform as payee; it costs nothing and expires unused)."""
        if self._partner_id is None:
            data = self._call(
                "POST",
                "/v2/checkout/orders",
                json={"intent": "CAPTURE", "purchase_units": [{"amount": {"currency_code": "USD", "value": "1.00"}}]},
            )
            self._partner_id = data["purchase_units"][0]["payee"]["merchant_id"]
        return self._partner_id

    def create_partner_referral(self, *, tracking_id, return_url, email) -> str:
        body: dict = {
            "tracking_id": tracking_id,
            "operations": [
                {
                    "operation": "API_INTEGRATION",
                    "api_integration_preference": {
                        "rest_api_integration": {
                            "integration_method": "PAYPAL",
                            "integration_type": "THIRD_PARTY",
                            "third_party_details": {"features": ["PAYMENT", "REFUND", "DELAY_FUNDS_DISBURSEMENT"]},
                        }
                    },
                }
            ],
            "products": ["EXPRESS_CHECKOUT"],
            "legal_consents": [{"type": "SHARE_DATA_CONSENT", "granted": True}],
            "partner_config_override": {"return_url": return_url},
        }
        if email:
            body["email"] = email
        data = self._call("POST", "/v2/customer/partner-referrals", json=body)
        url = next((link["href"] for link in data.get("links", []) if link["rel"] == "action_url"), None)
        if url is None:
            raise PayPalError("PayPal returned no onboarding link")
        return url

    def find_merchant(self, tracking_id) -> str | None:
        try:
            data = self._call(
                "GET",
                f"/v1/customer/partners/{self._partner()}/merchant-integrations",
                params={"tracking_id": tracking_id},
            )
        except PayPalError as e:
            if e.status == 404:
                return None
            raise
        return data.get("merchant_id")

    def merchant_status(self, merchant_id) -> MerchantStatus:
        data = self._call("GET", f"/v1/customer/partners/{self._partner()}/merchant-integrations/{merchant_id}")
        scopes = {
            scope
            for integration in data.get("oauth_integrations", [])
            for grant in integration.get("oauth_third_party", [])
            if grant.get("partner_client_id") == self._client_id
            for scope in grant.get("scopes", [])
        }
        return MerchantStatus(
            merchant_id,
            bool(data.get("payments_receivable")),
            bool(data.get("primary_email_confirmed")),
            all(any(s.endswith(required) for s in scopes) for required in REQUIRED_SCOPES),
        )

    # --- invoices and webhooks ---

    def create_and_send_invoice(
        self, *, recipient_email, item_name, amount, currency, note, reference, request_id
    ) -> Invoice:
        """Sent from the platform's own account. Invoicing for a payee would need the INVOICE_READ_WRITE partner
        feature, which PayPal enables per app (sandbox: FEATURES_UNAUTHORIZED); not available self-serve."""
        body = {
            "detail": {
                "currency_code": currency,
                "note": note[:4000],
                "reference": reference[:120],
                "payment_term": {"term_type": "DUE_ON_RECEIPT"},
            },
            "primary_recipients": [{"billing_info": {"email_address": recipient_email}}],
            "items": [
                {
                    "name": item_name[:200],
                    "quantity": "1",
                    "unit_amount": _money(amount, currency),
                    "unit_of_measure": "QUANTITY",
                }
            ],
        }
        data = self._call("POST", "/v2/invoicing/invoices", request_id=request_id, json=body)
        invoice_id = data.get("id") or data.get("href", "").rsplit("/", 1)[-1]
        if not invoice_id:
            raise PayPalError("PayPal returned no invoice id")
        self._call(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/send",
            request_id=f"{request_id}-send",
            json={"send_to_invoicer": False, "send_to_recipient": True},
        )
        sent = self._call("GET", f"/v2/invoicing/invoices/{invoice_id}")
        return Invoice(invoice_id, (sent.get("detail", {}).get("metadata") or {}).get("recipient_view_url"))

    def cancel_invoice(self, invoice_id, *, request_id) -> None:
        self._call(
            "POST",
            f"/v2/invoicing/invoices/{invoice_id}/cancel",
            request_id=request_id,
            json={"send_to_recipient": True, "send_to_invoicer": False},
        )

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


def make_gateway(settings) -> PayPalGateway:
    """Mock in mock mode, PayPal REST (sandbox by default) otherwise."""
    from app.config import Mode

    if settings.mode is Mode.mock:
        return MockPayPal(base_url=settings.public_base_url)
    return SandboxPayPal(
        client_id=settings.paypal_client_id,
        client_secret=settings.paypal_client_secret,
        base_url=settings.paypal_base_url,
        webhook_id=settings.paypal_webhook_id,
        partner_merchant_id=settings.paypal_partner_merchant_id,
        bn_code=settings.paypal_bn_code,
    )
