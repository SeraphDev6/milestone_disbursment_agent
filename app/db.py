"""Persistence. Tables are created at startup for the hackathon; move to Alembic before real data."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import Request
from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AgreementRow(Base):
    __tablename__ = "agreements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str] = mapped_column(String(3))
    settlement_mode: Mapped[str] = mapped_column(String(32))
    auto_release_enabled: Mapped[bool] = mapped_column(default=False)
    auto_release_threshold: Mapped[float] = mapped_column(Float)
    criteria_hash: Mapped[str | None] = mapped_column(String(64))  # set when locked at activation
    source_document_uri: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    parties: Mapped[list["PartyRow"]] = relationship(back_populates="agreement", cascade="all, delete-orphan")
    milestones: Mapped[list["MilestoneRow"]] = relationship(
        back_populates="agreement", cascade="all, delete-orphan", order_by="MilestoneRow.position"
    )


class PartyRow(Base):
    __tablename__ = "parties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    paypal_email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(120))
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    confirmed_criteria_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agreement: Mapped[AgreementRow] = relationship(back_populates="parties")


class MilestoneRow(Base):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    due_date: Mapped[date | None] = mapped_column(Date)
    criteria: Mapped[list[dict]] = mapped_column(JSON)  # list of Criterion.model_dump(mode="json")
    status: Mapped[str] = mapped_column(String(32))
    paypal_order_id: Mapped[str | None] = mapped_column(String(64), index=True)  # latest funding order
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(64), index=True)  # current live hold
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # original authorization time
    reauthorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agreement: Mapped[AgreementRow] = relationship(back_populates="milestones")
    settlements: Mapped[list["SettlementRow"]] = relationship(
        back_populates="milestone", order_by="SettlementRow.attempt", cascade="all, delete-orphan"
    )
    evidence: Mapped[list["EvidenceRow"]] = relationship(
        order_by="EvidenceRow.received_at", cascade="all, delete-orphan"
    )
    verdicts: Mapped[list["VerdictRow"]] = relationship(order_by="VerdictRow.created_at", cascade="all, delete-orphan")


class EvidenceRow(Base):
    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("milestone_id", "source", "external_ref", "pinned_ref"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    submitted_by: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(16))
    external_ref: Mapped[str] = mapped_column(String(500))
    pinned_ref: Mapped[str] = mapped_column(String(80))  # commit SHA or content hash
    evidence_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    dated: Mapped[date | None] = mapped_column(Date)
    artifact_uris: Mapped[list[str]] = mapped_column(JSON, default=list)
    check_results: Mapped[dict] = mapped_column(JSON, default=dict)  # set by adapters only
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VerdictRow(Base):
    """Append-only. Everything needed to replay the decision."""

    __tablename__ = "verdicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    criteria_hash: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON)
    criteria_results: Mapped[list[dict]] = mapped_column(JSON)
    met: Mapped[bool]
    model_confidence: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    is_precheck: Mapped[bool] = mapped_column(default=False)
    gate: Mapped[str] = mapped_column(String(32))  # auto_release, needs_review, precheck
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SettlementRow(Base):
    __tablename__ = "settlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    milestone_id: Mapped[str] = mapped_column(ForeignKey("milestones.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)  # f"{milestone_id}-{attempt}"
    paypal_ref: Mapped[str | None] = mapped_column(String(64), index=True)  # capture id once known
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    milestone: Mapped[MilestoneRow] = relationship(back_populates="settlements")


class LedgerRow(Base):
    """Mirrors PayPal money objects per agreement. Append-only."""

    __tablename__ = "ledger"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # authorized, captured, voided, expired
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    paypal_ref: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEventRow(Base):
    """PayPal webhook events already processed, keyed by PayPal's event id (dedupe)."""

    __tablename__ = "webhook_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEventRow(Base):
    """Append-only. Never updated or deleted."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agreement_id: Mapped[str] = mapped_column(ForeignKey("agreements.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read; everything stored is UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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
    session.add(
        AuditEventRow(
            agreement_id=agreement_id,
            milestone_id=milestone_id,
            actor=actor,
            action=action,
            from_status=from_status,
            to_status=to_status,
            detail=detail or {},
        )
    )


def make_engine(url: str) -> Engine:
    if url in ("sqlite://", "sqlite:///:memory:"):
        # One shared in-memory database across threads (tests, throwaway demos).
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as session:
        yield session
