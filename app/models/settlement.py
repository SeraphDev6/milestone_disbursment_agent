"""Money: settlement attempts and the ledger that mirrors PayPal."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type, fk, id_field, timestamp, utcnow
from app.models.enums import DisbursementKind, MilestoneStatus, SettlementMode, SettlementStatus

if TYPE_CHECKING:
    from app.models.agreements import Milestone


class SettlementBase(SQLModel):
    attempt: int = Field(ge=1, description="Attempt number; part of the PayPal idempotency key.")
    mode: SettlementMode = Field(sa_type=enum_type(SettlementMode), description="Capture of a hold, or an invoice.")
    idempotency_key: str = Field(
        max_length=80, unique=True, description="'{milestone_id}-{attempt}'; sent as PayPal-Request-Id."
    )
    paypal_ref: str | None = Field(
        default=None, max_length=64, index=True, description="Capture id, invoice id, or pay-now order id."
    )
    status: SettlementStatus = Field(
        sa_type=enum_type(SettlementStatus, 16), description="Mirrored from PayPal responses and webhooks."
    )


class Settlement(SettlementBase, table=True):
    __tablename__ = "settlements"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    error: str | None = Field(default=None, max_length=500)
    payer_url: str | None = Field(default=None, max_length=500)  # invoice mode: PayPal's invoice page for the payer
    created_at: datetime = timestamp()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: "Milestone" = Relationship(back_populates="settlements")


class SettlementRead(SettlementBase):
    id: str
    milestone_status: MilestoneStatus
    pay_url: str | None = Field(
        default=None, description="Invoice mode: send the payer here to pay this tranche (while it's open)."
    )


class DisbursementBase(SQLModel):
    """After a capture: paying the payee (referenced payout or Payouts) or refunding the payer."""

    kind: DisbursementKind = Field(sa_type=enum_type(DisbursementKind, 24))
    attempt: int = Field(ge=1)
    idempotency_key: str = Field(max_length=80, unique=True, description="Sent as PayPal-Request-Id.")
    paypal_ref: str | None = Field(
        default=None, max_length=128, index=True, description="Payout item, payout batch or refund id."
    )
    status: SettlementStatus = Field(sa_type=enum_type(SettlementStatus, 16))


class Disbursement(DisbursementBase, table=True):
    __tablename__ = "disbursements"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = timestamp()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: "Milestone" = Relationship(back_populates="disbursements")


class DisbursementRead(DisbursementBase):
    id: str
    milestone_status: MilestoneStatus


class LedgerEntry(SQLModel, table=True):
    """Mirrors PayPal money objects per agreement. Append-only."""

    __tablename__ = "ledger"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    milestone_id: str | None = Field(default=None, max_length=36, index=True)
    # authorized, captured, voided, expired (holds); disbursed, refunded (after capture);
    # invoiced, paid (to the payee), collected (into the platform, paid out after), invoice_cancelled
    kind: str = Field(max_length=24)
    amount: Decimal = Field(max_digits=14, decimal_places=2)
    paypal_ref: str = Field(max_length=64)
    at: datetime = timestamp()
