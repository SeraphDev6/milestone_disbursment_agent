"""Database tables (SQLModel). Tables are created at startup for now; Alembic migrations come next.

Status and enum-like columns are stored as plain strings; app.domain.models holds the enums.
Append-only tables: AuditEvent, Verdict, LedgerEntry.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, Text, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from app.db.session import new_id, utcnow


def _id() -> Any:
    return Field(default_factory=new_id, primary_key=True, max_length=36)


def _fk(target: str, *, nullable: bool = False) -> Any:
    return (
        Field(default=None, foreign_key=target, index=True, max_length=36)
        if nullable
        else Field(foreign_key=target, index=True, max_length=36)
    )


def _when(*, nullable: bool = False) -> Any:
    if nullable:
        return Field(default=None, sa_type=DateTime(timezone=True))
    return Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


def _json(factory: type) -> Any:
    return Field(default_factory=factory, sa_type=JSON)


def _money() -> Any:
    return Field(max_digits=14, decimal_places=2)


class Agreement(SQLModel, table=True):
    __tablename__ = "agreements"

    id: str = _id()
    title: str = Field(max_length=200)
    total_amount: Decimal = _money()
    currency: str = Field(max_length=3)
    settlement_mode: str = Field(max_length=32)
    auto_release_enabled: bool = False
    auto_release_threshold: float
    criteria_hash: str | None = Field(default=None, max_length=64)  # set when locked at activation
    source_document_uri: str | None = Field(default=None, max_length=1000)
    status: str = Field(max_length=32)
    created_at: datetime = _when()

    parties: list["Party"] = Relationship(
        back_populates="agreement", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    milestones: list["Milestone"] = Relationship(
        back_populates="agreement",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Milestone.position"},
    )


class Party(SQLModel, table=True):
    __tablename__ = "parties"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    role: str = Field(max_length=16)
    paypal_email: str = Field(max_length=320)
    display_name: str = Field(max_length=120)
    api_key_hash: str = Field(max_length=64, unique=True, index=True)
    confirmed_criteria_hash: str | None = Field(default=None, max_length=64)
    confirmed_at: datetime | None = _when(nullable=True)

    agreement: Agreement = Relationship(back_populates="parties")


class Milestone(SQLModel, table=True):
    __tablename__ = "milestones"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    position: int
    title: str = Field(max_length=200)
    amount: Decimal = _money()
    due_date: date | None = None
    criteria: list[dict] = _json(list)  # Criterion.model_dump(mode="json") per criterion
    status: str = Field(max_length=32)
    paypal_order_id: str | None = Field(default=None, max_length=64, index=True)  # latest funding order
    paypal_authorization_id: str | None = Field(default=None, max_length=64, index=True)  # current live hold
    funded_at: datetime | None = _when(nullable=True)  # original authorization time
    reauthorized_at: datetime | None = _when(nullable=True)

    agreement: Agreement = Relationship(back_populates="milestones")
    settlements: list["Settlement"] = Relationship(
        back_populates="milestone",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Settlement.attempt"},
    )
    evidence: list["Evidence"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Evidence.received_at"}
    )
    verdicts: list["Verdict"] = Relationship(
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Verdict.created_at"}
    )


class StoredFile(SQLModel, table=True):
    """Original uploaded bytes. In the database for the hackathon; object storage later."""

    __tablename__ = "files"

    id: str = _id()
    filename: str = Field(max_length=255)
    media_type: str = Field(max_length=100)
    sha256: str = Field(max_length=64, index=True)
    size: int
    data: bytes
    created_at: datetime = _when()


class Evidence(SQLModel, table=True):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("milestone_id", "source", "external_ref", "pinned_ref"),)

    id: str = _id()
    milestone_id: str = _fk("milestones.id")
    submitted_by: str = Field(max_length=80)
    source: str = Field(max_length=16)
    external_ref: str = Field(max_length=500)
    pinned_ref: str = Field(max_length=80)  # commit SHA or content hash
    evidence_type: str = Field(max_length=16)
    title: str = Field(max_length=200)
    content: str = Field(sa_type=Text)
    dated: date | None = None
    artifact_uris: list[str] = _json(list)
    check_results: dict = _json(dict)  # set by adapters only
    file_id: str | None = _fk("files.id", nullable=True)  # original upload, if any
    received_at: datetime = _when()

    file: StoredFile | None = Relationship()


class Verdict(SQLModel, table=True):
    """Append-only. Everything needed to replay the decision."""

    __tablename__ = "verdicts"

    id: str = _id()
    milestone_id: str = _fk("milestones.id")
    criteria_hash: str = Field(max_length=64)
    evidence_ids: list[str] = _json(list)
    criteria_results: list[dict] = _json(list)
    met: bool
    model_confidence: float
    rationale: str = Field(sa_type=Text)
    model: str = Field(max_length=64)
    prompt_version: str = Field(max_length=32)
    is_precheck: bool = False
    gate: str = Field(max_length=32)  # auto_release, needs_review, precheck
    created_at: datetime = _when()


class Settlement(SQLModel, table=True):
    __tablename__ = "settlements"

    id: str = _id()
    milestone_id: str = _fk("milestones.id")
    attempt: int
    mode: str = Field(max_length=32)
    idempotency_key: str = Field(max_length=80, unique=True)  # f"{milestone_id}-{attempt}"
    paypal_ref: str | None = Field(default=None, max_length=64, index=True)  # capture or invoice id
    status: str = Field(max_length=16)
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = _when()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: Milestone = Relationship(back_populates="settlements")


class LedgerEntry(SQLModel, table=True):
    """Mirrors PayPal money objects per agreement. Append-only."""

    __tablename__ = "ledger"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    milestone_id: str | None = Field(default=None, max_length=36, index=True)
    kind: str = Field(max_length=24)  # authorized, captured, voided, expired, invoiced, paid, invoice_cancelled
    amount: Decimal = _money()
    paypal_ref: str = Field(max_length=64)
    at: datetime = _when()


class WebhookEvent(SQLModel, table=True):
    """Inbound webhook deliveries already processed (PayPal event ids, GitHub delivery ids): dedupe."""

    __tablename__ = "webhook_events"

    id: str = Field(primary_key=True, max_length=80)
    event_type: str = Field(max_length=80)
    resource_id: str | None = Field(default=None, max_length=64)
    outcome: str = Field(max_length=200)
    received_at: datetime = _when()


class Integration(SQLModel, table=True):
    """An evidence source registered on an agreement, e.g. a GitHub repo webhook."""

    __tablename__ = "integrations"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    kind: str = Field(max_length=16)  # github
    target: str = Field(max_length=200)  # e.g. "owner/repo"
    # HMAC secret the source signs deliveries with. Must be stored readable to verify signatures.
    secret: str = Field(max_length=100)
    created_by: str = Field(max_length=80)
    created_at: datetime = _when()


class Subscription(SQLModel, table=True):
    """An outbound webhook a party registered for an agreement's events."""

    __tablename__ = "subscriptions"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    url: str = Field(max_length=1000)
    events: list[str] = _json(list)  # event types or prefixes ("milestone."), or ["*"]
    secret: str = Field(max_length=100)  # HMAC key; readable because we sign with it
    created_by: str = Field(max_length=80)
    active: bool = True
    created_at: datetime = _when()


class OutboxMessage(SQLModel, table=True):
    """One pending outbound delivery, written in the same transaction as the change it announces."""

    __tablename__ = "outbox"

    id: str = _id()
    subscription_id: str = _fk("subscriptions.id")
    event_type: str = Field(max_length=80)
    payload: dict = _json(dict)
    status: str = Field(default="pending", max_length=16, index=True)  # pending, delivered, dead
    attempts: int = 0
    next_attempt_at: datetime = _when()
    last_error: str | None = Field(default=None, max_length=500)
    delivered_at: datetime | None = _when(nullable=True)
    created_at: datetime = _when()


class AuditEvent(SQLModel, table=True):
    """Append-only. Never updated or deleted."""

    __tablename__ = "audit_events"

    id: str = _id()
    agreement_id: str = _fk("agreements.id")
    milestone_id: str | None = Field(default=None, max_length=36, index=True)
    actor: str = Field(max_length=80)
    action: str = Field(max_length=64)
    from_status: str | None = Field(default=None, max_length=32)
    to_status: str | None = Field(default=None, max_length=32)
    detail: dict = _json(dict)
    at: datetime = _when()
