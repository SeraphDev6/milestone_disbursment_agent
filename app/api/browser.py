"""Browser pages: finishing sign-in, and the OAuth consent screen MCP clients send people to.

The REST API uses bearer tokens only. These pages use an HttpOnly cookie session instead, because OAuth's
authorize step runs in a browser. Forms carry a CSRF token tied to the session and the request.
"""

import hashlib
import hmac
import secrets
from html import escape
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from mcp.server.auth.provider import AuthorizationParams, construct_redirect_uri
from sqlmodel import select

from app.api.deps import SessionDep, client_ip, mailer, rate_limit, show_links
from app.db import as_utc, utcnow
from app.models import Membership, OrgRole
from app.models.oauth import OAuthClient, OAuthPendingAuthorization
from app.services import identity, sso
from app.services.access import AccessError, Principal, hash_token, principal_from_token
from app.services.ratelimit import RateLimited

router = APIRouter(include_in_schema=False)
COOKIE = "mde_session"
LOGIN_COOKIE = "mde_login"  # ties a sign-in link's page to the browser that will press its button
SSO_COOKIE = "mde_sso"  # ties an SSO attempt to the browser that started it
BIND_TTL = 10 * 60


# --- helpers ---------------------------------------------------------------------------


def safe_next(path: str | None) -> str:
    """Only local paths: no scheme, no host, no protocol-relative or backslash tricks."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or urlsplit(path).netloc:
        return "/"
    return path


def _secure(request: Request) -> bool:
    return request.app.state.settings.public_base_url.startswith("https://")


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE, token, httponly=True, secure=_secure(request), samesite="lax", max_age=30 * 24 * 3600)


def bind_browser(response: Response, request: Request, name: str, value: str) -> None:
    """A short-lived, SameSite=Lax cookie that a cross-site request can't carry, so a sign-in can only finish in
    the browser that started it (login CSRF: an attacker signing a victim's browser in as the attacker)."""
    response.set_cookie(name, value, httponly=True, secure=_secure(request), samesite="lax", max_age=BIND_TTL)


def sso_binding(request: Request, state: str) -> str:
    key = request.app.state.settings.secret_key.get_secret_value().encode()
    return hmac.new(key, f"sso:{state}".encode(), hashlib.sha256).hexdigest()


def bound_to_browser(request: Request, name: str, expected: str) -> bool:
    actual = request.cookies.get(name)
    return bool(actual) and hmac.compare_digest(actual, expected)


def browser_principal(request: Request, session) -> tuple[Principal, str] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        principal = principal_from_token(session, token)
    except AccessError:
        return None
    return (principal, token) if principal.user else None


def csrf_token(request: Request, session_token: str, purpose: str) -> str:
    key = request.app.state.settings.secret_key.get_secret_value().encode()
    return hmac.new(key, f"{hash_token(session_token)}:{purpose}".encode(), hashlib.sha256).hexdigest()


def page(title: str, body_html: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><body style='font-family:system-ui;max-width:34rem;margin:4rem auto;"
        f"padding:0 1rem;line-height:1.5'><h1 style='font-size:1.4rem'>{escape(title)}</h1>{body_html}</body>",
        status_code=code,
    )


# --- finishing sign-in -----------------------------------------------------------------


@router.get("/auth/magic-link/verify")
def magic_link_page(request: Request, token: str, next: str | None = None) -> HTMLResponse:
    # A GET never signs in: email clients prefetch links. The button POSTs, with a nonce that must match the
    # cookie set here, so the POST can only come from this browser.
    nonce = secrets.token_urlsafe(24)
    response = page(
        "Finish signing in",
        "<form method=post action='/auth/magic-link/complete'>"
        f"<input type=hidden name=token value='{escape(token)}'><input type=hidden name=next value='{escape(safe_next(next))}'>"
        f"<input type=hidden name=nonce value='{nonce}'>"
        "<button style='font-size:1rem;padding:.5rem 1rem'>Sign in</button></form>",
    )
    bind_browser(response, request, LOGIN_COOKIE, nonce)
    return response


@router.post("/auth/magic-link/complete")
def magic_link_complete(
    request: Request,
    session: SessionDep,
    token: Annotated[str, Form()],
    next: Annotated[str, Form()] = "/",
    nonce: Annotated[str, Form()] = "",
) -> Response:
    if not nonce or not bound_to_browser(request, LOGIN_COOKIE, nonce):
        return page(
            "Open the link again",
            "<p>This page wasn't opened from your sign-in link. Open the link from your email and press Sign in.</p>",
            403,
        )
    try:
        _, session_token = identity.verify_magic_link(session, token)
    except identity.IdentityError as e:
        return page("Sign-in link didn't work", f"<p>{escape(e.message)}</p>", e.status_code)
    response = RedirectResponse(safe_next(next), status_code=303)
    set_session_cookie(response, request, session_token)
    response.delete_cookie(LOGIN_COOKIE)
    return response


@router.post("/oauth/sign-in")
def oauth_sign_in(
    request: Request, session: SessionDep, email: Annotated[str, Form()], next: Annotated[str, Form()] = "/"
) -> Response:
    target = safe_next(next)
    try:
        rate_limit(request, "magic_link", client_ip(request))
        rate_limit(request, "magic_link", "email:" + email.strip().lower())
    except RateLimited as e:
        return page("Too many sign-in attempts", f"<p>Try again in {e.retry_after // 60 + 1} minutes.</p>", 429)
    if sso.connection_for_email(session, email) is not None:
        url = sso.start(session, email, request.app.state.oidc, request.app.state.settings.public_base_url, target)
        response = RedirectResponse(url, status_code=303)
        bind_browser(response, request, SSO_COOKIE, sso_binding(request, sso.state_of(url)))
        return response
    try:
        link = identity.request_magic_link(
            session, mailer(request), email, request.app.state.settings.public_base_url, next_path=target
        )
    except identity.IdentityError as e:
        return page("Can't sign in this way", f"<p>{escape(e.message)}</p>", e.status_code)
    dev = f"<p><a href='{escape(link)}'>Dev link (mock mode)</a></p>" if show_links(request) else ""
    return page("Check your email", f"<p>We sent a sign-in link to {escape(email)}.</p>{dev}")


# --- consent ---------------------------------------------------------------------------


def _pending(session, request_id: str) -> OAuthPendingAuthorization | None:
    row = session.get(OAuthPendingAuthorization, request_id)
    if row is None or row.used_at is not None or as_utc(row.expires_at) < utcnow():
        return None
    return row


@router.get("/oauth/consent")
def consent(request: Request, session: SessionDep) -> Response:
    request_id = request.query_params.get("request", "")
    pending = _pending(session, request_id)
    if pending is None:
        return page("This request has expired", "<p>Go back to your app and connect again.</p>", 400)
    here = f"/oauth/consent?request={quote(request_id)}"
    signed_in = browser_principal(request, session)
    if signed_in is None:
        return page(
            "Sign in to connect",
            "<p>An app wants to act for you. Sign in to review what it can do.</p>"
            f"<form method=post action='/oauth/sign-in'><input type=hidden name=next value='{escape(here)}'>"
            "<input name=email type=email required placeholder='you@example.org' style='font-size:1rem;padding:.4rem'> "
            "<button style='font-size:1rem;padding:.4rem .8rem'>Continue</button></form>",
        )
    principal, session_token = signed_in
    client = session.get(OAuthClient, pending.client_id)
    info = client.info if client else {}
    params = AuthorizationParams.model_validate(pending.params)
    client_name = pending.params.get("_client_name") or info.get("client_name") or "An MCP client"
    cimd_host = pending.params.get("_cimd_host")
    redirect_host = urlsplit(str(params.redirect_uri)).hostname or str(params.redirect_uri)
    loopback = redirect_host in ("localhost", "127.0.0.1", "::1")
    memberships = session.exec(select(Membership).where(Membership.user_id == principal.user.id)).all()  # type: ignore[union-attr]
    if not memberships:
        return page("No organization yet", "<p>Create or join an organization first, then connect again.</p>", 403)
    options = "".join(
        f"<label style='display:block;margin:.3rem 0'><input type=radio name=org_id value='{escape(m.org_id)}' "
        f"{'checked' if i == 0 else ''} required> {escape(m.organization.name)} <small>({escape(m.role)})</small></label>"
        for i, m in enumerate(memberships)
    )
    csrf = csrf_token(request, session_token, request_id)
    return page(
        "Allow access?",
        f"<p><b>{escape(client_name)}</b>"
        + (f" (identified by <code>{escape(cimd_host)}</code>)" if cimd_host else "")
        + f" wants to act as <b>{escape(principal.user.email)}</b>. "  # type: ignore[union-attr]
        f"After you decide, you'll be sent to <code>{escape(redirect_host)}</code>.</p>"
        + (
            "<p style='background:#fff4e5;padding:.5rem;border-radius:4px'><b>This app runs on your computer.</b> "
            "Only allow it if you just started connecting it yourself: any program on this machine could claim "
            "to be it.</p>"
            if loopback
            else ""
        )
        + "<p>It can read agreements, criteria, evidence, verdicts and audit logs, draft agreements and submit "
        "evidence, with your role in the organization you pick. It can't confirm criteria, approve releases, "
        "fund, or dispute: those stay with people.</p>"
        f"<form method=post action='/oauth/consent'><input type=hidden name=request value='{escape(request_id)}'>"
        f"<input type=hidden name=csrf value='{csrf}'><fieldset><legend>Act for</legend>{options}</fieldset><p>"
        "<button name=decision value=approve style='font-size:1rem;padding:.4rem .8rem'>Allow</button> "
        "<button name=decision value=deny style='font-size:1rem;padding:.4rem .8rem'>Deny</button></p></form>",
    )


@router.post("/oauth/consent")
def consent_decision(
    request: Request,
    session: SessionDep,
    request_id: Annotated[str, Form(alias="request")],
    csrf: Annotated[str, Form()],
    decision: Annotated[str, Form()],
    org_id: Annotated[str | None, Form()] = None,
) -> Response:
    signed_in = browser_principal(request, session)
    pending = _pending(session, request_id)
    if signed_in is None or pending is None:
        return page("This request has expired", "<p>Go back to your app and connect again.</p>", 400)
    principal, session_token = signed_in
    if not hmac.compare_digest(csrf, csrf_token(request, session_token, request_id)):
        return page("Something went wrong", "<p>The form expired. Go back and try again.</p>", 403)
    params = AuthorizationParams.model_validate(pending.params)
    if decision != "approve":
        pending.used_at = utcnow()
        session.commit()
        url = construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)
        return RedirectResponse(url, status_code=303)
    if not org_id or principal.role_in(org_id) is None or not principal.role_in(org_id).at_least(OrgRole.viewer):  # type: ignore[union-attr]
        return page("Pick an organization", "<p>Choose an organization you belong to.</p>", 400)
    code = request.app.state.oauth.issue_code(session, pending, principal.user.id, org_id)  # type: ignore[union-attr]
    return RedirectResponse(construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state), 303)
