"""Agreements: extract from a document, lint, create (as the payer org), invite, edit tranches, confirm."""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile, status
from sqlmodel import Field, SQLModel, select
from starlette.concurrency import run_in_threadpool

from app.ai.agents import AIError
from app.ai.extraction import ExtractionResult, extract
from app.api.auth import invitation_read
from app.api.deps import AgreementAccessDep, OrgKeyDep, PrincipalDep, SessionDep, ai, mailer, rate_limit
from app.api.views import agreement_read
from app.db import record_audit, utcnow
from app.domain.criteria import assign_criterion_ids, criteria_hash, lint_agreement
from app.models import (
    Agreement,
    AgreementCreate,
    AgreementRead,
    AgreementStatus,
    Invitation,
    InvitationKind,
    InvitationRead,
    LintReport,
    Milestone,
    MilestoneCreate,
    Organization,
    OrgRole,
    Party,
    Role,
)
from app.models.identity import EMAIL_PATTERN
from app.services import identity
from app.services.access import AccessError, Action

router = APIRouter(tags=["agreements"])

EDITABLE = {AgreementStatus.draft, AgreementStatus.awaiting_confirmation}
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
DOCUMENT_TYPES = {"application/pdf", "text/plain", "text/markdown"}


class TranchesDraft(SQLModel):
    total_amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    milestones: list[MilestoneCreate] = Field(min_length=1)


class AgreementCreated(SQLModel):
    agreement: AgreementRead
    invitations: list[InvitationRead]


class InviteToAgreement(SQLModel):
    email: str = Field(max_length=320, schema_extra={"pattern": EMAIL_PATTERN})
    kind: Literal["counterparty", "reviewer"] = "reviewer"


class InvitationSent(SQLModel):
    invitation: InvitationRead


class MilestonesUpdate(SQLModel):
    total_amount: Decimal | None = Field(
        default=None, gt=0, max_digits=14, decimal_places=2, description="New total, if it changes too."
    )
    milestones: list[MilestoneCreate] = Field(min_length=1)


class ConfirmRequest(SQLModel):
    criteria_hash: str = Field(description="The criteria_hash this party reviewed and agrees to.")
    auto_release_enabled: bool | None = Field(
        default=None, description="Payer only: allow confident verdicts to release without a human."
    )


def milestone_rows(drafts: list[MilestoneCreate]) -> list[Milestone]:
    assign_criterion_ids(drafts)
    return [Milestone.model_validate(m, update={"position": i}) for i, m in enumerate(drafts)]


class AgreementText(SQLModel):
    text: str = Field(min_length=20, max_length=200_000, description="Full text of the grant agreement or SOW.")
    title: str = Field(default="agreement", max_length=200, description="File name or title, for provenance.")


@router.post(
    "/agreements/extract-text",
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "draft_agreement_from_text"},
    summary="Propose tranches and checkable criteria from an agreement's text",
)
async def extract_agreement_text(
    body: AgreementText, request: Request, _: OrgKeyDep, principal: PrincipalDep
) -> ExtractionResult:
    rate_limit(request, "extract", principal.actor)
    try:
        return await run_in_threadpool(extract, ai(request), body.text, "text/plain", body.title)
    except AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.post(
    "/agreements/extract",
    openapi_extra={"x-sdk-core": True},
    summary="Propose tranches and checkable criteria from an agreement document (PDF or text upload)",
)
async def extract_agreement(
    request: Request, _: OrgKeyDep, principal: PrincipalDep, file: Annotated[UploadFile, File()]
) -> ExtractionResult:
    rate_limit(request, "extract", principal.actor)
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
def lint(draft: TranchesDraft) -> LintReport:
    return lint_agreement(draft)


@router.post(
    "/orgs/{org_id}/agreements",
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "create_agreement"},
    summary="Create a draft agreement as this org (the payer) and invite the payee and any reviewers",
)
def create_agreement(
    org_id: str, draft: AgreementCreate, principal: PrincipalDep, session: SessionDep, request: Request
) -> AgreementCreated:
    principal.require_org(org_id, OrgRole.member)
    org = session.get(Organization, org_id)
    if not org.paypal_email:  # type: ignore[union-attr]
        raise AccessError("set your organization's PayPal email before creating agreements (PATCH /orgs/{id})", 422)
    actor = f"{principal.actor}@org:{org_id}"
    row = Agreement.model_validate(
        draft.model_dump(exclude={"milestones", "counterparty_email", "reviewer_emails"}), update={"created_by": actor}
    )
    row.milestones = milestone_rows(draft.milestones)
    row.parties = [Party(role=Role.payer, org_id=org_id, display_name=org.name, paypal_email=org.paypal_email)]  # type: ignore[union-attr]
    session.add(row)
    session.flush()
    record_audit(session, row.id, actor, "agreement_created", to_status=row.status)
    base, invitations = request.app.state.settings.api_base_url, []
    invitees = [(draft.counterparty_email, InvitationKind.counterparty)] + [
        (e, InvitationKind.reviewer) for e in draft.reviewer_emails
    ]
    for email, kind in invitees:
        inv, _ = identity.invite(
            session,
            mailer(request),
            kind=kind,
            email=email,
            actor=actor,
            base_url=base,
            app_url=request.app.state.app_url,
            agreement=row,
            commit=False,
        )
        invitations.append(inv)
    session.commit()
    return AgreementCreated(
        agreement=agreement_read(row), invitations=[invitation_read(session, i) for i in invitations]
    )


