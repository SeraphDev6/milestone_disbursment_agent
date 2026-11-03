"""Agreement, Party, Milestone: one definition each, used for the table, API input and API output.

XBase    shared fields, validation and descriptions (flow into OpenAPI, MCP schemas, prompts)
X        the table (table=True): XBase + database-only fields and relationships
XCreate  API input
XRead    API output
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import model_validator
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import PydanticJSON, enum_type, fk, id_field, timestamp
from app.models.criteria import Criterion, LintReport
from app.models.enums import AgreementStatus, MilestoneStatus, Role, SettlementMode

if TYPE_CHECKING:
    from app.models.evidence import Evidence, Verdict
    from app.models.settlement import Settlement


def _money(description: str) -> Any:
    return Field(gt=0, max_digits=14, decimal_places=2, description=description)


# --- Party -----------------------------------------------------------------------------


class PartyBase(SQLModel):
    role: Role = Field(sa_type=enum_type(Role, 16), description="payer, payee or approver.")
    paypal_email: str = Field(
        max_length=320,
        schema_extra={"pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]+$"},
        description="PayPal account used for funding or receiving.",
    )
    display_name: str = Field(min_length=1, max_length=120, description="Name shown in the UI and audit log.")


class Party(PartyBase, table=True):
    __tablename__ = "parties"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    api_key_hash: str = Field(max_length=64, unique=True, index=True)
    confirmed_criteria_hash: str | None = Field(default=None, max_length=64)
    confirmed_at: datetime | None = timestamp(optional=True)

    agreement: "Agreement" = Relationship(back_populates="parties")


class PartyCreate(PartyBase):
    pass


class PartyRead(PartyBase):
    id: str
    confirmed_current: bool = Field(description="Whether this party has signed the current criteria hash.")


# --- Milestone -------------------------------------------------------------------------


class MilestoneBase(SQLModel):
    """What a tranche is. These fields (and only these) are what both parties sign via the criteria hash."""

    title: str = Field(min_length=1, max_length=200, description="Short name of the tranche or deliverable.")
    amount: Decimal = _money("Amount released or invoiced when it verifies.")
    due_date: date | None = Field(
        default=None, description="Target date; passing it without evidence marks the milestone overdue."
    )
    criteria: list[Criterion] = Field(
        min_length=1,
        sa_type=PydanticJSON(list[Criterion]),
        description="Checkable conditions; all must be met to release this tranche.",
    )


class Milestone(MilestoneBase, table=True):
    __tablename__ = "milestones"

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    position: int
    status: MilestoneStatus = Field(default=MilestoneStatus.pending, sa_type=enum_type(MilestoneStatus))
    paypal_order_id: str | None = Field(default=None, max_length=64, index=True)  # latest funding order
    paypal_authorization_id: str | None = Field(default=None, max_length=64, index=True)  # current live hold
    funded_at: datetime | None = timestamp(optional=True)  # original authorization time
    reauthorized_at: datetime | None = timestamp(optional=True)

    agreement: "Agreement" = Relationship(back_populates="milestones")
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


class MilestoneCreate(MilestoneBase):
    pass


class MilestoneRead(MilestoneBase):
    id: str
    position: int
    status: MilestoneStatus
    paypal_authorization_id: str | None = Field(default=None, description="Live PayPal hold for this tranche.")
    funded_at: datetime | None = Field(default=None, description="When the original hold was placed.")


# --- Agreement -------------------------------------------------------------------------


class AgreementBase(SQLModel):
    title: str = Field(min_length=1, max_length=200, description="Human-readable name of the grant or SOW.")
    total_amount: Decimal = _money("Agreement total; milestones must sum to it.")
    currency: str = Field(
        default="USD", max_length=3, schema_extra={"pattern": r"^[A-Z]{3}$"}, description="ISO 4217 currency."
    )
    settlement_mode: SettlementMode = Field(
        sa_type=enum_type(SettlementMode),
        description="escrow_release holds funds per milestone and releases on verify; "
        "invoice_on_verify sends a PayPal invoice when a milestone verifies.",
    )
    auto_release_threshold: float = Field(
        default=0.85, ge=0, le=1, description="Minimum gate score to settle without human approval."
    )
    source_document_uri: str | None = Field(
        default=None, max_length=1000, description="Original agreement file the milestones came from."
    )


class Agreement(AgreementBase, table=True):
    __tablename__ = "agreements"

    id: str = id_field()
    status: AgreementStatus = Field(default=AgreementStatus.draft, sa_type=enum_type(AgreementStatus))
    auto_release_enabled: bool = False
    criteria_hash: str | None = Field(default=None, max_length=64)  # locked at activation
    created_at: datetime = timestamp()

    parties: list[Party] = Relationship(
        back_populates="agreement", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    milestones: list[Milestone] = Relationship(
        back_populates="agreement",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Milestone.position"},
    )


class AgreementCreate(AgreementBase):
    parties: list[PartyCreate] = Field(min_length=2, description="Exactly one payer and one payee; optional approvers.")
    milestones: list[MilestoneCreate] = Field(min_length=1, description="Tranches in payment order.")

    @model_validator(mode="after")
    def _one_payer_one_payee(self) -> "AgreementCreate":
        roles = [p.role for p in self.parties]
        if roles.count(Role.payer) != 1 or roles.count(Role.payee) != 1:
            raise ValueError("an agreement needs exactly one payer and exactly one payee")
        payer = next(p for p in self.parties if p.role is Role.payer)
        payee = next(p for p in self.parties if p.role is Role.payee)
        if payer.paypal_email.lower() == payee.paypal_email.lower():
            raise ValueError("payer and payee must be different PayPal accounts")
        return self


class FundingSummary(SQLModel):
    held: Decimal = Field(description="Escrow: authorized on the payer's PayPal account, not yet captured.")
    invoiced: Decimal = Field(description="Invoice mode: invoices sent and not yet paid.")
    released: Decimal = Field(description="Paid to the payee: captured holds plus paid invoices.")
    unfunded: Decimal = Field(description="Total minus held, invoiced and released.")


class AgreementRead(AgreementBase):
    id: str
    status: AgreementStatus
    auto_release_enabled: bool
    criteria_hash: str = Field(description="Hash of the current tranches and criteria; sign this to confirm.")
    locked: bool = Field(description="True once both parties signed and criteria can no longer change.")
    parties: list[PartyRead]
    milestones: list[MilestoneRead]
    funding: FundingSummary
    lint: LintReport
