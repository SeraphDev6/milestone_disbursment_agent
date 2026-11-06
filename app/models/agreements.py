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
from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from app.models.columns import PydanticJSON, enum_type, fk, id_field, timestamp
from app.models.criteria import Criterion, LintReport
from app.models.enums import (
    AgreementStatus,
    DeliverableRelease,
    EvidenceVisibility,
    MilestoneStatus,
    PayoutRoute,
    Role,
    SettlementMode,
)
from app.models.identity import EMAIL_PATTERN

if TYPE_CHECKING:
    from app.models.evidence import Evidence, Verdict
    from app.models.settlement import Disbursement, Settlement


def _money(description: str) -> Any:
    return Field(gt=0, max_digits=14, decimal_places=2, description=description)


# --- Party -----------------------------------------------------------------------------


class PartyBase(SQLModel):
    """An organization's seat on one agreement."""

    role: Role = Field(sa_type=enum_type(Role, 16), description="payer, payee or reviewer on this agreement.")
    paypal_email: str | None = Field(
        default=None,
        max_length=320,
        description="PayPal account used on this agreement (snapshot of the org's at joining). None for reviewers.",
    )
    display_name: str = Field(min_length=1, max_length=200, description="Org name at joining; shown in the record.")


class Party(PartyBase, table=True):
    __tablename__ = "parties"
    __table_args__ = (UniqueConstraint("agreement_id", "org_id"),)

    id: str = id_field()
    agreement_id: str | None = fk("agreements.id")
    org_id: str | None = fk("organizations.id")
    confirmed_criteria_hash: str | None = Field(default=None, max_length=64)
    confirmed_at: datetime | None = timestamp(optional=True)
    confirmed_by: str | None = Field(default=None, max_length=120)  # actor who signed for the org

    agreement: "Agreement" = Relationship(back_populates="parties")


class PartyRead(PartyBase):
    id: str = Field(description="Party id.")
    org_id: str = Field(description="The organization acting in this role.")
    confirmed_current: bool = Field(description="Whether this party has signed the current criteria hash.")
    paypal_connected: bool | None = Field(
        default=None,
        description="Payee only: PayPal account connected and able to receive. False means tranches are paid "
        "through the platform with PayPal Payouts.",
    )


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
    payer_assignee_id: str | None = fk("users.id", optional=True)  # responsible person on the payer side
    payee_assignee_id: str | None = fk("users.id", optional=True)  # responsible person on the payee side
    paypal_order_id: str | None = Field(default=None, max_length=64, index=True)  # latest funding order
    paypal_authorization_id: str | None = Field(default=None, max_length=64, index=True)  # current live hold
    funded_at: datetime | None = timestamp(optional=True)  # original authorization time
    reauthorized_at: datetime | None = timestamp(optional=True)
    # Set when the funding order is created: the connected payee's merchant id (direct route), or None when the
    # money goes through the platform account and PayPal Payouts.
    payout_route: PayoutRoute | None = Field(default=None, sa_type=enum_type(PayoutRoute, 16))
    payee_merchant_id: str | None = Field(default=None, max_length=32)
    captured_at: datetime | None = timestamp(optional=True)
    release_due_at: datetime | None = timestamp(optional=True)  # end of the dispute window
    revealed_at: datetime | None = timestamp(optional=True)  # on_capture: when the payer got the full evidence
    custody_ended_at: datetime | None = timestamp(optional=True)  # retention ran: originals purged or payee-only

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
    disbursements: list["Disbursement"] = Relationship(
        back_populates="milestone",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Disbursement.created_at"},
    )


class MilestoneCreate(MilestoneBase):
    pass