@router.get(
    "/agreements/{agreement_id}",
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "get_agreement_status"},
    summary="Agreement, tranches, criteria, lint and confirmation status",
)
def get_agreement(agreement_id: str, access: AgreementAccessDep) -> AgreementRead:
    access.require(Action.view)
    return agreement_read(access.party.agreement)


@router.post(
    "/agreements/{agreement_id}/invitations",
    openapi_extra={"x-sdk-core": True},
    status_code=201,
    summary="Invite a reviewer (or the payee, if none has joined) to this agreement (payer admin)",
)
def invite_to_agreement(
    agreement_id: str, body: InviteToAgreement, access: AgreementAccessDep, session: SessionDep, request: Request
) -> InvitationSent:
    access.require(Action.invite)
    a = access.party.agreement
    if a.status in (AgreementStatus.cancelled, AgreementStatus.completed):
        raise AccessError(f"agreement is {a.status}", 409)
    kind = InvitationKind(body.kind)
    if kind == InvitationKind.counterparty:
        pending = identity.pending_agreement_invitations(session, a.id)
        if any(p.role == Role.payee for p in a.parties) or any(i.kind == kind for i in pending):
            raise AccessError("this agreement already has a payee or a pending payee invitation", 409)
    inv, _ = identity.invite(
        session,
        mailer(request),
        kind=kind,
        email=body.email,
        actor=access.actor,
        base_url=request.app.state.settings.api_base_url,
        app_url=request.app.state.app_url,
        agreement=a,
    )
    return InvitationSent(invitation=invitation_read(session, inv))


@router.get("/agreements/{agreement_id}/invitations", summary="Invitations on this agreement")
def list_agreement_invitations(
    agreement_id: str, access: AgreementAccessDep, session: SessionDep
) -> list[InvitationRead]:
    access.require(Action.view)
    rows = session.exec(select(Invitation).where(Invitation.agreement_id == agreement_id)).all()
    return [invitation_read(session, i) for i in rows]


@router.delete(
    "/agreements/{agreement_id}/invitations/{invitation_id}", status_code=204, summary="Revoke an invitation"
)
def revoke_invitation(
    agreement_id: str, invitation_id: str, access: AgreementAccessDep, session: SessionDep
) -> Response:
    access.require(Action.invite)
    inv = session.get(Invitation, invitation_id)
    if inv is None or inv.agreement_id != agreement_id or inv.accepted_at is not None:
        raise AccessError("no open invitation with that id", 404)
    inv.revoked_at = utcnow()
    record_audit(session, agreement_id, access.actor, "invitation_revoked", detail={"email": inv.email})
    session.commit()
    return Response(status_code=204)


@router.put(
    "/agreements/{agreement_id}/milestones",
    summary="Replace tranches and criteria while in draft; clears all confirmations",
)
def replace_milestones(
    agreement_id: str, body: MilestonesUpdate, access: AgreementAccessDep, session: SessionDep
) -> AgreementRead:
    access.require(Action.edit_tranches)
    row = access.party.agreement
    if row.status not in EDITABLE:
        raise AccessError(f"criteria are locked (agreement is {row.status})", 409)
    previous = row.status
    row.milestones = milestone_rows(body.milestones)
    if body.total_amount is not None:
        row.total_amount = body.total_amount
    for p in row.parties:
        p.confirmed_criteria_hash = None
        p.confirmed_at = None
        p.confirmed_by = None
    row.status = AgreementStatus.draft
    record_audit(session, row.id, access.actor, "milestones_replaced", from_status=previous, to_status=row.status)
    session.commit()
    return agreement_read(row)


@router.post(
    "/agreements/{agreement_id}/confirm",
    openapi_extra={"x-sdk-core": True},
    summary="Sign the current criteria hash for your org; activates when every party has signed",
)
def confirm(agreement_id: str, body: ConfirmRequest, access: AgreementAccessDep, session: SessionDep) -> AgreementRead:
    access.require(Action.confirm_criteria)
    if body.auto_release_enabled is not None:
        access.require(Action.set_auto_release)
    row, party = access.party.agreement, access.party
    if row.status not in EDITABLE:
        raise AccessError(f"agreement is already {row.status}", 409)

    report = lint_agreement(row)
    if not report.ok:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "message": "criteria have lint errors; fix them before confirming",
                "lint": report.model_dump(mode="json"),
            },
        )
    current = criteria_hash(row.milestones, row.deliverable_release, row.min_evidence_visibility)
    if body.criteria_hash != current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "criteria changed since you reviewed them", "current_criteria_hash": current},
        )

    previous = row.status
    party.confirmed_criteria_hash, party.confirmed_at, party.confirmed_by = current, utcnow(), access.actor
    if body.auto_release_enabled is not None:
        row.auto_release_enabled = body.auto_release_enabled
    record_audit(
        session, row.id, access.actor, "criteria_confirmed", detail={"criteria_hash": current, "role": party.role}
    )

    # Live only when the payee has joined, every party (incl. reviewers) signed, and nobody invited is pending.
    everyone_signed = all(p.confirmed_criteria_hash == current for p in row.parties)
    has_payee = any(p.role == Role.payee for p in row.parties)
    nobody_pending = not identity.pending_agreement_invitations(session, row.id)
    if everyone_signed and has_payee and nobody_pending:
        row.status = AgreementStatus.active
        row.criteria_hash = current
    else:
        row.status = AgreementStatus.awaiting_confirmation
    if row.status != previous:
        record_audit(session, row.id, "system", "status_changed", from_status=previous, to_status=row.status)
    session.commit()
    return agreement_read(row)
