"""Evidence in (API and file uploads), verification, review and disputes."""

import hashlib
import re
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, Response, UploadFile, status
from sqlmodel import Field, SQLModel

from app.api.deps import (
    EvidenceAccessDep,
    HandoverAccessDep,
    MilestoneAccessDep,
    SessionDep,
    ai,
    gateway,
    rate_limit,
)
from app.api.views import EvidenceAccepted
from app.db import record_audit, utcnow
from app.integrations.github import GitHubError
from app.integrations.uploads import UploadError, parse_upload
from app.models import (
    CriterionResult,
    DeliverableRelease,
    EvidenceCreate,
    EvidenceRead,
    EvidenceVisibility,
    GitHubHandoverRead,
    Milestone,
    MilestoneStatus,
    Role,
    StoredFile,
    VerdictRead,
)
from app.services import custody, disclosure, github_app, handover, previews, verification
from app.services import files as stored
from app.services.access import AccessError, Action
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


class HandoverCheck(SQLModel):
    delivered_sha: str | None = Field(
        None,
        schema_extra={"pattern": r"^[0-9a-fA-F]{40}$"},
        description="Commit SHA the work was delivered as. Give this, or repo and ref.",
    )
    repo: str | None = Field(None, max_length=200, description="Repo it was handed over into, as owner/name.")
    ref: str | None = Field(None, max_length=200, description="Branch, tag or SHA in that repo.")


class HandoverResult(SQLModel):
    evidence_id: str
    pinned_ref: str = Field(description="The commit that was verified.")
    delivered_sha: str = Field(description="The commit that was delivered.")
    matches: bool = Field(description="Same commit, or a commit with exactly the same file tree.")
    how: str = Field(description="'same_commit', 'same_tree' or 'different'.")


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
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "submit_evidence"},
    summary="Payee submits evidence for a tranche; verification runs in the background",
)
def submit_evidence(
    milestone_id: str,
    body: EvidenceCreate,
    auth: MilestoneAccessDep,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
) -> EvidenceAccepted:
    access, m = auth
    access.require(Action.submit_evidence)
    row, created = verification.submit_evidence(session, m, body, source="api", actor=access.actor)
    if created and m.status == MilestoneStatus.evidence_received:
        _verify_later(background, request, m.id, "system:verifier")
    return EvidenceAccepted(evidence_id=row.id, duplicate=not created, milestone_status=MilestoneStatus(m.status))


@router.post(
    "/milestones/{milestone_id}/uploads",
    openapi_extra={"x-sdk-core": True},
    status_code=status.HTTP_202_ACCEPTED,
    summary="Payee uploads evidence files (PDF, CSV, XLSX, TXT, MD, images); verification runs in the background",
)
async def upload_evidence(
    milestone_id: str,
    auth: MilestoneAccessDep,
    session: SessionDep,
    request: Request,
    background: BackgroundTasks,
    files: Annotated[list[UploadFile], File(description="Up to 10 files, 20 MB each, 25 MB total.")],
    note: Annotated[str | None, Form(max_length=2000, description="Optional context, shown as a payee note.")] = None,
    dated: Annotated[date | None, Form(description="The evidence's own date, if the files don't carry one.")] = None,
    payer_visibility: Annotated[
        EvidenceVisibility | None,
        Form(description="on_capture agreements: what the payer sees of these files before capture."),
    ] = None,
    preview_pages: Annotated[
        str | None,
        Form(
            max_length=40,
            description="payer_visibility=preview: the PDF pages the payer's preview shows, e.g. '1-2,5' (at most "
            "5; default the first two). Images and spreadsheets get their own previews.",
        ),
    ] = None,
) -> list[EvidenceAccepted]:
    access, m = auth
    access.require(Action.submit_evidence)
    try:
        pages = previews.parse_pages(preview_pages)
    except previews.PreviewError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
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
    for item in parsed:
        item.evidence.payer_visibility = payer_visibility

    results = []
    for item in parsed:
        file_row = stored.save(session, item.data, filename=item.evidence.title, media_type=item.media_type)
        row, created = verification.submit_evidence(
            session, m, item.evidence, source="upload", actor=access.actor, file_id=file_row.id
        )
        if created and m.agreement.deliverable_release == DeliverableRelease.on_capture:
            previews.make(session, m, row, pages)
            session.commit()
        results.append(EvidenceAccepted(evidence_id=row.id, duplicate=not created, milestone_status=m.status))
    if any(not r.duplicate for r in results) and m.status == MilestoneStatus.evidence_received:
        _verify_later(background, request, m.id, "system:verifier")
    return results


