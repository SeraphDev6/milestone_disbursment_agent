"""Persistence. Tables are created at startup for the hackathon; move to Alembic before real data."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import Request
from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, create_engine
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
    paypal_authorization_id: Mapped[str | None] = mapped_column(String(64))
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agreement: Mapped[AgreementRow] = relationship(back_populates="milestones")


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
