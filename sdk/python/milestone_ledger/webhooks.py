"""Verify the engine's outbound webhooks and read their events.

Signature scheme (app/services/outbound.py in the engine):
    X-MDE-Signature = "sha256=" + hex(HMAC-SHA256(secret, X-MDE-Timestamp + "." + raw body))
    X-MDE-Timestamp = Unix seconds when the delivery was signed
    X-MDE-Event     = the event type (also in the body)
    X-MDE-Delivery  = unique per delivery attempt

Verify on the raw bytes exactly as received: any re-serialisation of the JSON changes the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MILESTONE_STATUSES: tuple[str, ...] = (
    "pending",
    "funded",
    "evidence_received",
    "verifying",
    "needs_review",
    "verified",
    "settling",
    "held",
    "contested",
    "settled",
    "refunded",
    "settlement_failed",
    "payout_failed",
    "disputed",
    "overdue",
)
AGREEMENT_STATUSES: tuple[str, ...] = ("draft", "awaiting_confirmation", "active", "completed", "cancelled")

SIGNATURE_HEADER = "X-MDE-Signature"
TIMESTAMP_HEADER = "X-MDE-Timestamp"
EVENT_HEADER = "X-MDE-Event"
DELIVERY_HEADER = "X-MDE-Delivery"


class WebhookVerificationError(ValueError):
    """The delivery can't be trusted. `code` is stable; the message says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EventData:
    action: str
    actor: str
    from_status: str | None = None
    to_status: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WebhookEvent:
    id: str
    type: str
    created_at: str
    agreement_id: str
    milestone_id: str | None
    data: EventData
    delivery_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def scope(self) -> str:
        """'milestone' or 'agreement'."""
        return self.type.split(".", 1)[0]

    @property
    def action(self) -> str:
        return self.data.action

    @property
    def is_status_change(self) -> bool:
        return self.type.endswith(".status_changed")


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    """Case-insensitive lookup that works with dicts, framework header objects and WSGI-style mappings."""
    for candidate in (name, name.lower(), name.upper(), "HTTP_" + name.upper().replace("-", "_")):
        try:
            value = headers.get(candidate)  # type: ignore[union-attr]
        except AttributeError:
            value = None
        if value is not None:
            return value if isinstance(value, str) else str(value[0] if isinstance(value, list) else value)
    lowered = name.lower()
    for key in headers:
        if str(key).lower() == lowered:
            value = headers[key]
            return value if isinstance(value, str) else str(value[0] if isinstance(value, list) else value)
    return None


def expected_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def verify_webhook(
    raw_body: bytes | str,
    headers: Mapping[str, Any],
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> WebhookEvent:
    """Check the signature and timestamp of one delivery and return its event.

    raw_body: the request body exactly as received (bytes; a str is encoded as UTF-8).
    headers: the request headers (any case).
    secret: the subscription's secret, shown once when it was created.
    tolerance_seconds: how old (or how far in the future) a delivery may be; retries carry a fresh timestamp.
    """
    body = raw_body.encode() if isinstance(raw_body, str) else bytes(raw_body)
    signature = _header(headers, SIGNATURE_HEADER)
    timestamp = _header(headers, TIMESTAMP_HEADER)
    if not signature:
        raise WebhookVerificationError("missing_signature", f"no {SIGNATURE_HEADER} header")
    if not timestamp:
        raise WebhookVerificationError("missing_timestamp", f"no {TIMESTAMP_HEADER} header")
    if not timestamp.isdigit():
        raise WebhookVerificationError("bad_timestamp", f"{TIMESTAMP_HEADER} is not Unix seconds: {timestamp!r}")
    current = time.time() if now is None else now
    age = current - int(timestamp)
    if abs(age) > tolerance_seconds:
        raise WebhookVerificationError(
            "timestamp_out_of_tolerance",
            f"delivery signed {int(age)}s ago; more than {tolerance_seconds}s (a replay, or clocks are off)",
        )
    if not hmac.compare_digest(signature, expected_signature(secret, timestamp, body)):
        raise WebhookVerificationError("signature_mismatch", "signature doesn't match the body and secret")
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise WebhookVerificationError("bad_json", f"body is not JSON: {e}") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
        raise WebhookVerificationError("bad_json", "body is not an event object")
    announced = _header(headers, EVENT_HEADER)
    if announced and announced != payload["type"]:
        raise WebhookVerificationError(
            "event_type_mismatch", f"{EVENT_HEADER} says {announced!r} but the body is {payload['type']!r}"
        )
    data = payload.get("data") or {}
    return WebhookEvent(
        id=str(payload.get("id", "")),
        type=payload["type"],
        created_at=str(payload.get("created_at", "")),
        agreement_id=str(payload.get("agreement_id", "")),
        milestone_id=payload.get("milestone_id"),
        data=EventData(
            action=str(data.get("action", "")),
            actor=str(data.get("actor", "")),
            from_status=data.get("from_status"),
            to_status=data.get("to_status"),
            detail=dict(data.get("detail") or {}),
        ),
        delivery_id=_header(headers, DELIVERY_HEADER),
        raw=payload,
    )
