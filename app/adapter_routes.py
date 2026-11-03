import json
import secrets
from datetime import date, datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import verification
from app.adapters import github
from app.adapters.upload import UploadError, parse_upload
from app.db import EvidenceRow, FileRow, IntegrationRow, MilestoneRow, PartyRow, WebhookEventRow, record_audit, utcnow
from app.domain import AgreementStatus, EvidenceType, MilestoneStatus, Role
from app.escrow_routes import _require
from app.evidence_routes import EvidenceAccepted, _verify_in_background
from app.routes import MilestonePartyDep, PartyDep, SessionDep, _hash_key

router = APIRouter(tags=["adapters"])

MAX_FILES = 10
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_REQUEST_BYTES = 25 * 1024 * 1024


# --- GitHub ----------------------------------------------------------------------------


class GitHubRegistration(BaseModel):
    repo: str = Field(..., description="Repository as 'owner/name'.")


class MilestoneLabel(BaseModel):
    milestone_id: str
    title: str
    label: str = Field(..., description="Add this label to a PR so its merge counts as evidence for the milestone.")


class GitHubIntegration(BaseModel):
    integration_id: str
    repo: str
    webhook_url: str = Field(..., description="Repo Settings > Webhooks > Payload URL.")
    secret: str = Field(..., description="Repo Settings > Webhooks > Secret.")
    content_type: str = "application/json"
    events: list[str] = ["pull_request"]
    labels: list[MilestoneLabel]


@router.post(
    "/agreements/{agreement_id}/integrations/github",
    summary="Payee connects a GitHub repo: merged PRs labelled with a milestone become its evidence",
)
def register_github(
    agreement_id: str, body: GitHubRegistration, party: PartyDep, session: SessionDep, request: Request
) -> GitHubIntegration:
    _require(party, Role.payee)
    a = party.agreement
    if a.status in (AgreementStatus.cancelled.value, AgreementStatus.completed.value):
        raise HTTPException(status.HTTP_409_CONFLICT, f"agreement is {a.status}")
    if not github.valid_repo(body.repo):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "repo must look like 'owner/name'")
    row = session.scalar(
        select(IntegrationRow).where(
            IntegrationRow.agreement_id == a.id, IntegrationRow.kind == "github", IntegrationRow.target == body.repo
        )
    )
    if row is None:
        row = IntegrationRow(
            agreement_id=a.id,
            kind="github",
            target=body.repo,
            secret=secrets.token_hex(32),
            created_by=f"party:{party.id}",
        )
        session.add(row)
        session.flush()
        record_audit(session, a.id, f"party:{party.id}", "github_connected", detail={"repo": body.repo})
        session.commit()
    base = request.app.state.settings.public_base_url
    return GitHubIntegration(
        integration_id=row.id,
        repo=row.target,
        webhook_url=f"{base}/webhooks/github/{row.id}",
        secret=row.secret,
        labels=[MilestoneLabel(milestone_id=m.id, title=m.title, label=github.label_for(m.id)) for m in a.milestones],
    )


def _ingest_pr(state, integration_id: str, pr: github.MergedPR, milestone_ids: list[str]) -> None:
    """Background: fetch CI and files from GitHub, store evidence per milestone, verify."""
    evidence, facts = github.build_evidence(pr, state.github)
    actor = f"github:{pr.merged_by}"
    to_verify = []
    with state.sessionmaker() as session:
        integration = session.get(IntegrationRow, integration_id)
        for mid in milestone_ids:
            m = session.get(MilestoneRow, mid)
            if m is None or integration is None or m.agreement_id != integration.agreement_id:
                continue
            try:
                _, created = verification.submit_evidence(
                    session, m, evidence, source="github", actor=actor, check_results=facts, pinned_ref=pr.merge_sha
                )
            except verification.VerificationError as e:
                record_audit(
                    session,
                    m.agreement_id,
                    actor,
                    "github_evidence_refused",
                    milestone_id=m.id,
                    detail={"pr": pr.url, "reason": e.message},
                )
                session.commit()
                continue
            if created and m.status == MilestoneStatus.evidence_received.value:
                to_verify.append(m.id)
    for mid in to_verify:
        with state.sessionmaker() as session:
            verification.run_verification(session, state.ai, state.paypal, mid, "system:verifier", utcnow())


