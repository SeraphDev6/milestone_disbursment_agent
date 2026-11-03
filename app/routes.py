import hashlib
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.criteria import assign_criterion_ids, criteria_hash, lint_agreement
from app.db import AgreementRow, LedgerRow, MilestoneRow, PartyRow, get_session, record_audit, utcnow
from app.domain import (
    AgreementDraft,
    AgreementStatus,
    Criterion,
    LintReport,
    MilestoneDraft,
    MilestoneStatus,
    PartyIn,
    Role,
    SettlementMode,
)

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]

EDITABLE = {AgreementStatus.draft.value, AgreementStatus.awaiting_confirmation.value}
SIGNERS = {Role.payer.value, Role.payee.value}


# --- Views -----------------------------------------------------------------------------


class PartyView(BaseModel):
    id: str
    role: Role
    display_name: str
    paypal_email: str
    confirmed_current: bool = Field(..., description="Whether this party has signed the current criteria hash.")


class MilestoneView(BaseModel):
    id: str
    position: int
    title: str
    amount: Decimal
    due_date: date | None
    status: MilestoneStatus
    criteria: list[Criterion]
    paypal_authorization_id: str | None = Field(None, description="Live PayPal hold for this tranche, if funded.")
    funded_at: datetime | None = Field(None, description="When the original hold was placed.")


class FundingSummary(BaseModel):
    held: Decimal = Field(..., description="Authorized on the payer's PayPal account, not yet captured.")
    released: Decimal = Field(..., description="Captured to the payee.")
    unfunded: Decimal = Field(..., description="Total minus held minus released.")


class AgreementView(BaseModel):
    id: str
    title: str
    total_amount: Decimal
    currency: str
    settlement_mode: SettlementMode
    auto_release_enabled: bool
    auto_release_threshold: float
    status: AgreementStatus
    criteria_hash: str = Field(..., description="Hash of the current tranches and criteria; sign this to confirm.")
    locked: bool = Field(..., description="True once both parties signed and criteria can no longer change.")
    parties: list[PartyView]
    milestones: list[MilestoneView]
    funding: FundingSummary
    lint: LintReport


class PartyKey(BaseModel):
    party_id: str
    role: Role
    api_key: str = Field(..., description="Shown once. Send as X-API-Key.")


class AgreementCreated(BaseModel):
    agreement: AgreementView
    party_keys: list[PartyKey]


class MilestonesUpdate(BaseModel):
    total_amount: Decimal | None = Field(None, gt=0, decimal_places=2, description="New total, if it changes too.")
    milestones: list[MilestoneDraft] = Field(..., min_length=1)


class ConfirmRequest(BaseModel):
    criteria_hash: str = Field(..., description="The criteria_hash this party reviewed and agrees to.")
    auto_release_enabled: bool | None = Field(
        None, description="Payer only: allow confident verdicts to release without a human."
    )


# --- Helpers ---------------------------------------------------------------------------


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _milestone_drafts(row: AgreementRow) -> list[MilestoneDraft]:
    return [
        MilestoneDraft(title=m.title, amount=m.amount, due_date=m.due_date, criteria=m.criteria) for m in row.milestones
    ]


def _as_draft(row: AgreementRow) -> AgreementDraft:
    return AgreementDraft(
        title=row.title,
        total_amount=row.total_amount,
        currency=row.currency,
        settlement_mode=SettlementMode(row.settlement_mode),
        auto_release_threshold=row.auto_release_threshold,
        source_document_uri=row.source_document_uri,
        parties=[
            PartyIn(role=Role(p.role), paypal_email=p.paypal_email, display_name=p.display_name) for p in row.parties
        ],
        milestones=_milestone_drafts(row),
    )


