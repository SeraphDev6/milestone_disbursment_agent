"""Request dependencies: database session, party authentication, role checks, app services."""

import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session, select

from app.ai.agents import AIRuntime
from app.db import Evidence, Milestone, Party, get_session
from app.domain.models import Role
from app.integrations.paypal import PayPalGateway

SessionDep = Annotated[Session, Depends(get_session)]
ApiKey = Annotated[str | None, Header(alias="X-API-Key", description="Party API key returned at agreement creation.")]


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _party_for_key(session: Session, key: str | None) -> Party | None:
    if not key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
    return session.exec(select(Party).where(Party.api_key_hash == hash_key(key))).first()


def current_party(agreement_id: str, session: SessionDep, x_api_key: ApiKey = None) -> Party:
    party = _party_for_key(session, x_api_key)
    if party is None or party.agreement_id != agreement_id:
        # Same answer for "no such agreement" and "not your agreement".
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agreement not found")
    return party


def milestone_party(milestone_id: str, session: SessionDep, x_api_key: ApiKey = None) -> tuple[Party, Milestone]:
    party = _party_for_key(session, x_api_key)
    milestone = session.get(Milestone, milestone_id)
    if party is None or milestone is None or milestone.agreement_id != party.agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "milestone not found")
    return party, milestone


def evidence_party(evidence_id: str, session: SessionDep, x_api_key: ApiKey = None) -> tuple[Party, Evidence]:
    party = _party_for_key(session, x_api_key)
    ev = session.get(Evidence, evidence_id)
    m = session.get(Milestone, ev.milestone_id) if ev else None
    if party is None or ev is None or m is None or m.agreement_id != party.agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "evidence not found")
    return party, ev


PartyDep = Annotated[Party, Depends(current_party)]
MilestonePartyDep = Annotated[tuple[Party, Milestone], Depends(milestone_party)]
EvidencePartyDep = Annotated[tuple[Party, Evidence], Depends(evidence_party)]


def require_org_key(
    request: Request,
    x_org_key: Annotated[str | None, Header(description="Organization key, required when the server sets one.")] = None,
) -> None:
    expected = request.app.state.settings.org_api_key
    if expected and not (x_org_key and secrets.compare_digest(x_org_key, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-Org-Key header required")


OrgKeyDep = Annotated[None, Depends(require_org_key)]


def require_role(party: Party, *roles: Role) -> None:
    if party.role not in {r.value for r in roles}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"only {' or '.join(r.value for r in roles)} can do this")


def gateway(request: Request) -> PayPalGateway:
    return request.app.state.paypal


def ai(request: Request) -> AIRuntime:
    return request.app.state.ai
