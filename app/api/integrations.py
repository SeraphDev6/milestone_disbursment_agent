"""Connections to the outside: GitHub as an evidence source, and outbound webhook subscriptions."""

import json
import secrets
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.api.deps import PartyDep, SessionDep, require_role
from app.config import Mode
from app.db import Integration, Milestone, Subscription, WebhookEvent, record_audit
from app.domain.models import AgreementStatus, MilestoneStatus, Role
from app.integrations import github
from app.services import verification
from app.services.outbound import DestinationError, check_destination

router = APIRouter()


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
    tags=["integrations"],
    summary="Payee connects a GitHub repo: merged PRs labelled with a milestone become its evidence",
)
def register_github(
    agreement_id: str, body: GitHubRegistration, party: PartyDep, session: SessionDep, request: Request
) -> GitHubIntegration:
    require_role(party, Role.payee)
    a = party.agreement
    if a.status in (AgreementStatus.cancelled.value, AgreementStatus.completed.value):
        raise HTTPException(status.HTTP_409_CONFLICT, f"agreement is {a.status}")
    if not github.valid_repo(body.repo):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "repo must look like 'owner/name'")
    row = session.exec(
        select(Integration).where(
            Integration.agreement_id == a.id, Integration.kind == "github", Integration.target == body.repo
        )
    ).first()
    if row is None:
        row = Integration(
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
        integration = session.get(Integration, integration_id)
        for mid in milestone_ids:
            m = session.get(Milestone, mid)
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
        verification.verify_in_background(state.sessionmaker, state.ai, state.paypal, mid, "system:verifier")


@router.post(
    "/webhooks/github/{integration_id}", tags=["integrations"], summary="GitHub repo webhook intake (HMAC-verified)"
)
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
    integration = session.get(Integration, integration_id)
    if integration is None or integration.kind != "github":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown integration")
    if not github.signature_ok(integration.secret, raw, x_hub_signature_256):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature doesn't match")
    if x_github_event == "ping":
        return {"outcome": "pong"}
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-GitHub-Delivery header required")
    delivery_id = f"github:{x_github_delivery}"
    if session.get(WebhookEvent, delivery_id) is not None:
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
            m.id for m in session.exec(select(Milestone).where(Milestone.agreement_id == integration.agreement_id))
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
    session.add(WebhookEvent(id=delivery_id, event_type=f"github.{x_github_event}", resource_id=None, outcome=outcome))
    session.commit()
    return {"outcome": outcome}


# --- outbound webhook subscriptions ----------------------------------------------------


class SubscriptionIn(BaseModel):
    url: str = Field(..., max_length=1000, description="HTTPS endpoint that receives signed JSON events.")
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Event types (milestone.status_changed), prefixes ending in '.' (milestone.), or '*'.",
    )


class SubscriptionView(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class SubscriptionCreated(SubscriptionView):
    secret: str = Field(
        ..., description="Shown once. Verify X-MDE-Signature with HMAC-SHA256(secret, ts + '.' + body)."
    )


def _sub_view(s: Subscription) -> SubscriptionView:
    return SubscriptionView(id=s.id, url=s.url, events=s.events, active=s.active, created_at=s.created_at)


@router.post(
    "/agreements/{agreement_id}/subscriptions", tags=["events"], summary="Subscribe a URL to this agreement's events"
)
def subscribe(
    agreement_id: str, body: SubscriptionIn, party: PartyDep, session: SessionDep, request: Request
) -> SubscriptionCreated:
    state = request.app.state
    try:
        check_destination(body.url, allow_http=state.settings.mode is Mode.mock, resolve=state.resolve)
    except DestinationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    sub = Subscription(
        agreement_id=agreement_id,
        url=body.url,
        events=body.events or ["*"],
        secret=secrets.token_hex(32),
        created_by=f"party:{party.id}",
    )
    session.add(sub)
    session.flush()
    record_audit(session, agreement_id, f"party:{party.id}", "subscription_created", detail={"url": body.url})
    session.commit()
    return SubscriptionCreated(**_sub_view(sub).model_dump(), secret=sub.secret)


@router.get("/agreements/{agreement_id}/subscriptions", tags=["events"], summary="Subscriptions on this agreement")
def list_subscriptions(agreement_id: str, party: PartyDep, session: SessionDep) -> list[SubscriptionView]:
    subs = session.exec(select(Subscription).where(Subscription.agreement_id == agreement_id)).all()
    return [_sub_view(s) for s in subs]


@router.delete("/agreements/{agreement_id}/subscriptions/{subscription_id}", tags=["events"], status_code=204)
def unsubscribe(agreement_id: str, subscription_id: str, party: PartyDep, session: SessionDep) -> Response:
    sub = session.get(Subscription, subscription_id)
    if sub is None or sub.agreement_id != agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    if sub.created_by != f"party:{party.id}" and party.role != Role.payer.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only its creator or the payer can remove a subscription")
    sub.active = False
    record_audit(session, agreement_id, f"party:{party.id}", "subscription_removed", detail={"url": sub.url})
    session.commit()
    return Response(status_code=204)
