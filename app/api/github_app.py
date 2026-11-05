"""The GitHub App: installing it for an org, the repos it can read, and its webhook."""

import json
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Field, SQLModel, select

from app.api.deps import PrincipalDep, SessionDep
from app.api.views import html_page
from app.db import record_audit, utcnow
from app.integrations import github
from app.models import GitHubInstallationRead, Integration, Milestone, OrgRole, WebhookEvent
from app.services import github_app as service

router = APIRouter(tags=["github"])


class GitHubOrgStatus(SQLModel):
    app_available: bool = Field(description="This deployment has a GitHub App orgs can install.")
    app_slug: str | None = Field(default=None, description="The app's name on GitHub (github.com/apps/<slug>).")
    installations: list[GitHubInstallationRead]


class InstallStarted(SQLModel):
    install_url: str = Field(description="Open in the browser: GitHub's install page for the app.")


class ClaimIn(SQLModel):
    claim: str = Field(max_length=4000, description="The github_claim GitHub's callback handed back.")


class RepoRead(SQLModel):
    repo: str = Field(description="owner/name")
    installation_id: int


def _app(request: Request):
    app = request.app.state.github_app
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this deployment has no GitHub App configured")
    return app


@router.get("/orgs/{org_id}/github", summary="Whether the GitHub App is available and where it's installed")
def org_status(org_id: str, principal: PrincipalDep, session: SessionDep, request: Request) -> GitHubOrgStatus:
    principal.require_org(org_id, OrgRole.viewer)
    app = request.app.state.github_app
    return GitHubOrgStatus(
        app_available=app is not None,
        app_slug=app.slug if app else None,
        installations=[GitHubInstallationRead.model_validate(r) for r in service.installations(session, [org_id])],
    )


@router.post("/orgs/{org_id}/github/install", summary="Start installing the GitHub App for this org (admin)")
def start_install(org_id: str, principal: PrincipalDep, request: Request) -> InstallStarted:
    principal.require_org(org_id, OrgRole.admin)
    return InstallStarted(
        install_url=service.start_install(_app(request), request.app.state.secrets, org_id, principal)
    )


@router.post(
    "/orgs/{org_id}/github/installations",
    summary="Link the installation GitHub's callback vouched for to this org (the admin who started it)",
)
def confirm_install(
    org_id: str, body: ClaimIn, principal: PrincipalDep, session: SessionDep, request: Request
) -> GitHubInstallationRead:
    principal.require_org(org_id, OrgRole.admin)
    _app(request)
    row = service.confirm_claim(session, request.app.state.secrets, principal, org_id, body.claim)
    return GitHubInstallationRead.model_validate(row)


@router.delete(
    "/orgs/{org_id}/github/installations/{installation_id}",
    status_code=204,
    summary="Unlink an installation from this org (admin). Uninstalling the app itself is done on GitHub.",
)
def unlink(org_id: str, installation_id: int, principal: PrincipalDep, session: SessionDep) -> Response:
    principal.require_org(org_id, OrgRole.admin)
    service.unlink(session, org_id, installation_id)
    return Response(status_code=204)


@router.get("/orgs/{org_id}/github/repos", summary="Repos this org's GitHub App installations can read")
def org_repos(org_id: str, principal: PrincipalDep, session: SessionDep, request: Request) -> list[RepoRead]:
    principal.require_org(org_id, OrgRole.viewer)
    app = request.app.state.github_app
    if app is None:
        return []
    rows = service.installations(session, [org_id])
    return [RepoRead(repo=r, installation_id=i) for r, i in service.repos(app, rows)]


