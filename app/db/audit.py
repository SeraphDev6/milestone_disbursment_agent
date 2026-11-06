"""The audit log, and the outbound-webhook outbox it feeds in the same transaction.

Tamper evidence: every event's `hash` is SHA-256 over its own fields and the previous event's hash on the same
agreement, so the log is a chain per agreement. `verify_chain` recomputes it; a row edited, removed or inserted
after the fact breaks the chain from that point. Events written before the chain existed have no hash until
`seal_chain` (python -m app.jobs seal-audit) hashes them in order.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, col, select

from app.db.session import new_id, utcnow
from app.models import AuditEvent, OutboxMessage, Subscription


def _canonical(e: AuditEvent, prev_hash: str | None) -> bytes:
    at = e.at if isinstance(e.at, datetime) else datetime.fromisoformat(str(e.at))
    body = {
        "id": e.id,
        "agreement_id": e.agreement_id,
        "milestone_id": e.milestone_id,
        "actor": e.actor,
        "action": e.action,
        "from_status": e.from_status,
        "to_status": e.to_status,
        "detail": e.detail or {},
        "at": at.replace(tzinfo=None).isoformat(timespec="microseconds"),
        "prev_hash": prev_hash,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def event_hash(e: AuditEvent, prev_hash: str | None) -> str:
    return hashlib.sha256(_canonical(e, prev_hash)).hexdigest()


def _chain(session: Session, agreement_id: str) -> list[AuditEvent]:
    """The agreement's events in chain order: by time, then id (so ties are stable)."""
    return list(
        session.exec(
            select(AuditEvent)
            .where(AuditEvent.agreement_id == agreement_id)
            .order_by(col(AuditEvent.at), col(AuditEvent.id))
        ).all()
    )


@dataclass
class ChainReport:
    agreement_id: str
    events: int = 0
    sealed: int = 0
    unsealed: int = 0  # written before the chain existed; seal_chain fixes them
    broken_at: list[str] = field(default_factory=list)  # event ids whose hash or link doesn't recompute
    head: str | None = None  # the last sealed hash: quote it in a packet, compare it later

    @property
    def status(self) -> str:
        if self.broken_at:
            return "broken"
        return "unsealed" if self.unsealed else "verified"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "events": self.events,
            "sealed": self.sealed,
            "unsealed": self.unsealed,
            "broken_at": self.broken_at,
            "head": self.head,
        }


def verify_chain(session: Session, agreement_id: str) -> ChainReport:
    """Recompute every sealed event's hash and link; a mismatch names the event where the chain breaks."""
    report = ChainReport(agreement_id=agreement_id)
    prev: str | None = None
    for e in _chain(session, agreement_id):
        report.events += 1
        if e.hash is None:
            report.unsealed += 1
            continue
        report.sealed += 1
        if e.prev_hash != prev or event_hash(e, e.prev_hash) != e.hash:
            report.broken_at.append(e.id)
        prev = e.hash
        report.head = e.hash
    return report


def seal_chain(session: Session, agreement_id: str) -> int:
    """Hash the events that have none, in order, continuing from the last sealed one. Returns how many."""
    prev: str | None = None
    sealed = 0
    for e in _chain(session, agreement_id):
        if e.hash is None:
            e.prev_hash = prev
            e.hash = event_hash(e, prev)
            session.add(e)
            sealed += 1
        prev = e.hash
    session.commit()
    return sealed


def event_type_for(action: str, milestone_id: str | None, to_status: str | None) -> str:
    scope = "milestone" if milestone_id else "agreement"
    return f"{scope}.status_changed" if to_status else f"{scope}.{action}"


def subscribed(events: list[str], event_type: str) -> bool:
    return any(e == "*" or e == event_type or (e.endswith(".") and event_type.startswith(e)) for e in events)


def record_audit(
    session: Session,
    agreement_id: str,
    actor: str,
    action: str,
    *,
    milestone_id: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    detail: dict | None = None,
) -> None:
    event = AuditEvent(
        id=new_id(),
        agreement_id=agreement_id,
        milestone_id=milestone_id,
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=to_status,
        detail=detail or {},
        at=utcnow(),
    )
    # Chain it to the agreement's latest event (pending ones in this session included, via autoflush).
    last = session.exec(
        select(AuditEvent)
        .where(AuditEvent.agreement_id == agreement_id)
        .order_by(col(AuditEvent.at).desc(), col(AuditEvent.id).desc())
    ).first()
    event.prev_hash = last.hash if last is not None else None
    event.hash = event_hash(event, event.prev_hash)
    session.add(event)

    # Outbound webhooks: queued in the same transaction, so subscribers never hear about a rolled-back change.
    event_type = event_type_for(action, milestone_id, to_status)
    subs = session.exec(
        select(Subscription).where(Subscription.agreement_id == agreement_id, col(Subscription.active).is_(True))
    ).all()
    for sub in subs:
        if subscribed(sub.events, event_type):
            session.add(
                OutboxMessage(
                    subscription_id=sub.id,
                    event_type=event_type,
                    payload={
                        "id": event.id,
                        "type": event_type,
                        "created_at": event.at.isoformat(),
                        "agreement_id": agreement_id,
                        "milestone_id": milestone_id,
                        "data": {
                            "action": action,
                            "actor": actor,
                            "from_status": from_status,
                            "to_status": to_status,
                            "detail": detail or {},
                        },
                    },
                )
            )