@router.get(
    "/milestones/{milestone_id}/evidence",
    openapi_extra={"x-mcp-tool": "list_evidence"},
    summary="Evidence on file for a tranche",
)
def list_evidence(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep) -> list[EvidenceRead]:
    access, m = auth
    access.require(Action.view)
    shown = [disclosure.evidence_read(e, m, access.party.role) for e in m.evidence]
    disclosure.record_view(session, m, access.actor, access.party.role, [e.id for e in shown])
    return shown


class PayerView(SQLModel):
    withheld: bool = Field(description="True while the payer side gets the filtered view (on_capture, not captured).")
    evidence: list[EvidenceRead] = Field(description="The evidence list exactly as the payer side gets it.")
    verdict: VerdictRead | None = Field(description="The latest verdict exactly as the payer side gets it.")


@router.get(
    "/milestones/{milestone_id}/payer-view",
    summary="What the payer side sees of this tranche's evidence and verdict",
    description="For the payee (to see what they're showing) and reviewers (to judge what the payer is deciding "
    "on, next to the full evidence). The payer side gets its own view, which is the same thing.",
)
def payer_view(milestone_id: str, auth: MilestoneAccessDep) -> PayerView:
    access, m = auth
    access.require(Action.view)
    v = verification.latest_verdict(m)
    return PayerView(
        withheld=disclosure.restricted(Role.payer, m),
        evidence=[disclosure.evidence_read(e, m, Role.payer) for e in m.evidence],
        verdict=disclosure.verdict_read(v, m, Role.payer) if v else None,
    )


@router.get(
    "/evidence/{evidence_id}/file",
    summary="Download the original uploaded file",
    description="The response carries X-Content-SHA256 (the file's hash, checked against the hash recorded at "
    "upload, which is what the verifier read) and X-Pinned-Ref. On a deliverable_release=on_capture agreement the "
    "payer side can download only once the tranche is captured; each such download is recorded in the audit log.",
)
def download_file(evidence_id: str, auth: EvidenceAccessDep, session: SessionDep) -> Response:
    access, ev = auth
    access.require(Action.view)
    m = session.get(Milestone, ev.milestone_id)
    if disclosure.restricted(access.party.role, m):
        raise AccessError("the payee's files open to the payer side once this tranche is captured", 403)
    if ev.file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this evidence has no uploaded file")
    if why := custody.blocked(m, ev.file, access.party.role):
        raise HTTPException(status.HTTP_410_GONE, why)
    data = stored.read(ev.file)
    digest = hashlib.sha256(data).hexdigest()
    matches = digest == ev.file.sha256
    payer_after_reveal = (
        access.party.role == Role.payer and m.agreement.deliverable_release == DeliverableRelease.on_capture
    )
    on_capture = m.agreement.deliverable_release == DeliverableRelease.on_capture
    if payer_after_reveal or not matches or on_capture:
        record_audit(
            session,
            m.agreement_id,
            access.actor,
            ("deliverable_downloaded" if payer_after_reveal else "evidence_downloaded")
            if matches
            else "deliverable_mismatch",
            milestone_id=m.id,
            detail={
                "evidence_id": ev.id,
                "side": str(access.party.role),
                "sha256": digest,
                "committed_sha256": ev.file.sha256,
                "matches": matches,
            },
        )
        session.commit()
    if not matches:
        raise HTTPException(status.HTTP_409_CONFLICT, "the stored file no longer matches what was verified")
    safe_name = "".join(ch for ch in ev.file.filename if ch.isalnum() or ch in "._- ")[:100] or "evidence"
    return Response(
        data,
        media_type=ev.file.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": digest,
            "X-Pinned-Ref": ev.pinned_ref,
            "Access-Control-Expose-Headers": "X-Content-SHA256, X-Pinned-Ref",
        },
    )


