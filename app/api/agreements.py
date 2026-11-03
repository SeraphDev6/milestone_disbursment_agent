"""Agreements: extract from a document, lint, create, edit tranches, confirm."""

import secrets
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.ai.agents import AIError
from app.ai.extraction import ExtractionResult, extract
from app.api.deps import OrgKeyDep, PartyDep, SessionDep, ai, hash_key
from app.api.views import AgreementView, agreement_as_draft, agreement_view, milestone_drafts
from app.db import Agreement, Milestone, Party, record_audit, utcnow
from app.domain.criteria import assign_criterion_ids, criteria_hash, lint_agreement
from app.domain.models import AgreementDraft, AgreementStatus, LintReport, MilestoneDraft, MilestoneStatus, Role

router = APIRouter(tags=["agreements"])

EDITABLE = {AgreementStatus.draft.value, AgreementStatus.awaiting_confirmation.value}
SIGNERS = {Role.payer.value, Role.payee.value}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_TYPES = {"application/pdf", "text/plain", "text/markdown"}


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


def milestone_rows(drafts: list[MilestoneDraft]) -> list[Milestone]:
    assign_criterion_ids(drafts)
    return [
        Milestone(
            position=i,
            title=m.title,
            amount=m.amount,
            due_date=m.due_date,
            criteria=[c.model_dump(mode="json") for c in m.criteria],
            status=MilestoneStatus.pending.value,
        )
        for i, m in enumerate(drafts)
    ]


@router.post(
    "/agreements/extract",
    openapi_extra={"x-mcp-tool": "draft_agreement_from_document"},
    summary="Propose tranches and checkable criteria from an agreement document (PDF or text)",
)
async def extract_agreement(request: Request, _: OrgKeyDep, file: Annotated[UploadFile, File()]) -> ExtractionResult:
    media_type = (file.content_type or "").split(";")[0]
    if media_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"upload a PDF or text file, not {media_type}")
    data = await file.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "agreements up to 20 MB")
    try:
        return await run_in_threadpool(extract, ai(request), data, media_type, file.filename or "agreement")
    except AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.post(
    "/criteria/lint",
    openapi_extra={"x-mcp-tool": "lint_agreement_criteria"},
    summary="Check tranches and criteria for anything a reviewer couldn't verify",
)
def lint(draft: AgreementDraft) -> LintReport:
    return lint_agreement(draft)


@router.post(
    "/agreements",
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"x-mcp-tool": "create_agreement"},
    summary="Create a draft agreement; returns one API key per party",
)
def create_agreement(draft: AgreementDraft, session: SessionDep, _: OrgKeyDep) -> AgreementCreated:
    row = Agreement(
        title=draft.title,
        total_amount=draft.total_amount,
        currency=draft.currency,
        settlement_mode=draft.settlement_mode.value,
        auto_release_threshold=draft.auto_release_threshold,
        source_document_uri=draft.source_document_uri,
        status=AgreementStatus.draft.value,
        milestones=milestone_rows(draft.milestones),
    )
    keys: list[tuple[Party, str]] = []
    for p in draft.parties:
        key = "mde_" + secrets.token_urlsafe(32)
        party = Party(
            role=p.role.value, paypal_email=p.paypal_email, display_name=p.display_name, api_key_hash=hash_key(key)
        )
        row.parties.append(party)
        keys.append((party, key))
    session.add(row)
    session.flush()
    record_audit(session, row.id, "system", "agreement_created", to_status=row.status)
    session.commit()
    return AgreementCreated(
        agreement=agreement_view(row),
        party_keys=[PartyKey(party_id=p.id, role=Role(p.role), api_key=k) for p, k in keys],
    )


@router.get(
    "/agreements/{agreement_id}",
    openapi_extra={"x-mcp-tool": "get_agreement_status"},
    summary="Agreement, tranches, criteria, lint and confirmation status",
)
def get_agreement(agreement_id: str, party: PartyDep) -> AgreementView:
    return agreement_view(party.agreement)


@router.put(
    "/agreements/{agreement_id}/milestones",
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
    row.milestones = milestone_rows(body.milestones)
    if body.total_amount is not None:
        row.total_amount = body.total_amount
    for p in row.parties:
        p.confirmed_criteria_hash = None
        p.confirmed_at = None
    row.status = AgreementStatus.draft.value
    record_audit(
        session, row.id, f"party:{party.id}", "milestones_replaced", from_status=previous, to_status=row.status
    )
    session.commit()
    return agreement_view(row)


@router.post(
    "/agreements/{agreement_id}/confirm",
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

    report = lint_agreement(agreement_as_draft(row))
    if not report.ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": "criteria have lint errors; fix them before confirming",
                "lint": report.model_dump(mode="json"),
            },
        )
    current = criteria_hash(milestone_drafts(row))
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
    record_audit(
        session,
        row.id,
        f"party:{party.id}",
        "criteria_confirmed",
        detail={"criteria_hash": current, "role": party.role},
    )

    signers = [p for p in row.parties if p.role in SIGNERS]
    if all(p.confirmed_criteria_hash == current for p in signers):
        row.status = AgreementStatus.active.value
        row.criteria_hash = current
    else:
        row.status = AgreementStatus.awaiting_confirmation.value
    if row.status != previous:
        record_audit(session, row.id, "system", "status_changed", from_status=previous, to_status=row.status)
    session.commit()
    return agreement_view(row)
