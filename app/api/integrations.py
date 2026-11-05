"""Connections to the outside: GitHub as an evidence source, and outbound webhook subscriptions."""

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from sqlmodel import Field, SQLModel, select

from app.api.deps import AgreementAccessDep, SessionDep
from app.db import record_audit
from app.integrations import github
from app.models import (
    AgreementStatus,
    Integration,
    Milestone,
    MilestoneStatus,
    Role,
    Subscription,
    SubscriptionCreate,
    SubscriptionCreated,
    SubscriptionRead,
    WebhookEvent,
)
from app.services import verification
from app.services.access import Action
from app.services.outbound import DestinationError, check_destination

router = APIRouter()


# --- GitHub ----------------------------------------------------------------------------


class GitHubRegistration(SQLModel):
    repo: str = Field(..., description="Repository as 'owner/name'.")


class MilestoneLabel(SQLModel):
    milestone_id: str
    title: str
    label: str = Field(..., description="Add this label to a PR so its merge counts as evidence for the milestone.")


class GitHubIntegration(SQLModel):
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
    agreement_id: str, body: GitHubRegistration, access: AgreementAccessDep, session: SessionDep, request: Request
) -> GitHubIntegration:
    access.require(Action.connect_github)
    a = access.party.agreement
    if a.status in (AgreementStatus.cancelled, AgreementStatus.completed):
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
            created_by=access.actor,
        )
        session.add(row)
        session.flush()
        record_audit(session, a.id, access.actor, "github_connected", detail={"repo": body.repo})
        session.commit()
    base = request.app.state.settings.api_base_url
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
            if created and m.status == MilestoneStatus.evidence_received:
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


@router.post(
    "/agreements/{agreement_id}/subscriptions",
    openapi_extra={"x-sdk-core": True},
    tags=["events"],
    summary="Subscribe a URL to this agreement's events",
)
def subscribe(
    agreement_id: str, body: SubscriptionCreate, access: AgreementAccessDep, session: SessionDep, request: Request
) -> SubscriptionCreated:
    access.require(Action.manage_subscriptions)
    state = request.app.state
    try:
        check_destination(body.url, allow_http=state.settings.is_mock, resolve=state.resolve)
    except DestinationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    sub = Subscription.model_validate(
        body,
        update={"agreement_id": agreement_id, "secret": secrets.token_hex(32), "created_by": access.actor},
    )
    session.add(sub)
    session.flush()
    record_audit(session, agreement_id, access.actor, "subscription_created", detail={"url": body.url})
    session.commit()
    return SubscriptionCreated.model_validate(sub)


@router.get(
    "/agreements/{agreement_id}/subscriptions",
    openapi_extra={"x-sdk-core": True},
    tags=["events"],
    summary="Subscriptions on this agreement",
)
def list_subscriptions(agreement_id: str, access: AgreementAccessDep, session: SessionDep) -> list[SubscriptionRead]:
    access.require(Action.view)
    subs = session.exec(select(Subscription).where(Subscription.agreement_id == agreement_id)).all()
    return [SubscriptionRead.model_validate(s) for s in subs]


@router.delete(
    "/agreements/{agreement_id}/subscriptions/{subscription_id}",
    openapi_extra={"x-sdk-core": True},
    tags=["events"],
    status_code=204,
)
def unsubscribe(agreement_id: str, subscription_id: str, access: AgreementAccessDep, session: SessionDep) -> Response:
    access.require(Action.manage_subscriptions)
    sub = session.get(Subscription, subscription_id)
    if sub is None or sub.agreement_id != agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    if sub.created_by != access.actor and access.party.role != Role.payer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only its creator or the payer can remove a subscription")
    sub.active = False
    record_audit(session, agreement_id, access.actor, "subscription_removed", detail={"url": sub.url})
    session.commit()
    return Response(status_code=204)
