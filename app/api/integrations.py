"""Connections to the outside: GitHub as an evidence source, and outbound webhook subscriptions."""

import json
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from sqlmodel import Field, SQLModel, select

from app.api.deps import AgreementAccessDep, SessionDep
from app.db import record_audit
from app.integrations import github
from app.models import (
    AgreementStatus,
    DeliverableRelease,
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
from app.services import github_app, verification
from app.services.access import Action
from app.services.outbound import DestinationError, check_destination

router = APIRouter()


# --- GitHub ----------------------------------------------------------------------------


class GitHubRegistration(SQLModel):
    repo: str = Field(..., description="Repository as 'owner/name'.")
    use_app: bool = Field(
        default=True,
        description="Connect through the GitHub App when one of your org's installations can read the repo (no "
        "webhook to configure). False forces the hand-configured repo webhook.",
    )
    mode: Literal["merged_pr", "contractor_repo"] | None = Field(
        default=None,
        description="merged_pr: merged, labelled PRs in this repo are the evidence; the client usually owns it, so "
        "it has the code at merge. contractor_repo: this is your private repo; a labelled PR, or a release or tag "
        "named `mde-<milestone id>`, pins the commit, and the code goes to the client only once the tranche is paid. "
        "Defaults to contractor_repo when the agreement withholds work until capture, else merged_pr.",
    )
    handover: Literal["pull_request", "archive", "transfer"] = Field(
        default="pull_request",
        description="contractor_repo: how the code reaches the client once paid. pull_request opens a PR into "
        "client_repo (the client installs the GitHub App on it); archive keeps a source archive for them to download; "
        "transfer: you transfer this repo to the client's GitHub account and the app only watches for it. A transfer "
        "with the verified commit on the default branch completes any of the three.",
    )
    client_repo: str | None = Field(
        default=None, max_length=200, description="contractor_repo with handover=pull_request: the client's repo."
    )


class MilestoneLabel(SQLModel):
    milestone_id: str
    title: str
    label: str = Field(..., description="Add this label to a PR so its merge counts as evidence for the milestone.")


class GitHubIntegration(SQLModel):
    integration_id: str
    repo: str
    via_app: bool = Field(
        default=False, description="Connected through the GitHub App: merged PRs arrive with no repo webhook."
    )
    webhook_url: str | None = Field(None, description="Repo Settings > Webhooks > Payload URL. None via the app.")
    secret: str | None = Field(None, description="Repo Settings > Webhooks > Secret. None via the app.")
    content_type: str = "application/json"
    events: list[str] = ["pull_request"]
    labels: list[MilestoneLabel]
    mode: str = Field(default="merged_pr", description="merged_pr or contractor_repo.")
    handover: str | None = Field(default=None, description="contractor_repo: pull_request, archive or transfer.")
    client_repo: str | None = None
    handover_ready: bool | None = Field(
        default=None,
        description="pull_request: whether the client's org has the GitHub App on client_repo yet. transfer: whether "
        "it has the app on any GitHub account to transfer to. The handover waits (and is retried) until it does.",
    )
    transfer_to: list[str] = Field(
        default_factory=list,
        description="contractor_repo: the client's GitHub accounts with the app, where a transfer is recognised.",
    )


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
    on_capture = a.deliverable_release == DeliverableRelease.on_capture
    mode = body.mode or ("contractor_repo" if on_capture else "merged_pr")
    app = request.app.state.github_app
    installation_id = None
    repo_id = None
    if body.use_app or mode == "contractor_repo":
        rows = github_app.installations(session, [access.party.org_id or ""])
        installation_id = github_app.installation_for_repo(app, rows, body.repo)
    payer_orgs = [p.org_id or "" for p in a.parties if p.role == Role.payer]
    handover_ready = None
    if mode == "merged_pr" and on_capture:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this agreement withholds the work until the tranche is paid, but merged PRs put the code in the "
            "registered repo straight away; connect your own private repo with mode=contractor_repo",
        )
    if mode == "contractor_repo":
        if not on_capture:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "contractor_repo is for agreements that withhold the work until capture (deliverable_release "
                "on_capture); this one releases it on submit, so connect the repo with mode=merged_pr",
            )
        if installation_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"install the GitHub App on {body.repo} from your organization's settings first; a contractor repo "
                "is read through the app",
            )
        repo_id = _check_withheld(app, session, installation_id, payer_orgs, body.repo)
        if body.handover == "pull_request":
            if not body.client_repo or not github.valid_repo(body.client_repo):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "handover=pull_request needs client_repo as 'owner/name'"
                )
            if body.client_repo.lower() == body.repo.lower():
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "client_repo must be the client's repo")
            payer_installs = github_app.installations(session, payer_orgs)
            handover_ready = github_app.installation_for_repo(app, payer_installs, body.client_repo) is not None
        elif body.handover == "transfer":
            handover_ready = bool(github_app.installations(session, payer_orgs))
    handover = body.handover if mode == "contractor_repo" else None
    client_repo = body.client_repo if handover == "pull_request" else None
    row = session.exec(
        select(Integration).where(
            Integration.agreement_id == a.id, Integration.kind == "github", Integration.target == body.repo
        )
    ).first()
    detail = {"repo": body.repo, "via_app": bool(installation_id), "mode": mode, "handover": handover}
    if row is None:
        row = Integration(
            agreement_id=a.id,
            kind="github",
            target=body.repo,
            secret=secrets.token_hex(32),
            created_by=access.actor,
            installation_id=installation_id,
            mode=mode,
            handover=handover,
            handover_repo=client_repo,
            repo_id=repo_id,
        )
        session.add(row)
        session.flush()
        record_audit(session, a.id, access.actor, "github_connected", detail=detail)
        session.commit()
    elif (
        (installation_id and row.installation_id != installation_id)
        or ((row.mode, row.handover, row.handover_repo) != (mode, handover, client_repo))
        or (repo_id and row.repo_id != repo_id)
    ):
        row.installation_id = installation_id or row.installation_id  # switched to the app
        row.mode, row.handover, row.handover_repo = mode, handover, client_repo
        row.repo_id = repo_id or row.repo_id
        record_audit(session, a.id, access.actor, "github_connected", detail=detail)
        session.commit()
    base = request.app.state.settings.api_base_url
    via_app = row.installation_id is not None
    return GitHubIntegration(
        integration_id=row.id,
        repo=row.target,
        via_app=via_app,
        webhook_url=None if via_app else f"{base}/webhooks/github/{row.id}",
        secret=None if via_app else row.secret,
        labels=[MilestoneLabel(milestone_id=m.id, title=m.title, label=github.label_for(m.id)) for m in a.milestones],
        mode=row.mode,
        handover=row.handover,
        client_repo=row.handover_repo,
        handover_ready=handover_ready,
        transfer_to=sorted({i.account_login for i in github_app.installations(session, payer_orgs)})
        if row.mode == "contractor_repo"
        else [],
        events=["pull_request", "release", "push"] if row.mode == "contractor_repo" else ["pull_request"],
    )