class MilestoneRead(MilestoneBase):
    id: str = Field(description="Tranche (milestone) id.")
    position: int = Field(description="0-based order within the agreement.")
    status: MilestoneStatus = Field(description="Where the tranche is in its life, pending to settled.")
    paypal_authorization_id: str | None = Field(default=None, description="Live PayPal hold for this tranche.")
    payer_assignee_id: str | None = Field(default=None, description="Payer-side user the tranche is assigned to.")
    payee_assignee_id: str | None = Field(default=None, description="Payee-side user the tranche is assigned to.")
    funded_at: datetime | None = Field(default=None, description="When the original hold was placed.")
    payout_route: PayoutRoute | None = Field(
        default=None, description="direct: paid into the payee's connected PayPal; platform: via PayPal Payouts."
    )
    captured_at: datetime | None = Field(default=None, description="When the payer's money was captured.")
    release_due_at: datetime | None = Field(
        default=None, description="When the dispute window closes and the payee is paid, unless contested."
    )
    revealed_at: datetime | None = Field(
        default=None,
        description="deliverable_release=on_capture: when the payer's money was captured and the full evidence "
        "opened to the payer side. Unset while the evidence is withheld.",
    )
    pay_path: str | None = Field(
        default=None, description="Invoice mode: where the payer pays this tranche (relative to the API), while open."
    )
    refund_failed: bool = Field(
        default=False,
        description="A contest was resolved for the payer but PayPal refused the refund; the tranche stays contested "
        "and resolving it again with decision=refund retries.",
    )
    awaiting_approval: bool = Field(
        default=False,
        description="Funding was started and PayPal is waiting for the payer to approve the hold; clients poll while "
        "this is set. Cleared when the hold is placed, the payer cancels, or funding is started again.",
    )


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
    require_reviewer_approval: bool = Field(
        default=False, description="When on, only a reviewer's approval releases a tranche in review."
    )
    reviewer_decides_withheld: bool = Field(
        default=True,
        description="deliverable_release=on_capture: while a tranche's evidence is withheld from the payer, a tranche "
        "in review waits for an invited reviewer's approval (they see all the evidence; the payer can't judge what "
        "they can't see, but can still reject). Without a reviewer on the agreement the payer decides as usual.",
    )
    dispute_window_days: int = Field(
        default=0,
        ge=0,
        le=14,
        description="Escrow: days between capturing a verified tranche and paying the payee, during which the "
        "payer or a reviewer can contest it. 0 pays at once.",
    )
    deliverable_release: DeliverableRelease = Field(
        default=DeliverableRelease.on_submit,
        sa_type=enum_type(DeliverableRelease, 16),
        description="When the payer can open the payee's full evidence: on_submit (as it arrives; usual for grants) "
        "or on_capture (only once the tranche is captured, so the payer pays on the verdict; suggested for SOWs). "
        "Part of the criteria hash both parties sign; set when the agreement is created.",
    )
    min_evidence_visibility: EvidenceVisibility = Field(
        default=EvidenceVisibility.hash_only,
        sa_type=enum_type(EvidenceVisibility, 16),
        description="on_capture only: the least the payee may show the payer of each evidence item before capture. "
        "hash_only (pass/fail and the pinned hash), verdict_only (+ observed values), excerpt (+ the verifier's "
        "quotes and rationale) or preview. The payee picks a level at or above it per item. Signed with the "
        "criteria.",
    )


class Agreement(AgreementBase, table=True):
    __tablename__ = "agreements"

    id: str = id_field()
    status: AgreementStatus = Field(default=AgreementStatus.draft, sa_type=enum_type(AgreementStatus))
    auto_release_enabled: bool = False
    criteria_hash: str | None = Field(default=None, max_length=64)  # locked at activation
    created_by: str | None = Field(default=None, max_length=120)
    created_at: datetime = timestamp()

    parties: list[Party] = Relationship(
        back_populates="agreement", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    milestones: list[Milestone] = Relationship(
        back_populates="agreement",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "order_by": "Milestone.position"},
    )


class AgreementCreate(AgreementBase):
    """Created by the payer org. The payee and any reviewers are invited by email and join with their own org."""

    counterparty_email: str = Field(
        max_length=320,
        schema_extra={"pattern": EMAIL_PATTERN},
        description="Email of the person invited to accept for the payee organization.",
    )
    reviewer_emails: list[str] = Field(
        default_factory=list, description="Optional third-party reviewers to invite (criteria and evidence)."
    )
    milestones: list[MilestoneCreate] = Field(min_length=1, description="Tranches in payment order.")

    @model_validator(mode="after")
    def _distinct_invitees(self) -> "AgreementCreate":
        emails = [self.counterparty_email.lower(), *(e.lower() for e in self.reviewer_emails)]
        if len(set(emails)) != len(emails):
            raise ValueError("the counterparty and reviewers must be different people")
        return self


class FundingSummary(SQLModel):
    held: Decimal = Field(description="Escrow: authorized on the payer's PayPal account, not yet captured.")
    in_dispute_window: Decimal = Field(
        default=Decimal("0.00"), description="Escrow: captured from the payer, not yet paid to the payee."
    )
    invoiced: Decimal = Field(description="Invoice mode: invoices sent and not yet paid.")
    released: Decimal = Field(description="Paid to the payee: disbursed captures plus paid invoices.")
    refunded: Decimal = Field(default=Decimal("0.00"), description="Captured, then returned to the payer.")
    unfunded: Decimal = Field(description="Total minus held, in the dispute window, invoiced and released.")


class AgreementRead(AgreementBase):
    id: str = Field(description="Agreement id.")
    status: AgreementStatus = Field(description="draft, awaiting_confirmation, active, completed or cancelled.")
    auto_release_enabled: bool = Field(
        description="Set by the payer when signing: a funded tranche settles itself on a confident verdict."
    )
    criteria_hash: str = Field(description="Hash of the current tranches and criteria; sign this to confirm.")
    locked: bool = Field(description="True once every party signed and criteria can no longer change.")
    parties: list[PartyRead] = Field(description="Payer, payee and any reviewers that have joined.")
    pending_invitations: list[str] = Field(
        default_factory=list, description="Emails invited (as counterparty or reviewer) who haven't joined yet."
    )
    milestones: list[MilestoneRead] = Field(description="Tranches in payment order.")
    funding: FundingSummary
    lint: LintReport
