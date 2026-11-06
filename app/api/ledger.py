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
    id: str = Field(description="Ledger entry id.")
    milestone_id: str | None = Field(description="The tranche the money belongs to; null for agreement-level.")
    kind: str = Field(description="authorized, captured, voided, expired, invoiced, paid or invoice_cancelled.")
    amount: Decimal = Field(description="In the agreement's currency.")
    paypal_ref: str = Field(description="PayPal's id for the object: authorization, capture, invoice or order.")
    at: datetime = Field(description="When the engine recorded it.")


class SettlementAttempt(SQLModel):
    id: str = Field(description="Settlement attempt id.")
    milestone_id: str = Field(description="The tranche being settled.")
    attempt: int = Field(description="1 for the first try; a failed release retries with the next number.")
    mode: SettlementMode = Field(description="escrow_release (capture a hold) or invoice_on_verify.")
    idempotency_key: str = Field(description="Sent to PayPal as PayPal-Request-Id, so a retry can't double-charge.")
    paypal_ref: str | None = Field(description="Capture id, invoice id or pay-now order id once PayPal answered.")
    status: SettlementStatus = Field(description="submitted, succeeded, failed or expired.")
    amount: Decimal = Field(description="What this attempt captures or invoices (a share, with partial release).")
    final: bool = Field(description="Escrow: whether this capture closes the hold. False for a partial share.")
    criterion_ids: list[str] = Field(description="The criteria this capture pays for.")
    error: str | None = Field(description="PayPal's error when the attempt failed.")
    created_at: datetime = Field(description="When the attempt started.")
    updated_at: datetime = Field(description="Last change.")


class DisbursementAttempt(SQLModel):
    id: str = Field(description="Disbursement attempt id.")
    milestone_id: str = Field(description="The tranche whose captured money moves.")
    kind: DisbursementKind = Field(description="referenced_payout or payout (to the payee), refund (to the payer).")
    attempt: int = Field(description="1 for the first try; a failed payout retries with the next number.")
    idempotency_key: str = Field(description="Sent to PayPal as PayPal-Request-Id, so a retry can't double-pay.")
    paypal_ref: str | None = Field(description="Payout item id, referenced payout id or refund id from PayPal.")
    status: SettlementStatus = Field(description="submitted, succeeded, failed or unclaimed.")
    amount: Decimal = Field(description="The captured amount this pays out or refunds.")
    settlement_id: str | None = Field(description="The capture whose money this moves.")
    error: str | None = Field(description="PayPal's error when the attempt failed.")
    created_at: datetime = Field(description="When the attempt started.")
    updated_at: datetime = Field(description="Last change.")


class LedgerView(SQLModel):
    currency: str = Field(description="The agreement's ISO 4217 currency; every amount below is in it.")
    entries: list[LedgerEntryRead] = Field(description="PayPal money movements, oldest first.")
    settlements: list[SettlementAttempt] = Field(description="Every capture or invoice attempt, per tranche.")
    disbursements: list[DisbursementAttempt] = Field(description="Payouts to the payee and refunds to the payer.")


@router.get(
    "/agreements/{agreement_id}/ledger",
    openapi_extra={"x-sdk-core": "getLedger"},
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