@router.get(
    "/evidence/{evidence_id}/preview",
    summary="Download the payer-facing preview of an evidence file (payer_visibility=preview)",
    description="Derived from the original (watermarked PDF pages, a downscaled watermarked image, or a sheet's "
    "headers and totals), never the original itself. The payer side may open it before capture when the item's "
    "visibility is preview; the payee and reviewers always can, to see what the payer sees.",
    response_class=Response,
)
def download_preview(evidence_id: str, auth: EvidenceAccessDep, session: SessionDep) -> Response:
    access, ev = auth
    access.require(Action.view)
    m = session.get(Milestone, ev.milestone_id)
    f = session.get(StoredFile, ev.preview_file_id) if ev.preview_file_id else None
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this evidence has no preview")
    if why := custody.blocked(m, f, access.party.role):
        raise HTTPException(status.HTTP_410_GONE, why)
    if disclosure.restricted(access.party.role, m) and ev.payer_visibility != EvidenceVisibility.preview:
        raise AccessError("the payee didn't share a preview of this item with the payer side", 403)
    if m.agreement.deliverable_release == DeliverableRelease.on_capture:
        record_audit(
            session,
            m.agreement_id,
            access.actor,
            "preview_downloaded",
            milestone_id=m.id,
            detail={"evidence_id": ev.id, "side": str(access.party.role), "sha256": f.sha256},
        )
        session.commit()
    return Response(
        stored.read(f),
        media_type=f.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="preview-{ev.id[:8]}{_ext(f.media_type)}"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": f.sha256,
            "Access-Control-Expose-Headers": "X-Content-SHA256",
        },
    )


def _ext(media_type: str) -> str:
    return {"application/pdf": ".pdf", "image/jpeg": ".jpg", "text/plain": ".txt"}.get(media_type, "")


_PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/(?:pull/\d+|commit/[0-9a-f]{40})")


@router.post(
    "/evidence/{evidence_id}/handover",
    summary="Check that code handed over outside the engine is the commit that was verified",
    description="For code evidence pinned to a commit. Give the delivered commit SHA, or the repo and ref it was "
    "handed over into (looked up on GitHub). It matches when it is the same commit, or a commit with exactly the "
    "same file tree (e.g. squashed into the client's repo). The result is recorded in the audit log and the "
    "compliance packet.",
)
def check_handover(
    evidence_id: str, body: HandoverCheck, auth: EvidenceAccessDep, session: SessionDep, request: Request
) -> HandoverResult:
    access, ev = auth
    access.require(Action.view)
    pinned = ev.pinned_ref.lower()
    if not re.fullmatch(r"[0-9a-f]{40}", pinned):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "this evidence isn't pinned to a commit")
    state = request.app.state
    orgs = [p.org_id or "" for p in access.party.agreement.parties]

    def reader(repo: str):  # an installation on the agreement that can read it, else the server's token
        return github_app.client_for_repo(state.github_app, state.github, session, orgs, repo)

    try:
        if body.repo and body.ref:
            delivered = reader(body.repo).commit(body.repo, body.ref)
            delivered_sha, delivered_tree = delivered["sha"].lower(), delivered["commit"]["tree"]["sha"]
        elif body.delivered_sha:
            delivered_sha, delivered_tree = body.delivered_sha.lower(), None
        else:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give delivered_sha, or repo and ref")
        how = "same_commit" if delivered_sha == pinned else "different"
        source = _PR_URL.match(ev.external_ref)
        if how == "different" and delivered_tree and source:
            verified_tree = reader(source.group(1)).commit(source.group(1), pinned)["commit"]["tree"]["sha"]
            how = "same_tree" if verified_tree == delivered_tree else "different"
    except GitHubError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"couldn't check on GitHub: {e}") from e
    m = session.get(Milestone, ev.milestone_id)
    record_audit(
        session,
        m.agreement_id,
        access.actor,
        "handover_checked",
        milestone_id=m.id,
        detail={
            "evidence_id": ev.id,
            "pinned_ref": pinned,
            "delivered_sha": delivered_sha,
            "repo": body.repo,
            "ref": body.ref,
            "matches": how != "different",
            "how": how,
        },
    )
    session.commit()
    return HandoverResult(
        evidence_id=ev.id, pinned_ref=pinned, delivered_sha=delivered_sha, matches=how != "different", how=how
    )


@router.get(
    "/milestones/{milestone_id}/handovers",
    summary="Code handed over from the contractor's repo once the tranche was paid (contractor_repo integrations)",
)
def list_handovers(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep) -> list[GitHubHandoverRead]:
    access, m = auth
    access.require(Action.view)
    return [handover.read(r, session) for r in handover.for_milestone(session, m.id)]


@router.post("/handovers/{handover_id}/retry", summary="Try a waiting or failed handover again now (either party)")
def retry_handover(handover_id: str, auth: HandoverAccessDep, session: SessionDep) -> GitHubHandoverRead:
    access, row, _ = auth
    access.require(Action.view)
    try:
        handover.retry(session, row, access.actor)
    except handover.HandoverError as e:
        raise HTTPException(e.status_code, e.message) from e
    return handover.read(row, session)


