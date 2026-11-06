"""Signed, expiring pay links.

`/pay/{settlement id}` needs no sign-in: the payer's finance person may open it from an email. An unguessable id
alone would let anyone who ever saw the link create PayPal orders against the payee for as long as the request
stays open (30 days), so every link carries a signature over the settlement id and an expiry, made with the
server's SECRET_KEY. Signed-in payer-side people get a fresh link whenever they read the tranche, and can ask the
API to email a new one (POST /milestones/{id}/pay-link), so an expired link is never a dead end.

Services reach the signer through `session.info["pay_links"]`, like the notifier.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlmodel import Session

PAY_LINKS = "pay_links"
_SIG_CHARS = 32  # 128 bits of the HMAC-SHA256 hex digest


@dataclass(frozen=True)
class PayLinks:
    secret: bytes
    valid_for: timedelta

    @classmethod
    def from_settings(cls, settings) -> PayLinks:
        return cls(settings.secret_key.get_secret_value().encode(), timedelta(days=settings.pay_link_valid_days))

    def _sign(self, settlement_id: str, expires: int) -> str:
        msg = f"pay:{settlement_id}:{expires}".encode()
        return hmac.new(self.secret, msg, hashlib.sha256).hexdigest()[:_SIG_CHARS]

    def token(self, settlement_id: str, now: datetime) -> str:
        expires = int((now + self.valid_for).timestamp())
        return f"{expires}.{self._sign(settlement_id, expires)}"

    def path(self, settlement_id: str, now: datetime) -> str:
        return f"/pay/{settlement_id}?t={self.token(settlement_id, now)}"

    def check(self, settlement_id: str, token: str | None, now: datetime) -> str | None:
        """None when the link is good; otherwise why not ('expired' or 'invalid')."""
        if not token or "." not in token:
            return "invalid"
        expires_text, sig = token.split(".", 1)
        if not expires_text.isdigit() or not hmac.compare_digest(sig, self._sign(settlement_id, int(expires_text))):
            return "invalid"
        return "expired" if int(expires_text) < int(now.timestamp()) else None


def pay_links_for(session: Session) -> PayLinks | None:
    return session.info.get(PAY_LINKS)
