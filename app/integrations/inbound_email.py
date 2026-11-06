"""Inbound email as an evidence source (Resend receiving).

Resend delivers a signed `email.received` webhook with the envelope and attachment metadata only; the body and the
attachments are read back from its API. `ResendInbox` does that; `MockInbox` holds messages tests put there. The
adapter that turns a message into evidence is app.api.email_evidence.

Webhook signatures are Svix's: `svix-signature` carries `v1,<base64 HMAC-SHA256>` over "<svix-id>.<svix-timestamp>.<body>"
with the secret's base64 payload (after the `whsec_` prefix) as the key; the timestamp must be within five minutes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

RESEND_API = "https://api.resend.com"
SIGNATURE_TOLERANCE = 300


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ReceivedMail:
    id: str
    sender: str  # the address only, lower-case
    recipients: tuple[str, ...]  # to, cc and received_for, lower-case addresses
    subject: str
    text: str
    attachments: tuple[Attachment, ...] = ()


class Inbox(Protocol):
    def fetch(self, email_id: str) -> ReceivedMail: ...


def address_of(value: str) -> str:
    """'Name <a@b>' or 'a@b' -> 'a@b'."""
    v = value.strip()
    if "<" in v and v.endswith(">"):
        v = v[v.rindex("<") + 1 : -1]
    return v.strip().lower()


def verify_svix(secret: str, headers: dict[str, str], body: bytes, now: float | None = None) -> bool:
    """Standard Svix verification, as Resend's webhooks use it."""
    lowered = {k.lower(): v for k, v in headers.items()}
    msg_id, ts, sigs = lowered.get("svix-id"), lowered.get("svix-timestamp"), lowered.get("svix-signature")
    if not (msg_id and ts and sigs and ts.isdigit()):
        return False
    if abs((now if now is not None else time.time()) - int(ts)) > SIGNATURE_TOLERANCE:
        return False
    key = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        raw = base64.b64decode(key)
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(raw, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    for candidate in sigs.split(" "):
        version, _, sig = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


def sign_svix(secret: str, msg_id: str, ts: str, body: bytes) -> dict[str, str]:
    """What Resend would send (tests, and the local relay)."""
    key = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    sig = base64.b64encode(hmac.new(base64.b64decode(key), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest())
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": "v1," + sig.decode()}


class ResendInbox:
    """Reads a received email and its attachments from Resend."""

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(
            base_url=RESEND_API, headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0, transport=transport
        )

    def fetch(self, email_id: str) -> ReceivedMail:
        r = self._client.get(f"/emails/receiving/{email_id}")
        r.raise_for_status()
        mail = r.json()
        attachments = []
        listing = self._client.get(f"/emails/receiving/{email_id}/attachments", params={"limit": 100})
        listing.raise_for_status()
        for item in listing.json().get("data", []):
            url = item.get("download_url")
            if not url:
                continue
            blob = httpx.get(url, timeout=60.0, follow_redirects=True)
            blob.raise_for_status()
            attachments.append(
                Attachment(item.get("filename") or "attachment", item.get("content_type") or "", blob.content)
            )
        recipients = [
            address_of(x) for x in (mail.get("to") or []) + (mail.get("cc") or []) + (mail.get("received_for") or [])
        ]
        return ReceivedMail(
            id=email_id,
            sender=address_of(mail.get("from") or ""),
            recipients=tuple(recipients),
            subject=mail.get("subject") or "",
            text=mail.get("text") or "",
            attachments=tuple(attachments),
        )


@dataclass
class MockInbox:
    """Tests and the dev server: messages put here are what `fetch` returns."""

    messages: dict[str, ReceivedMail] = field(default_factory=dict)

    def fetch(self, email_id: str) -> ReceivedMail:
        if email_id not in self.messages:
            raise httpx.HTTPError(f"no received email {email_id}")
        return self.messages[email_id]
