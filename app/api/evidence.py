"""Evidence in (API and file uploads), verification, review and disputes."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlmodel import Field, SQLModel, select

from app.api.deps import EvidencePartyDep, MilestonePartyDep, SessionDep, ai, gateway, require_role
from app.api.views import EvidenceAccepted
from app.db import utcnow
from app.integrations.uploads import UploadError, parse_upload
from app.models import CriterionResult, EvidenceCreate, EvidenceRead, MilestoneStatus, Role, StoredFile, VerdictRead
from app.services import verification
from app.services.packet import milestone_section

router = APIRouter(tags=["evidence"])

MAX_FILES = 10
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 25 * 1024 * 1024


class PrecheckRequest(SQLModel):
    evidence: list[EvidenceCreate] = Field(..., min_length=1, description="Draft evidence; nothing is stored.")


class ReviewRequest(SQLModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=2000, description="Shown to the payee and kept in the audit log.")


class ReviewOutcome(SQLModel):
    milestone_status: MilestoneStatus
    missing: list[CriterionResult] = Field(..., description="On rejection: the unmet criteria and what to send.")


class DisputeIn(SQLModel):
    reason: str = Field(..., min_length=10, max_length=4000, description="Why the rejection is wrong.")


class DisputePack(SQLModel):
    milestone_status: MilestoneStatus
    reason: str
    pack: dict = Field(..., description="Criteria, evidence, verdicts, reviews, payments and audit for this tranche.")


def _verify_later(background: BackgroundTasks, request: Request, milestone_id: str, actor: str) -> None:
    state = request.app.state
    background.add_task(
        verification.verify_in_background, state.sessionmaker, state.ai, state.paypal, milestone_id, actor
    )


# --- intake ----------------------------------------------------------------------------


@router.post(
    "/milestones/{milestone_id}/evidence",
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={"x-mcp-tool": "submit_evidence"},
    summary="Payee submits evidence for a tranche; verification runs in the background",
)
def submit_evidence(
    milestone_id: str,
    body: EvidenceCreate,
    auth: MilestonePartyDep,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
) -> EvidenceAccepted:
    party, m = auth
    require_role(party, Role.payee)
    row, created = verification.submit_evidence(session, m, body, source="api", actor=f"party:{party.id}")
    if created and m.status == MilestoneStatus.evidence_received:
        _verify_later(background, request, m.id, "system:verifier")
    return EvidenceAccepted(evidence_id=row.id, duplicate=not created, milestone_status=MilestoneStatus(m.status))


@router.post(
    "/milestones/{milestone_id}/uploads",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Payee uploads evidence files (PDF, CSV, XLSX, TXT, MD, images); verification runs in the background",
)
async def upload_evidence(
    milestone_id: str,
    auth: MilestonePartyDep,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(description="Up to 10 files, 20 MB each, 25 MB total.")],
    note: Annotated[str | None, Form(max_length=2000, description="Optional context, shown as a payee note.")] = None,
    dated: Annotated[date | None, Form(description="The evidence's own date, if the files don't carry one.")] = None,
) -> list[EvidenceAccepted]:
    party, m = auth
    require_role(party, Role.payee)
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"up to {MAX_FILES} files per upload")

    parsed, total = [], 0
    for f in files:
        data = await f.read(MAX_FILE_BYTES + 1)
        total += len(data)
        if len(data) > MAX_FILE_BYTES or total > MAX_REQUEST_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "files up to 20 MB each, 25 MB per upload")
        try:
            parsed += parse_upload(f.filename or "upload", data, note, dated)  # nothing stored until all parse
        except UploadError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    results = []
    for item in parsed:
        file_row = session.exec(select(StoredFile).where(StoredFile.sha256 == item.sha256)).first() or StoredFile(
            filename=item.evidence.title,
            media_type=item.media_type,
            sha256=item.sha256,
            size=len(item.data),
            data=item.data,
        )
        session.add(file_row)
        session.flush()
        row, created = verification.submit_evidence(
            session, m, item.evidence, source="upload", actor=f"party:{party.id}", file_id=file_row.id
        )
        results.append(EvidenceAccepted(evidence_id=row.id, duplicate=not created, milestone_status=m.status))
    if any(not r.duplicate for r in results) and m.status == MilestoneStatus.evidence_received:
        _verify_later(background, request, m.id, "system:verifier")
    return results


@router.get(
    "/milestones/{milestone_id}/evidence",
    openapi_extra={"x-mcp-tool": "list_evidence"},
    summary="Evidence on file for a tranche",
)
def list_evidence(milestone_id: str, auth: MilestonePartyDep) -> list[EvidenceRead]:
    _, m = auth
    return [EvidenceRead.from_row(e) for e in m.evidence]


@router.get("/evidence/{evidence_id}/file", summary="Download the original uploaded file")
def download_file(evidence_id: str, auth: EvidencePartyDep) -> Response:
    _, ev = auth
    if ev.file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this evidence has no uploaded file")
    safe_name = "".join(ch for ch in ev.file.filename if ch.isalnum() or ch in "._- ")[:100] or "evidence"
    return Response(
        ev.file.data,
        media_type=ev.file.media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "X-Content-Type-Options": "nosniff"},
    )


# --- verification ----------------------------------------------------------------------


@router.post(
    "/milestones/{milestone_id}/precheck",
    openapi_extra={"x-mcp-tool": "precheck_evidence"},
    summary="Payee checks draft evidence against the criteria without submitting it",
)
def precheck(
    milestone_id: str, body: PrecheckRequest, auth: MilestonePartyDep, session: SessionDep, request: Request
) -> VerdictRead:
    party, m = auth
    require_role(party, Role.payee)
    return VerdictRead.model_validate(
        verification.precheck(session, ai(request), m, body.evidence, f"party:{party.id}")
    )


@router.post("/milestones/{milestone_id}/verify", summary="Re-run verification on the evidence already submitted")
def reverify(
    milestone_id: str, auth: MilestonePartyDep, request: Request, background: BackgroundTasks
) -> EvidenceAccepted:
    party, m = auth
    if m.status != MilestoneStatus.evidence_received:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tranche is {m.status}; nothing to verify")
    _verify_later(background, request, m.id, f"party:{party.id}")
    return EvidenceAccepted(evidence_id="", duplicate=False, milestone_status=MilestoneStatus(m.status))


@router.get(
    "/milestones/{milestone_id}/verdict",
    openapi_extra={"x-mcp-tool": "get_verdict"},
    summary="Latest verdict for a tranche, with per-criterion results and citations",
)
def get_verdict(milestone_id: str, auth: MilestonePartyDep) -> VerdictRead:
    _, m = auth
    v = verification.latest_verdict(m)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no verdict yet")
    return VerdictRead.model_validate(v)


# --- people ----------------------------------------------------------------------------


@router.post("/milestones/{milestone_id}/review", summary="Payer or approver approves or rejects a verdict in review")
def review(
    milestone_id: str, body: ReviewRequest, auth: MilestonePartyDep, session: SessionDep, request: Request
) -> ReviewOutcome:
    party, m = auth
    require_role(party, Role.payer, Role.approver)
    verification.review(session, gateway(request), m, party, body.decision == "approve", body.note, utcnow())
    session.refresh(m)
    v = verification.latest_verdict(m)
    missing = [] if body.decision == "approve" or v is None else [r for r in v.criteria_results if not r.met]
    return ReviewOutcome(milestone_status=MilestoneStatus(m.status), missing=missing)


@router.post(
    "/milestones/{milestone_id}/dispute",
    summary="Payee disputes a rejection; the payer side must review the tranche again",
)
def dispute(milestone_id: str, body: DisputeIn, auth: MilestonePartyDep, session: SessionDep) -> DisputePack:
    party, m = auth
    require_role(party, Role.payee)
    verification.dispute(session, m, party, body.reason)
    return DisputePack(
        milestone_status=MilestoneStatus(m.status), reason=body.reason, pack=milestone_section(session, m)
    )
