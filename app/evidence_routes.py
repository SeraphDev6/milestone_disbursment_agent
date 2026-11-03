from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from app import verification
from app.ai import AIRuntime
from app.db import VerdictRow, utcnow
from app.domain import CriterionResult, EvidenceIn, MilestoneStatus, Role
from app.escrow_routes import _gateway, _require
from app.extraction import ExtractionResult, extract
from app.routes import MilestonePartyDep, OrgKeyDep, SessionDep

router = APIRouter(tags=["evidence"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DOCUMENT_TYPES = {"application/pdf", "text/plain", "text/markdown"}


def _ai(request: Request) -> AIRuntime:
    return request.app.state.ai


class VerdictView(BaseModel):
    id: str
    is_precheck: bool
    met: bool = Field(..., description="Every criterion met and cited. Computed by the server, not the model.")
    gate: str = Field(..., description="auto_release, needs_review or precheck.")
    model_confidence: float
    rationale: str
    criteria_results: list[CriterionResult]
    model: str
    prompt_version: str
    created_at: datetime


class EvidenceAccepted(BaseModel):
    evidence_id: str
    duplicate: bool = Field(..., description="True when identical evidence was already on file.")
    milestone_status: MilestoneStatus


class PrecheckRequest(BaseModel):
    evidence: list[EvidenceIn] = Field(..., min_length=1, description="Draft evidence; nothing is stored.")


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=2000, description="Shown to the payee and kept in the audit log.")


class ReviewOutcome(BaseModel):
    milestone_status: MilestoneStatus
    missing: list[CriterionResult] = Field(..., description="On rejection: the unmet criteria and what to send.")


def _verdict_view(v: VerdictRow) -> VerdictView:
    return VerdictView(
        id=v.id,
        is_precheck=v.is_precheck,
        met=v.met,
        gate=v.gate,
        model_confidence=v.model_confidence,
        rationale=v.rationale,
        criteria_results=v.criteria_results,
        model=v.model,
        prompt_version=v.prompt_version,
        created_at=v.created_at,
    )


def _verify_in_background(request: Request, milestone_id: str, actor: str) -> None:
    state = request.app.state
    with state.sessionmaker() as session:
        verification.run_verification(session, state.ai, state.paypal, milestone_id, actor, utcnow())


@router.post(
    "/agreements/extract",
    tags=["agreements"],
    openapi_extra={"x-mcp-tool": "draft_agreement_from_document"},
    summary="Propose tranches and checkable criteria from an agreement document (PDF or text)",
)
async def extract_agreement(request: Request, _: OrgKeyDep, file: Annotated[UploadFile, File()]) -> ExtractionResult:
    media_type = (file.content_type or "").split(";")[0]
    if media_type not in DOCUMENT_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"upload a PDF or text file, not {media_type}")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "agreements up to 20 MB")
    from starlette.concurrency import run_in_threadpool

    try:
        return await run_in_threadpool(extract, _ai(request), data, media_type, file.filename or "agreement")
    except verification.AIError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


@router.post(
    "/milestones/{milestone_id}/evidence",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={"x-mcp-tool": "submit_evidence"},
    summary="Payee submits evidence for a tranche; verification runs in the background",
)
def submit_evidence(
    milestone_id: str,
    body: EvidenceIn,
    auth: MilestonePartyDep,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
) -> EvidenceAccepted:
    party, m = auth
    _require(party, Role.payee)
    row, created = verification.submit_evidence(session, m, body, source="api", actor=f"party:{party.id}")
    if created and m.status == MilestoneStatus.evidence_received.value:
        background.add_task(_verify_in_background, request, m.id, "system:verifier")
    return EvidenceAccepted(evidence_id=row.id, duplicate=not created, milestone_status=MilestoneStatus(m.status))


@router.post(
    "/milestones/{milestone_id}/precheck",
    openapi_extra={"x-mcp-tool": "precheck_evidence"},
    summary="Payee checks draft evidence against the criteria without submitting it",
)
def precheck(
    milestone_id: str, body: PrecheckRequest, auth: MilestonePartyDep, session: SessionDep, request: Request
) -> VerdictView:
    party, m = auth
    _require(party, Role.payee)
    return _verdict_view(verification.precheck(session, _ai(request), m, body.evidence, f"party:{party.id}"))


@router.post("/milestones/{milestone_id}/verify", summary="Re-run verification on the evidence already submitted")
def reverify(
    milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request, background: BackgroundTasks
) -> EvidenceAccepted:
    party, m = auth
    if m.status != MilestoneStatus.evidence_received.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tranche is {m.status}; nothing to verify")
    background.add_task(_verify_in_background, request, m.id, f"party:{party.id}")
    return EvidenceAccepted(evidence_id="", duplicate=False, milestone_status=MilestoneStatus(m.status))


@router.get(
    "/milestones/{milestone_id}/verdict",
    openapi_extra={"x-mcp-tool": "get_verdict"},
    summary="Latest verdict for a tranche, with per-criterion results and citations",
)
def get_verdict(milestone_id: str, auth: MilestonePartyDep) -> VerdictView:
    _, m = auth
    v = verification.latest_verdict(m)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no verdict yet")
    return _verdict_view(v)


@router.post("/milestones/{milestone_id}/review", summary="Payer or approver approves or rejects a verdict in review")
def review(
    milestone_id: str, body: ReviewRequest, auth: MilestonePartyDep, session: SessionDep, request: Request
) -> ReviewOutcome:
    party, m = auth
    _require(party, Role.payer, Role.approver)
    verification.review(session, _gateway(request), m, party, body.decision == "approve", body.note, utcnow())
    session.refresh(m)
    v = verification.latest_verdict(m)
    missing = [] if body.decision == "approve" or v is None else [r for r in v.criteria_results if not r["met"]]
    return ReviewOutcome(milestone_status=MilestoneStatus(m.status), missing=missing)
