"""Outbound webhooks: deliver queued events to subscriber URLs, signed, with retries.

Signature: X-MDE-Signature = "sha256=" + HMAC-SHA256(secret, f"{timestamp}.{body}"), with the timestamp in
X-MDE-Timestamp so receivers can reject replays. Deliveries retry with exponential backoff and are marked
dead after MAX_ATTEMPTS.

Subscriber URLs are user input, so destinations that resolve to private, loopback, link-local or reserved
addresses are refused (server-side request forgery). Plain http is allowed only in mock mode.
"""

import hashlib
import hmac
import ipaddress
import json
import socket
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import OutboxRow, SubscriptionRow, as_utc, utcnow

MAX_ATTEMPTS = 10
BATCH = 50
TIMEOUT = 10.0

Resolver = Callable[[str], list[str]]
Poster = Callable[[str, bytes, dict[str, str]], int]


class DestinationError(ValueError):
    pass


def resolve_host(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_destination(url: str, *, allow_http: bool, resolve: Resolver = resolve_host) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and not allow_http):
        raise DestinationError("webhook URLs must use https")
    if not parts.hostname:
        raise DestinationError("webhook URL has no host")
    try:
        addresses = resolve(parts.hostname)
    except OSError as e:
        raise DestinationError(f"can't resolve {parts.hostname}") from e
    for addr in addresses:
        ip = ipaddress.ip_address(addr)
        if not ip.is_global:
            if allow_http and ip.is_loopback:
                continue  # local development in mock mode
            raise DestinationError(f"{parts.hostname} resolves to a non-public address")


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def httpx_poster(url: str, body: bytes, headers: dict[str, str]) -> int:
    # No redirects: a public URL could otherwise bounce the request to an internal one.
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
        return client.post(url, content=body, headers=headers).status_code


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(30 * 2 ** (attempts - 1), 6 * 3600))


def deliver_due(
    session: Session,
    now: datetime,
    *,
    post: Poster = httpx_poster,
    resolve: Resolver = resolve_host,
    allow_http: bool = False,
) -> int:
    """Send every due delivery once. Returns how many succeeded."""
    pending = session.scalars(
        select(OutboxRow).where(OutboxRow.status == "pending").order_by(OutboxRow.created_at).limit(BATCH * 4)
    ).all()
    # Compared in Python: SQLite drops tzinfo, so a SQL comparison against an aware `now` isn't reliable.
    due = [r for r in pending if as_utc(r.next_attempt_at) <= now][:BATCH]
    delivered = 0
    for row in due:
        sub = session.get(SubscriptionRow, row.subscription_id)
        row.attempts += 1
        if sub is None or not sub.active:
            row.status, row.last_error = "dead", "subscription removed"
            continue
        try:
            check_destination(sub.url, allow_http=allow_http, resolve=resolve)
            body = json.dumps(row.payload, separators=(",", ":"), sort_keys=True).encode()
            ts = str(int(now.timestamp()))
            status = post(
                sub.url,
                body,
                {
                    "Content-Type": "application/json",
                    "User-Agent": "milestone-disbursement-engine",
                    "X-MDE-Event": row.event_type,
                    "X-MDE-Delivery": row.id,
                    "X-MDE-Timestamp": ts,
                    "X-MDE-Signature": sign(sub.secret, ts, body),
                },
            )
            error = None if 200 <= status < 300 else f"HTTP {status}"
        except DestinationError as e:
            row.status, row.last_error = "dead", str(e)  # won't get better by retrying
            continue
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"[:500]
        if error is None:
            row.status, row.delivered_at, row.last_error = "delivered", now, None
            delivered += 1
        elif row.attempts >= MAX_ATTEMPTS:
            row.status, row.last_error = "dead", error
        else:
            row.next_attempt_at, row.last_error = now + backoff(row.attempts), error
    session.commit()
    return delivered


def start_delivery_loop(factory: sessionmaker, interval: float, allow_http: bool) -> threading.Event:
    """Background thread that delivers due webhooks every `interval` seconds. Set the returned event to stop."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                with factory() as session:
                    deliver_due(session, utcnow(), allow_http=allow_http)
            except Exception:  # noqa: BLE001 - keep the loop alive; failures are retried next tick
                time.sleep(interval)

    threading.Thread(target=loop, name="webhook-delivery", daemon=True).start()
    return stop