def _view(row: AgreementRow) -> AgreementView:
    current = criteria_hash(_milestone_drafts(row))
    return AgreementView(
        id=row.id,
        title=row.title,
        total_amount=row.total_amount,
        currency=row.currency,
        settlement_mode=SettlementMode(row.settlement_mode),
        auto_release_enabled=row.auto_release_enabled,
        auto_release_threshold=row.auto_release_threshold,
        status=AgreementStatus(row.status),
        criteria_hash=current,
        locked=row.criteria_hash is not None,
        parties=[
            PartyView(
                id=p.id,
                role=Role(p.role),
                display_name=p.display_name,
                paypal_email=p.paypal_email,
                confirmed_current=p.confirmed_criteria_hash == current,
            )
            for p in row.parties
        ],
        milestones=[
            MilestoneView(
                id=m.id,
                position=m.position,
                title=m.title,
                amount=m.amount,
                due_date=m.due_date,
                status=MilestoneStatus(m.status),
                criteria=m.criteria,
                paypal_authorization_id=m.paypal_authorization_id,
                funded_at=m.funded_at,
            )
            for m in row.milestones
        ],
        funding=_funding(row),
        lint=lint_agreement(_as_draft(row)),
    )


def _funding(row: AgreementRow) -> FundingSummary:
    session = object_session(row)
    entries = session.scalars(select(LedgerRow).where(LedgerRow.agreement_id == row.id)).all() if session else []
    by_kind: dict[str, Decimal] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, Decimal(0)) + e.amount
    released = by_kind.get("captured", Decimal(0))
    held = (
        by_kind.get("authorized", Decimal(0))
        - released
        - by_kind.get("voided", Decimal(0))
        - by_kind.get("expired", Decimal(0))
    )
    cents = Decimal("0.01")
    return FundingSummary(
        held=held.quantize(cents),
        released=released.quantize(cents),
        unfunded=(row.total_amount - held - released).quantize(cents),
    )


def _milestone_rows(drafts: list[MilestoneDraft]) -> list[MilestoneRow]:
    assign_criterion_ids(drafts)
    return [
        MilestoneRow(
            position=i,
            title=m.title,
            amount=m.amount,
            due_date=m.due_date,
            criteria=[c.model_dump(mode="json") for c in m.criteria],
            status=MilestoneStatus.pending.value,
        )
        for i, m in enumerate(drafts)
    ]


def _audit(session: Session, agreement: AgreementRow, actor: str, action: str, **kw) -> None:
    record_audit(session, agreement.id, actor, action, **kw)


def current_party(
    agreement_id: str,
    session: SessionDep,
    x_api_key: Annotated[str | None, Header(description="Party API key returned at agreement creation.")] = None,
) -> PartyRow:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
    party = session.scalar(select(PartyRow).where(PartyRow.api_key_hash == _hash_key(x_api_key)))
    if party is None or party.agreement_id != agreement_id:
        # Same answer for "no such agreement" and "not your agreement".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agreement not found")
    return party


PartyDep = Annotated[PartyRow, Depends(current_party)]


def require_org_key(
    request: Request,
    x_org_key: Annotated[str | None, Header(description="Organization key, required when the server sets one.")] = None,
) -> None:
    expected = request.app.state.settings.org_api_key
    if expected and not (x_org_key and secrets.compare_digest(x_org_key, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-Org-Key header required")


OrgKeyDep = Annotated[None, Depends(require_org_key)]


def milestone_party(
    milestone_id: str,
    session: SessionDep,
    x_api_key: Annotated[str | None, Header(description="Party API key returned at agreement creation.")] = None,
) -> tuple[PartyRow, MilestoneRow]:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
    party = session.scalar(select(PartyRow).where(PartyRow.api_key_hash == _hash_key(x_api_key)))
    milestone = session.get(MilestoneRow, milestone_id)
    if party is None or milestone is None or milestone.agreement_id != party.agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "milestone not found")
    return party, milestone


MilestonePartyDep = Annotated[tuple[PartyRow, MilestoneRow], Depends(milestone_party)]


# --- Endpoints -------------------------------------------------------------------------


@router.get("/health", tags=["system"])
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "mode": request.app.state.settings.mode.value}


@router.post(
    "/criteria/lint",
    tags=["agreements"],
    openapi_extra={"x-mcp-tool": "lint_agreement_criteria"},
    summary="Check tranches and criteria for anything a reviewer couldn't verify",
)
def lint(draft: AgreementDraft) -> LintReport:
    return lint_agreement(draft)


