"""Request dependencies: database session, the caller, their seat on an agreement, app services."""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session

from app.ai.agents import AIRuntime
from app.db import get_session
from app.integrations.email import Mailer
from app.integrations.paypal import PayPalGateway
from app.models import Agreement, Evidence, GitHubHandover, Milestone
from app.services.access import Access, Principal, access_for, principal_from_token

SessionDep = Annotated[Session, Depends(get_session)]
OrgHeader = Annotated[
    str | None, Header(alias="X-Org-Id", description="Which of your orgs you act for, if you belong to several.")
]


def bearer_token(authorization: Annotated[str | None, Header()] = None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def current_principal(session: SessionDep, token: Annotated[str | None, Depends(bearer_token)]) -> Principal:
    return principal_from_token(session, token)


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def agreement_access(
    agreement_id: str, session: SessionDep, principal: PrincipalDep, x_org_id: OrgHeader = None
) -> Access:
    agreement = session.get(Agreement, agreement_id)
    if agreement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agreement not found")
    return access_for(principal, agreement, x_org_id)


def milestone_access(
    milestone_id: str, session: SessionDep, principal: PrincipalDep, x_org_id: OrgHeader = None
) -> tuple[Access, Milestone]:
    milestone = session.get(Milestone, milestone_id)
    if milestone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "milestone not found")
    return access_for(principal, milestone.agreement, x_org_id), milestone


def evidence_access(
    evidence_id: str, session: SessionDep, principal: PrincipalDep, x_org_id: OrgHeader = None
) -> tuple[Access, Evidence]:
    ev = session.get(Evidence, evidence_id)
    m = session.get(Milestone, ev.milestone_id) if ev else None
    if ev is None or m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found")
    return access_for(principal, m.agreement, x_org_id), ev


def handover_access(
    handover_id: str, session: SessionDep, principal: PrincipalDep, x_org_id: OrgHeader = None
) -> tuple[Access, GitHubHandover, Milestone]:
    row = session.get(GitHubHandover, handover_id)
    m = session.get(Milestone, row.milestone_id) if row else None
    if row is None or m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "handover not found")
    return access_for(principal, m.agreement, x_org_id), row, m


AgreementAccessDep = Annotated[Access, Depends(agreement_access)]
MilestoneAccessDep = Annotated[tuple[Access, Milestone], Depends(milestone_access)]
EvidenceAccessDep = Annotated[tuple[Access, Evidence], Depends(evidence_access)]
HandoverAccessDep = Annotated[tuple[Access, GitHubHandover, Milestone], Depends(handover_access)]


def require_org_key(
    request: Request,
    x_org_key: Annotated[str | None, Header(description="Deployment key, required when the server sets one.")] = None,
) -> None:
    """Optional deployment-wide gate on spend-heavy endpoints (extraction), independent of sign-in."""
    expected = request.app.state.settings.org_api_key
    if expected and not (x_org_key and secrets.compare_digest(x_org_key, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-Org-Key header required")


OrgKeyDep = Annotated[None, Depends(require_org_key)]


def gateway(request: Request) -> PayPalGateway:
    return request.app.state.paypal


def ai(request: Request) -> AIRuntime:
    return request.app.state.ai


def mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def client_ip(request: Request) -> str:
    """The caller's address for rate limits: the first X-Forwarded-For hop when the deployment trusts its proxy."""
    if request.app.state.settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, scope: str, key: str) -> None:
    """Count one request against `scope` (a `rate_limit_*_per_hour` setting name) for `key`; 429 when exceeded."""
    limit = getattr(request.app.state.settings, f"rate_limit_{scope}_per_hour")
    request.app.state.ratelimiter.check(scope, key, limit)
