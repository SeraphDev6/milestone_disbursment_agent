"""The money record for one agreement: PayPal movements (ledger) and settlement attempts."""

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter
from sqlmodel import Field, SQLModel, select

from app.api.deps import AgreementAccessDep, SessionDep
from app.models import Disbursement, DisbursementKind, LedgerEntry, Settlement, SettlementMode, SettlementStatus
from app.services.access import Action

router = APIRouter(tags=["settlement"])


class LedgerEntryRead(SQLModel):
    id: str
    milestone_id: str | None
    kind: str = Field(description="authorized, captured, voided, expired, invoiced, paid or invoice_cancelled.")
    amount: Decimal
    paypal_ref: str
    at: datetime


class SettlementAttempt(SQLModel):
    id: str
    milestone_id: str
    attempt: int
    mode: SettlementMode
    idempotency_key: str
    paypal_ref: str | None
    status: SettlementStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


class DisbursementAttempt(SQLModel):
    id: str
    milestone_id: str
    kind: DisbursementKind = Field(description="referenced_payout or payout (to the payee), refund (to the payer).")
    attempt: int
    idempotency_key: str
    paypal_ref: str | None
    status: SettlementStatus
    error: str | None
    created_at: datetime
    updated_at: datetime


class LedgerView(SQLModel):
    currency: str
    entries: list[LedgerEntryRead] = Field(description="PayPal money movements, oldest first.")
    settlements: list[SettlementAttempt] = Field(description="Every capture or invoice attempt, per tranche.")
    disbursements: list[DisbursementAttempt] = Field(description="Payouts to the payee and refunds to the payer.")


@router.get(
    "/agreements/{agreement_id}/ledger",
    openapi_extra={"x-sdk-core": True},
    summary="Every PayPal money movement and settlement attempt on an agreement",
)
def ledger(agreement_id: str, access: AgreementAccessDep, session: SessionDep) -> LedgerView:
    access.require(Action.view)
    a = access.party.agreement
    entries = session.exec(select(LedgerEntry).where(LedgerEntry.agreement_id == a.id).order_by(LedgerEntry.at)).all()
    ids = [m.id for m in a.milestones]
    attempts = (
        session.exec(select(Settlement).where(Settlement.milestone_id.in_(ids)).order_by(Settlement.created_at)).all()  # type: ignore[union-attr]
        if ids
        else []
    )
    disbursements = (
        session.exec(
            select(Disbursement).where(Disbursement.milestone_id.in_(ids)).order_by(Disbursement.created_at)  # type: ignore[union-attr]
        ).all()
        if ids
        else []
    )
    return LedgerView(
        currency=a.currency,
        entries=[LedgerEntryRead.model_validate(e) for e in entries],
        settlements=[SettlementAttempt.model_validate(s) for s in attempts],
        disbursements=[DisbursementAttempt.model_validate(d) for d in disbursements],
    )