@router.post(
    "/agreements",
    status_code=status.HTTP_201_CREATED,
    tags=["agreements"],
    openapi_extra={"x-mcp-tool": "create_agreement"},
    summary="Create a draft agreement; returns one API key per party",
)
def create_agreement(draft: AgreementDraft, session: SessionDep, _: OrgKeyDep) -> AgreementCreated:
    row = AgreementRow(
        title=draft.title,
        total_amount=draft.total_amount,
        currency=draft.currency,
        settlement_mode=draft.settlement_mode.value,
        auto_release_threshold=draft.auto_release_threshold,
        source_document_uri=draft.source_document_uri,
        status=AgreementStatus.draft.value,
        milestones=_milestone_rows(draft.milestones),
    )
    keys: list[tuple[PartyRow, str]] = []
    for p in draft.parties:
        key = "mde_" + secrets.token_urlsafe(32)
        party = PartyRow(
            role=p.role.value, paypal_email=p.paypal_email, display_name=p.display_name, api_key_hash=_hash_key(key)
        )
        row.parties.append(party)
        keys.append((party, key))
    session.add(row)
    session.flush()
    _audit(session, row, "system", "agreement_created", to_status=row.status)
    session.commit()
    return AgreementCreated(
        agreement=_view(row),
        party_keys=[PartyKey(party_id=p.id, role=Role(p.role), api_key=k) for p, k in keys],
    )


@router.get(
    "/agreements/{agreement_id}",
    tags=["agreements"],
    openapi_extra={"x-mcp-tool": "get_agreement_status"},
    summary="Agreement, tranches, criteria, lint and confirmation status",
)
def get_agreement(agreement_id: str, party: PartyDep) -> AgreementView:
    return _view(party.agreement)


@router.put(
    "/agreements/{agreement_id}/milestones",
    tags=["agreements"],
    summary="Replace tranches and criteria while in draft; clears all confirmations",
)
def replace_milestones(
    agreement_id: str, body: MilestonesUpdate, party: PartyDep, session: SessionDep
) -> AgreementView:
    row = party.agreement
    if party.role != Role.payer.value:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only the payer edits tranches; the payee confirms or asks for changes"
        )
    if row.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"criteria are locked (agreement is {row.status})")
    previous = row.status
    row.milestones = _milestone_rows(body.milestones)
    if body.total_amount is not None:
        row.total_amount = body.total_amount
    for p in row.parties:
        p.confirmed_criteria_hash = None
        p.confirmed_at = None
    row.status = AgreementStatus.draft.value
    _audit(session, row, f"party:{party.id}", "milestones_replaced", from_status=previous, to_status=row.status)
    session.commit()
    return _view(row)


@router.post(
    "/agreements/{agreement_id}/confirm",
    tags=["agreements"],
    summary="Sign the current criteria hash; activates when payer and payee have both signed",
)
def confirm(agreement_id: str, body: ConfirmRequest, party: PartyDep, session: SessionDep) -> AgreementView:
    row = party.agreement
    if party.role not in SIGNERS:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the payer and payee confirm criteria")
    if row.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"agreement is already {row.status}")
    if body.auto_release_enabled is not None and party.role != Role.payer.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only the payer can turn on auto-release")

    report = lint_agreement(_as_draft(row))
    if not report.ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": "criteria have lint errors; fix them before confirming",
                "lint": report.model_dump(mode="json"),
            },
        )
    current = criteria_hash(_milestone_drafts(row))
    if body.criteria_hash != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "criteria changed since you reviewed them", "current_criteria_hash": current},
        )

    previous = row.status
    party.confirmed_criteria_hash = current
    party.confirmed_at = utcnow()
    if body.auto_release_enabled is not None:
        row.auto_release_enabled = body.auto_release_enabled
    _audit(
        session, row, f"party:{party.id}", "criteria_confirmed", detail={"criteria_hash": current, "role": party.role}
    )

    signers = [p for p in row.parties if p.role in SIGNERS]
    if all(p.confirmed_criteria_hash == current for p in signers):
        row.status = AgreementStatus.active.value
        row.criteria_hash = current
    else:
        row.status = AgreementStatus.awaiting_confirmation.value
    if row.status != previous:
        _audit(session, row, "system", "status_changed", from_status=previous, to_status=row.status)
    session.commit()
    return _view(row)
