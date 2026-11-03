"""The audit log, and the outbound-webhook outbox it feeds in the same transaction."""

from sqlmodel import Session, col, select

from app.db.models import AuditEvent, OutboxMessage, Subscription
from app.db.session import new_id, utcnow


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