@router.get("/github/app/callback", include_in_schema=False)
def callback(
    request: Request,
    session: SessionDep,
    code: str = "",
    installation_id: int = 0,
    state: str = "",
    setup_action: str = "",
) -> Response:
    """GitHub sends the installer here (the app's callback URL, with user authorization during installation)."""
    app = _app(request)
    if setup_action == "request":
        return html_page(
            "Install requested",
            "An owner of that GitHub account has to approve the install. Connect again once they have.",
        )
    if not (code and installation_id and state):
        return html_page("GitHub not connected", "GitHub didn't send back what we need; start again.", 400)
    try:
        org_id, claim = service.finish_install(
            session, app, request.app.state.secrets, code=code, installation_id=installation_id, state=state
        )
    except service.GitHubAppError as e:
        return html_page("GitHub not connected", e.message, e.status_code)
    app_url = request.app.state.app_url
    if app_url:
        target = f"{app_url.rstrip('/')}/o/{quote(org_id)}/settings?{urlencode({'github_claim': claim})}#github"
        return RedirectResponse(target, status_code=303)
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Confirm the GitHub connection</title>"
        "<body style='font-family:system-ui;max-width:40rem;margin:4rem auto'><h1>Confirm the GitHub connection</h1>"
        f'<p>POST this claim to <code>/orgs/{org_id}/github/installations</code> as <code>{{"claim": ...}}</code> '
        "while signed in as the admin who started it, within 30 minutes:</p>"
        f"<textarea readonly rows=6 style='width:100%'>{claim}</textarea></body>"
    )


def _ingest(state, integration_ids: list[str], pr: github.MergedPR) -> None:
    from app.api.integrations import ingest_pr

    for iid in integration_ids:
        with state.sessionmaker() as session:
            integration = session.get(Integration, iid)
            if integration is None:
                continue
            ids = set(session.exec(select(Milestone.id).where(Milestone.agreement_id == integration.agreement_id)))
        matched = [mid for mid in pr.milestone_ids() if mid in ids]
        if matched:
            ingest_pr(state, iid, pr, matched)


def _ingest_tag(state, integration_ids: list[str], tc: github.TaggedCommit) -> None:
    from app.api.integrations import ingest_tag

    for iid in integration_ids:
        with state.sessionmaker() as session:
            integration = session.get(Integration, iid)
            if integration is None:
                continue
            ids = list(session.exec(select(Milestone.id).where(Milestone.agreement_id == integration.agreement_id)))
        matched = tc.milestone_ids(ids)
        if matched:
            ingest_tag(state, iid, tc, matched)


@router.post("/webhooks/github/app", tags=["integrations"], summary="GitHub App webhook intake (HMAC-verified)")
async def app_webhook(
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    app = _app(request)
    raw = await request.body()
    if not github.signature_ok(app.webhook_secret, raw, x_hub_signature_256):
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

    installation_id = int((payload.get("installation") or {}).get("id") or 0)
    if x_github_event == "installation" and payload.get("action") in ("deleted", "suspend"):
        n = service.installation_removed(session, installation_id)
        outcome = f"installation {payload['action']}: unlinked from {n} organization(s)"
    elif x_github_event == "pull_request":
        pr = github.parse_merged_pr(x_github_event, payload)
        if pr is None:
            outcome = "ignored: not a merged pull request"
        else:
            integrations = service.integrations_for(session, installation_id, pr.repo)
            if not integrations:
                outcome = f"ignored: {pr.repo} isn't connected to an agreement through the app"
            elif not pr.milestone_ids():
                outcome = "ignored: no milestone label"
                for i in integrations:
                    record_audit(
                        session,
                        i.agreement_id,
                        f"github:{pr.merged_by}",
                        "github_unmatched",
                        detail={"pr": pr.url, "labels": pr.labels},
                    )
            else:
                outcome = f"queued for {len(integrations)} agreement(s)"
                background.add_task(_ingest, request.app.state, [i.id for i in integrations], pr)
    elif x_github_event in ("release", "push"):
        tc = github.parse_tagged_commit(x_github_event, payload, utcnow())
        if tc is None:
            outcome = "ignored: not a published release or a new tag"
        else:
            found = service.integrations_for(session, installation_id, tc.repo)
            integrations = [i for i in found if i.mode == "contractor_repo"]
            if not integrations:
                outcome = f"ignored: {tc.repo} isn't connected to an agreement as a contractor repo"
            elif not tc.tag.startswith(github.LABEL_PREFIX):
                outcome = "ignored: tag isn't named for a milestone"
            else:
                outcome = f"queued for {len(integrations)} agreement(s)"
                background.add_task(_ingest_tag, request.app.state, [i.id for i in integrations], tc)
    else:
        outcome = f"ignored: {x_github_event}"
    session.add(WebhookEvent(id=delivery_id, event_type=f"github.{x_github_event}", resource_id=None, outcome=outcome))
    session.commit()
    return {"outcome": outcome}
