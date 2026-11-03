"""Money: settlement attempts and the ledger that mirrors PayPal."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type, fk, id_field, timestamp, utcnow
from app.models.enums import MilestoneStatus, SettlementMode, SettlementStatus

if TYPE_CHECKING:
    from app.models.agreements import Milestone


class SettlementBase(SQLModel):
    attempt: int = Field(ge=1, description="Attempt number; part of the PayPal idempotency key.")
    mode: SettlementMode = Field(sa_type=enum_type(SettlementMode), description="Capture of a hold, or an invoice.")
    idempotency_key: str = Field(
        max_length=80, unique=True, description="'{milestone_id}-{attempt}'; sent as PayPal-Request-Id."
    )
    paypal_ref: str | None = Field(default=None, max_length=64, index=True, description="Capture id or invoice id.")
    status: SettlementStatus = Field(
        sa_type=enum_type(SettlementStatus, 16), description="Mirrored from PayPal responses and webhooks."
    )


class Settlement(SettlementBase, table=True):
    __tablename__ = "settlements"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = timestamp()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: "Milestone" = Relationship(back_populates="settlements")


class SettlementRead(SettlementBase):
    id: str
    milestone_status: MilestoneStatus


class LedgerEntry(SQLModel, table=True):
    """Mirrors PayPal money objects per agreement. Append-only."""

    __tablename__ = "ledger"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    milestone_id: str | None = Field(default=None, max_length=36, index=True)
    kind: str = Field(max_length=24)  # authorized, captured, voided, expired, invoiced, paid, invoice_cancelled
    amount: Decimal = Field(max_digits=14, decimal_places=2)
    paypal_ref: str = Field(max_length=64)
    at: datetime = timestamp()