def _check_withheld(app, session, installation_id: int, payer_orgs: list[str], repo: str) -> int | None:
    """A contractor repo only withholds the code if the client can't read it: private, and none of the client
    org's own installations can see it. Returns GitHub's id for the repo."""
    try:
        info = app.client(installation_id).repo(repo)
    except github.GitHubError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"couldn't look up {repo} on GitHub: {e}") from e
    if not info.get("private"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{repo} is public, so the client can already read the code; make it private"
        )
    if github_app.installation_for_repo(app, github_app.installations(session, payer_orgs), repo) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"the client's organization can already read {repo} through its GitHub App installation, so the code "
            "isn't withheld; use a repo only you can read",
        )
    return info.get("id")


def ingest_pr(state, integration_id: str, pr: github.MergedPR, milestone_ids: list[str]) -> None:
    """Background: fetch CI and files from GitHub, store evidence per milestone, verify. Through the app, reads use
    the installation's token and a check run on the merge commit follows each tranche."""
    api = _reader(state, integration_id)
    evidence, facts = github.build_evidence(pr, api)
    _store(
        state, integration_id, evidence, facts, pr.merge_sha, pr.repo, f"github:{pr.merged_by}", milestone_ids, pr.url
    )


def ingest_tag(state, integration_id: str, tc: github.TaggedCommit, milestone_ids: list[str]) -> None:
    """Background: a release or tag in a contractor repo. The commit it points to is the evidence."""
    api = _reader(state, integration_id)
    actor = f"github:{tc.author}"
    try:
        evidence, facts, sha = github.build_commit_evidence(tc, api)
    except github.GitHubError as e:
        with state.sessionmaker() as session:
            integration = session.get(Integration, integration_id)
            if integration is not None:
                record_audit(
                    session,
                    integration.agreement_id,
                    actor,
                    "github_evidence_refused",
                    detail={"tag": tc.tag, "repo": tc.repo, "reason": f"couldn't read the tagged commit: {e}"},
                )
                session.commit()
        return
    _store(state, integration_id, evidence, facts, sha, tc.repo, actor, milestone_ids, tc.url)


def _reader(state, integration_id: str):
    with state.sessionmaker() as session:
        integration = session.get(Integration, integration_id)
        installation_id = integration.installation_id if integration else None
    app = getattr(state, "github_app", None)
    return app.client(installation_id) if app is not None and installation_id else state.github


def _store(state, integration_id, evidence, facts, sha: str, repo: str, actor: str, milestone_ids, url: str) -> None:
    app = getattr(state, "github_app", None)
    to_verify = []
    with state.sessionmaker() as session:
        integration = session.get(Integration, integration_id)
        for mid in milestone_ids:
            m = session.get(Milestone, mid)
            if m is None or integration is None or m.agreement_id != integration.agreement_id:
                continue
            try:
                _, created = verification.submit_evidence(
                    session, m, evidence, source="github", actor=actor, check_results=facts, pinned_ref=sha
                )
            except verification.VerificationError as e:
                record_audit(
                    session,
                    m.agreement_id,
                    actor,
                    "github_evidence_refused",
                    milestone_id=m.id,
                    detail={"source": url, "reason": e.message},
                )
                session.commit()
                continue
            if integration.installation_id and app is not None:
                github_app.track(session, m, integration.installation_id, repo, sha)
                session.commit()
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
            background.add_task(ingest_pr, request.app.state, integration.id, pr, matched)
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
