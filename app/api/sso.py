"""Org SSO configuration and the OIDC sign-in round trip."""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Field, SQLModel, select

from app.api.auth import SessionCreated
from app.api.browser import SSO_COOKIE, bind_browser, bound_to_browser, safe_next, set_session_cookie, sso_binding
from app.api.deps import PrincipalDep, SessionDep
from app.models import OrgRole, UserRead
from app.models.identity import EMAIL_PATTERN
from app.models.sso import SSOConnection, SSOConnectionRead, SSOConnectionWrite
from app.services import ServiceError, sso

router = APIRouter(tags=["sso"])


class SSOStart(SQLModel):
    email: str = Field(max_length=320, schema_extra={"pattern": EMAIL_PATTERN})
    to_app: bool = Field(
        default=False,
        description="Return to the web app (PUBLIC_APP_URL/auth/sso#token=...) instead of answering with JSON.",
    )


APP_RETURN = "app:"  # stored as the login state's next_path: finish in the web app


def _start_next(request: Request, to_app: bool) -> str | None:
    if not to_app:
        return None
    if not request.app.state.app_url:
        raise ServiceError("this server has no web app configured (PUBLIC_APP_URL)", 422)
    return APP_RETURN


class SSORedirect(SQLModel):
    authorize_url: str = Field(description="Send the person here to sign in with their organization.")


def _base(request: Request) -> str:
    return request.app.state.settings.api_base_url


def _connection(session, org_id: str) -> SSOConnection:
    conn = session.exec(select(SSOConnection).where(SSOConnection.org_id == org_id)).first()
    if conn is None:
        raise sso.SSOError("SSO isn't set up for this organization", 404)
    return conn


@router.put("/orgs/{org_id}/sso", summary="Set up or change the org's OIDC single sign-on (admin)")
def configure(
    org_id: str, body: SSOConnectionWrite, principal: PrincipalDep, session: SessionDep, request: Request
) -> SSOConnectionRead:
    principal.require_org(org_id, OrgRole.admin)
    state = request.app.state
    conn = sso.configure(session, org_id, body, state.oidc, state.secrets)
    return sso.read(conn, _base(request))


@router.get("/orgs/{org_id}/sso", summary="The org's SSO setup and domain verification status (admin)")
def get_sso(org_id: str, principal: PrincipalDep, session: SessionDep, request: Request) -> SSOConnectionRead:
    principal.require_org(org_id, OrgRole.admin)
    return sso.read(_connection(session, org_id), _base(request))


@router.delete("/orgs/{org_id}/sso", status_code=204, summary="Remove the org's SSO (owner)")
def delete_sso(org_id: str, principal: PrincipalDep, session: SessionDep) -> Response:
    principal.require_org(org_id, OrgRole.owner)
    session.delete(_connection(session, org_id))
    session.commit()
    return Response(status_code=204)


@router.post(
    "/orgs/{org_id}/sso/domains/{domain}/verify",
    summary="Check the DNS TXT record proving the org owns this email domain (admin)",
)
def verify_domain(
    org_id: str, domain: str, principal: PrincipalDep, session: SessionDep, request: Request
) -> SSOConnectionRead:
    principal.require_org(org_id, OrgRole.admin)
    sso.verify_domain(session, org_id, domain, request.app.state.txt_lookup)
    return sso.read(_connection(session, org_id), _base(request))


@router.post("/auth/sso/start", summary="Start signing in with your organization's identity provider")
def start(body: SSOStart, session: SessionDep, request: Request, response: Response) -> SSORedirect:
    next_path = _start_next(request, body.to_app)
    url = sso.start(session, body.email, request.app.state.oidc, _base(request), next_path)
    if next_path:  # the callback will sign a browser in: only the one that started here
        bind_browser(response, request, SSO_COOKIE, sso_binding(request, sso.state_of(url)))
    return SSORedirect(authorize_url=url)


@router.get("/auth/sso/start", include_in_schema=False)
def start_redirect(email: str, session: SessionDep, request: Request, to_app: bool = False) -> RedirectResponse:
    url = sso.start(session, email, request.app.state.oidc, _base(request), _start_next(request, to_app))
    response = RedirectResponse(url, status_code=303)
    bind_browser(response, request, SSO_COOKIE, sso_binding(request, sso.state_of(url)))
    return response


@router.get("/auth/sso/callback", summary="Where the identity provider returns; issues a session token")
def callback(code: str, state: str, session: SessionDep, request: Request) -> Response:
    app_state = request.app.state
    # Browser flows (the web app, MCP consent) finish only in the browser that started them. The plain API
    # flow returns the token in the body, which signs nobody's browser in, so it needs no binding.
    bound = bound_to_browser(request, SSO_COOKIE, sso_binding(request, state))
    user, token, next_path = sso.finish(
        session, code, state, app_state.oidc, app_state.secrets, _base(request), browser_bound=bound
    )
    if next_path == APP_RETURN:  # the web app is bearer-only: hand it the token in the fragment (never sent on)
        app_url = app_state.app_url.rstrip("/")
        return RedirectResponse(f"{app_url}/auth/sso#token={token}", status_code=303)
    if next_path:  # a browser flow (e.g. MCP consent): continue there, signed in
        response = RedirectResponse(safe_next(next_path), status_code=303)
        set_session_cookie(response, request, token)
        return response
    return JSONResponse(SessionCreated(token=token, user=UserRead.model_validate(user)).model_dump(mode="json"))