@router.post("/webhooks/github/{integration_id}", summary="GitHub repo webhook intake (HMAC-verified)")
async def github_webhook(
    integration_id: str,
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    raw = await request.body()
    integration = session.get(IntegrationRow, integration_id)
    if integration is None or integration.kind != "github":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown integration")
    if not github.signature_ok(integration.secret, raw, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature doesn't match")
    if x_github_event == "ping":
        return {"outcome": "pong"}
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-GitHub-Delivery header required")
    delivery_id = f"github:{x_github_delivery}"
    if session.get(WebhookEventRow, delivery_id) is not None:
        return {"outcome": "duplicate"}
    try:
        payload = json.loads(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from e

    pr = github.parse_merged_pr(x_github_event, payload)
    if pr is None:
        outcome = "ignored: not a merged pull request"
    elif pr.repo.lower() != integration.target.lower():
        outcome = f"ignored: {pr.repo} is not the registered repo"
    else:
        agreement_milestones = {
            m.id
            for m in session.scalars(select(MilestoneRow).where(MilestoneRow.agreement_id == integration.agreement_id))
        }
        matched = [mid for mid in pr.milestone_ids() if mid in agreement_milestones]
        if not matched:
            outcome = "ignored: no milestone label"
            record_audit(
                session,
                integration.agreement_id,
                f"github:{pr.merged_by}",
                "github_unmatched",
                detail={"pr": pr.url, "labels": pr.labels},
            )
        else:
            outcome = f"queued for {len(matched)} milestone(s)"
            background.add_task(_ingest_pr, request.app.state, integration.id, pr, matched)
    session.add(
        WebhookEventRow(id=delivery_id, event_type=f"github.{x_github_event}", resource_id=None, outcome=outcome)
    )
    session.commit()
    return {"outcome": outcome}


# --- Uploads ---------------------------------------------------------------------------


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
    _require(party, Role.payee)
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
        file_row = session.scalar(select(FileRow).where(FileRow.sha256 == item.sha256)) or FileRow(
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
    if any(not r.duplicate for r in results) and m.status == MilestoneStatus.evidence_received.value:
        background.add_task(_verify_in_background, request, m.id, "system:verifier")
    return results


# --- Reading evidence back ------------------------------------------------------------


class EvidenceSummary(BaseModel):
    id: str
    source: str
    evidence_type: EvidenceType
    title: str
    excerpt: str = Field(..., description="First 500 characters of the text the checks and verifier read.")
    dated: date | None
    check_results: dict[str, bool]
    artifact_uris: list[str]
    has_file: bool
    received_at: datetime


@router.get(
    "/milestones/{milestone_id}/evidence",
    openapi_extra={"x-mcp-tool": "list_evidence"},
    summary="Evidence on file for a tranche",
)
def list_evidence(milestone_id: str, auth: MilestonePartyDep) -> list[EvidenceSummary]:
    _, m = auth
    return [
        EvidenceSummary(
            id=e.id,
            source=e.source,
            evidence_type=EvidenceType(e.evidence_type),
            title=e.title,
            excerpt=e.content[:500],
            dated=e.dated,
            check_results=e.check_results,
            artifact_uris=e.artifact_uris,
            has_file=e.file_id is not None,
            received_at=e.received_at,
        )
        for e in m.evidence
    ]


def _evidence_party(
    evidence_id: str, session: SessionDep, x_api_key: Annotated[str | None, Header()] = None
) -> tuple[PartyRow, EvidenceRow]:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
    party = session.scalar(select(PartyRow).where(PartyRow.api_key_hash == _hash_key(x_api_key)))
    ev = session.get(EvidenceRow, evidence_id)
    m = session.get(MilestoneRow, ev.milestone_id) if ev else None
    if party is None or ev is None or m is None or m.agreement_id != party.agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found")
    return party, ev


@router.get("/evidence/{evidence_id}/file", summary="Download the original uploaded file")
def download_file(
    evidence_id: str, auth: Annotated[tuple[PartyRow, EvidenceRow], Depends(_evidence_party)]
) -> Response:
    _, ev = auth
    if ev.file is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this evidence has no uploaded file")
    safe_name = "".join(ch for ch in ev.file.filename if ch.isalnum() or ch in "._- ")[:100] or "evidence"
    return Response(
        ev.file.data,
        media_type=ev.file.media_type,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "X-Content-Type-Options": "nosniff"},
    )
