"""The record and the wiring: audit log, inbound webhook dedupe, integrations, outbound subscriptions."""

from datetime import datetime

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel

from app.models.columns import fk, id_field, timestamp


class AuditEventBase(SQLModel):
    milestone_id: str | None = Field(default=None, max_length=36, index=True)
    actor: str = Field(max_length=80, description="party:<id>, model, webhook:paypal, github:<user> or system.")
    action: str = Field(max_length=64)
    from_status: str | None = Field(default=None, max_length=32)
    to_status: str | None = Field(default=None, max_length=32)
    detail: dict = Field(default_factory=dict, sa_type=JSON)
    at: datetime = timestamp()


class AuditEvent(AuditEventBase, table=True):
    """Append-only. Never updated or deleted."""

    __tablename__ = "audit_events"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")


class AuditEventRead(AuditEventBase):
    pass


class WebhookEvent(SQLModel, table=True):
    """Inbound deliveries already processed (PayPal event ids, GitHub delivery ids), for dedupe."""

    __tablename__ = "webhook_events"

    id: str = Field(primary_key=True, max_length=80)
    event_type: str = Field(max_length=80)
    resource_id: str | None = Field(default=None, max_length=64)
    outcome: str = Field(max_length=200)
    received_at: datetime = timestamp()


class Integration(SQLModel, table=True):
    """An evidence source registered on an agreement, e.g. a GitHub repo webhook."""

    __tablename__ = "integrations"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    kind: str = Field(max_length=16)  # github
    target: str = Field(max_length=200)  # e.g. "owner/repo"
    # HMAC secret the source signs deliveries with. Must be stored readable to verify signatures.
    secret: str = Field(max_length=100)
    created_by: str = Field(max_length=80)
    created_at: datetime = timestamp()


class SubscriptionBase(SQLModel):
    url: str = Field(max_length=1000, description="HTTPS endpoint that receives signed JSON events.")
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        sa_type=JSON,
        description="Event types (milestone.status_changed), prefixes ending in '.' (milestone.), or '*'.",
    )


class Subscription(SubscriptionBase, table=True):
    """An outbound webhook a party registered for an agreement's events."""

    __tablename__ = "subscriptions"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    secret: str = Field(max_length=100)  # HMAC key; readable because we sign with it
    created_by: str = Field(max_length=80)
    active: bool = True
    created_at: datetime = timestamp()


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionRead(SubscriptionBase):
    id: str
    active: bool
    created_at: datetime


class SubscriptionCreated(SubscriptionRead):
    secret: str = Field(description="Shown once. Verify X-MDE-Signature with HMAC-SHA256(secret, ts + '.' + body).")


class OutboxMessage(SQLModel, table=True):
    """One pending outbound delivery, written in the same transaction as the change it announces."""

    __tablename__ = "outbox"

    id: str = id_field()
    subscription_id: str | None = fk("subscriptions.id")
    event_type: str = Field(max_length=80)
    payload: dict = Field(default_factory=dict, sa_type=JSON)
    status: str = Field(default="pending", max_length=16, index=True)  # pending, delivered, dead
    attempts: int = 0
    next_attempt_at: datetime = timestamp()
    last_error: str | None = Field(default=None, max_length=500)
    delivered_at: datetime | None = timestamp(optional=True)
    created_at: datetime = timestamp()
