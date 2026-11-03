"""Outbound webhooks: deliver queued events to subscriber URLs, signed, with retries.

Signature: X-MDE-Signature = "sha256=" + HMAC-SHA256(secret, f"{timestamp}.{body}"), with the timestamp in
X-MDE-Timestamp so receivers can reject replays. Deliveries retry with exponential backoff and are marked
dead after MAX_ATTEMPTS.

Subscriber URLs are user input, so destinations that resolve to private, loopback, link-local or reserved
addresses are refused (server-side request forgery). Plain http is allowed only in mock mode. The post goes to
the address that was vetted, not to a second lookup of the name, so a DNS answer can't change between the check
and the connection (rebinding); TLS still verifies the certificate against the hostname.
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
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, select

from app.db import as_utc, utcnow
from app.models import OutboxMessage, Subscription

MAX_ATTEMPTS = 10
BATCH = 50
TIMEOUT = 10.0

Resolver = Callable[[str], list[str]]
Poster = Callable[[str, bytes, dict[str, str], str | None], int]  # url, body, headers, vetted address


class DestinationError(ValueError):
    pass


def resolve_host(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def check_destination(url: str, *, allow_http: bool, resolve: Resolver = resolve_host) -> list[str]:
    """Refuse a URL that isn't https (http in mock mode) or that resolves to a non-public address. Returns the
    vetted addresses so the caller can connect to one of them rather than resolve the name again."""
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
    if not addresses:
        raise DestinationError(f"{parts.hostname} has no address")
    return addresses


def pinned_url(url: str, address: str) -> tuple[str, str]:
    """`url` with its host replaced by `address`, plus the original host for the Host header and TLS name."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    literal = f"[{address}]" if ":" in address else address
    netloc = f"{literal}:{parts.port}" if parts.port else literal
    return parts._replace(netloc=netloc).geturl(), host


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def httpx_poster(url: str, body: bytes, headers: dict[str, str], address: str | None = None, *, transport=None) -> int:
    # No redirects: a public URL could otherwise bounce the request to an internal one. With a vetted address
    # the connection goes there; the Host header and the TLS server name stay the hostname.
    extensions = {}
    if address:
        url, host = pinned_url(url, address)
        headers = {**headers, "Host": host}
        if url.startswith("https://"):
            extensions["sni_hostname"] = host
    with httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=transport) as client:
        return client.post(url, content=body, headers=headers, extensions=extensions).status_code


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
    pending = session.exec(
        select(OutboxMessage)
        .where(OutboxMessage.status == "pending")
        .order_by(OutboxMessage.created_at)
        .limit(BATCH * 4)
    ).all()
    # Compared in Python: SQLite drops tzinfo, so a SQL comparison against an aware `now` isn't reliable.
    due = [r for r in pending if as_utc(r.next_attempt_at) <= now][:BATCH]
    delivered = 0
    for row in due:
        sub = session.get(Subscription, row.subscription_id)
        row.attempts += 1
        if sub is None or not sub.active:
            row.status, row.last_error = "dead", "subscription removed"
            continue
        try:
            addresses = check_destination(sub.url, allow_http=allow_http, resolve=resolve)
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
                addresses[0],
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


def run_every(name: str, factory: sessionmaker, interval: float, job) -> threading.Event:
    """Background thread that runs `job(session, now)` every `interval` seconds. Set the returned event to stop."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                with factory() as session:
                    job(session, utcnow())
            except Exception:  # noqa: BLE001 - keep the loop alive; failures are retried next tick
                time.sleep(interval)

    threading.Thread(target=loop, name=name, daemon=True).start()
    return stop


def start_delivery_loop(factory: sessionmaker, interval: float, allow_http: bool) -> threading.Event:
    """Deliver due webhooks every `interval` seconds."""
    return run_every(
        "webhook-delivery", factory, interval, lambda session, now: deliver_due(session, now, allow_http=allow_http)
    )