@router.get(
    "/handovers/{handover_id}/archive",
    summary="Download the source archive handed over for a paid tranche (handover=archive)",
    description="The archive's files were checked against the verified commit's git tree before it was kept. "
    "X-Content-SHA256 is its SHA-256; X-Git-Tree is the tree it reproduces.",
    response_class=Response,
)
def download_handover(handover_id: str, auth: HandoverAccessDep, session: SessionDep) -> Response:
    access, row, m = auth
    access.require(Action.view)
    f = session.get(StoredFile, row.file_id) if row.file_id else None
    if row.status != "delivered" or f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no archive has been handed over for this yet")
    if why := custody.blocked(m, f, access.party.role):
        raise HTTPException(status.HTTP_410_GONE, why)
    data = stored.read(f)
    digest = hashlib.sha256(data).hexdigest()
    record_audit(
        session,
        m.agreement_id,
        access.actor,
        "deliverable_downloaded" if digest == f.sha256 else "deliverable_mismatch",
        milestone_id=m.id,
        detail={"handover_id": row.id, "sha256": digest, "committed_sha256": f.sha256, "tree_sha": row.tree_sha},
    )
    session.commit()
    if digest != f.sha256:
        raise HTTPException(status.HTTP_409_CONFLICT, "the stored archive no longer matches what was handed over")
    return Response(
        data,
        media_type=f.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{f.filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": digest,
            "X-Pinned-Ref": row.pinned_sha,
            "X-Git-Tree": row.tree_sha or "",
            "Access-Control-Expose-Headers": "X-Content-SHA256, X-Pinned-Ref, X-Git-Tree",
        },
    )


# --- verification ----------------------------------------------------------------------


@router.post(
    "/milestones/{milestone_id}/precheck",
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "precheck_evidence"},
    summary="Payee checks draft evidence against the criteria without submitting it",
)
def precheck(
    milestone_id: str, body: PrecheckRequest, auth: MilestoneAccessDep, session: SessionDep, request: Request
) -> VerdictRead:
    access, m = auth
    access.require(Action.submit_evidence)
    rate_limit(request, "precheck", access.principal.actor)
    return VerdictRead.model_validate(verification.precheck(session, ai(request), m, body.evidence, access.actor))


@router.post("/milestones/{milestone_id}/verify", summary="Re-run verification on the evidence already submitted")
def reverify(
    milestone_id: str, auth: MilestoneAccessDep, request: Request, background: BackgroundTasks
) -> EvidenceAccepted:
    access, m = auth
    access.require(Action.view)
    if m.status != MilestoneStatus.evidence_received:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tranche is {m.status}; nothing to verify")
    _verify_later(background, request, m.id, access.actor)
    return EvidenceAccepted(evidence_id="", duplicate=False, milestone_status=MilestoneStatus(m.status))


@router.get(
    "/milestones/{milestone_id}/verdict",
    openapi_extra={"x-sdk-core": True, "x-mcp-tool": "get_verdict"},
    summary="Latest verdict for a tranche, with per-criterion results and citations",
)
def get_verdict(milestone_id: str, auth: MilestoneAccessDep) -> VerdictRead:
    access, m = auth
    access.require(Action.view)
    v = verification.latest_verdict(m)
    if v is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no verdict yet")
    return disclosure.verdict_read(v, m, access.party.role)


# --- people ----------------------------------------------------------------------------


@router.post(
    "/milestones/{milestone_id}/review",
    openapi_extra={"x-sdk-core": True},
    summary="Payer (admin) or reviewer approves or rejects a verdict in review",
)
def review(
    milestone_id: str, body: ReviewRequest, auth: MilestoneAccessDep, session: SessionDep, request: Request
) -> ReviewOutcome:
    access, m = auth
    access.require(Action.review)
    if body.decision == "approve" and m.agreement.require_reviewer_approval and access.party.role != Role.reviewer:
        raise AccessError("this agreement requires a reviewer's approval; the payer can still reject", 403)
    verification.review(session, gateway(request), m, access.actor, body.decision == "approve", body.note, utcnow())
    session.refresh(m)
    v = verification.latest_verdict(m)
    missing = [] if body.decision == "approve" else disclosure.missing_for(v, m, access.party.role)
    return ReviewOutcome(milestone_status=MilestoneStatus(m.status), missing=missing)


@router.post(
    "/milestones/{milestone_id}/dispute",
    summary="Payee disputes a rejection; the payer side must review the tranche again",
)
def dispute(milestone_id: str, body: DisputeIn, auth: MilestoneAccessDep, session: SessionDep) -> DisputePack:
    access, m = auth
    access.require(Action.dispute)
    verification.dispute(session, m, access.actor, body.reason)
    return DisputePack(
        milestone_status=MilestoneStatus(m.status), reason=body.reason, pack=milestone_section(session, m)
    )
