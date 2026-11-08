"""Money: settlement attempts and the ledger that mirrors PayPal."""

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, DateTime
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import enum_type, fk, id_field, timestamp, utcnow
from app.models.enums import DisbursementKind, MilestoneStatus, SettlementMode, SettlementStatus

if TYPE_CHECKING:
    from app.models.agreements import Milestone, Party


class Hold(SQLModel, table=True):
    """One funder's PayPal authorization on one tranche. A single-payer tranche has one; a co-funded tranche has one
    per funder (and another when a funder covers part of a shortfall). The row outlives the authorization: an expired
    or voided hold keeps its share and is funded again through the same row."""

    __tablename__ = "holds"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    party_id: str | None = fk("parties.id")  # the funder
    amount: Decimal = Field(max_digits=14, decimal_places=2)  # the share this hold covers
    paypal_order_id: str | None = Field(default=None, max_length=64, index=True)  # latest funding order
    paypal_authorization_id: str | None = Field(default=None, max_length=64, index=True)  # current live hold
    funded_at: datetime | None = timestamp(optional=True)  # when this authorization was placed
    reauthorized_at: datetime | None = timestamp(optional=True)
    created_at: datetime = timestamp()

    milestone: "Milestone" = Relationship(back_populates="holds")
    party: "Party" = Relationship()
    settlements: list["Settlement"] = Relationship(back_populates="hold")

    @property
    def live(self) -> bool:
        return self.paypal_authorization_id is not None


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
    amount: Decimal = Field(
        max_digits=14,
        decimal_places=2,
        description="What this attempt captures or invoices: the whole tranche, or (partial release) the share of "
        "it the newly met criteria account for by weight.",
    )
    final: bool = Field(
        default=True,
        description="Escrow: this capture closes the hold (final_capture). False for a partial release, which leaves "
        "the remainder on hold for the criteria still to be met.",
    )
    criterion_ids: list[str] = Field(
        default_factory=list,
        sa_type=JSON,
        description="Partial release: the criteria this capture pays for. Empty for a whole-tranche release.",
    )


class Settlement(SettlementBase, table=True):
    __tablename__ = "settlements"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    hold_id: str | None = fk("holds.id", optional=True)  # escrow: the funder's hold this capture takes from
    error: str | None = Field(default=None, max_length=500)
    payer_url: str | None = Field(default=None, max_length=500)  # invoice mode: PayPal's invoice page for the payer
    created_at: datetime = timestamp()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: "Milestone" = Relationship(back_populates="settlements")
    hold: Optional["Hold"] = Relationship(back_populates="settlements")
    disbursements: list["Disbursement"] = Relationship(back_populates="settlement")


class SettlementRead(SettlementBase):
    id: str = Field(description="Settlement attempt id.")
    milestone_status: MilestoneStatus = Field(description="The tranche's status after this call.")
    pay_url: str | None = Field(
        default=None, description="Invoice mode: send the payer here to pay this tranche (while it's open)."
    )


class DisbursementBase(SQLModel):
    """After a capture: paying the payee (referenced payout or Payouts) or refunding the payer."""

    kind: DisbursementKind = Field(sa_type=enum_type(DisbursementKind, 24))
    attempt: int = Field(ge=1, description="1 for the first try; a failed payout retries with the next number.")
    idempotency_key: str = Field(max_length=80, unique=True, description="Sent as PayPal-Request-Id.")
    paypal_ref: str | None = Field(
        default=None, max_length=128, index=True, description="Payout item, payout batch or refund id."
    )
    status: SettlementStatus = Field(sa_type=enum_type(SettlementStatus, 16))
    amount: Decimal = Field(
        max_digits=14, decimal_places=2, description="The captured amount this pays out or refunds."
    )


class Disbursement(DisbursementBase, table=True):
    __tablename__ = "disbursements"

    id: str = id_field()
    milestone_id: str | None = fk("milestones.id")
    settlement_id: str | None = fk("settlements.id", optional=True)  # the capture whose money moves
    error: str | None = Field(default=None, max_length=500)
    created_at: datetime = timestamp()
    updated_at: datetime = Field(
        default_factory=utcnow, sa_type=DateTime(timezone=True), sa_column_kwargs={"onupdate": utcnow}
    )

    milestone: "Milestone" = Relationship(back_populates="disbursements")
    settlement: Optional["Settlement"] = Relationship(back_populates="disbursements")


class DisbursementRead(DisbursementBase):
    id: str = Field(description="Disbursement attempt id.")
    milestone_status: MilestoneStatus = Field(description="The tranche's status after this call.")


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
