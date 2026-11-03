"""Sign-in, the signed-in user, and accepting invitations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlmodel import Field, SQLModel, select

from app.api.deps import PrincipalDep, SessionDep, bearer_token, client_ip, mailer, rate_limit, show_links
from app.db import as_utc, utcnow
from app.models import (
    Agreement,
    Invitation,
    InvitationRead,
    Membership,
    MembershipRead,
    OrganizationCreate,
    OrganizationRead,
    UserRead,
)
from app.models.identity import EMAIL_PATTERN
from app.services import identity
from app.services.access import AccessError

router = APIRouter(tags=["auth"])


class MagicLinkRequest(SQLModel):
    email: str = Field(max_length=320, schema_extra={"pattern": EMAIL_PATTERN})


class MagicLinkSent(SQLModel):
    sent: bool = True
    dev_link: str | None = Field(default=None, description="Mock mode only: the link that would have been emailed.")


class MagicLinkVerify(SQLModel):
    token: str


class SessionCreated(SQLModel):
    token: str = Field(description="Send as 'Authorization: Bearer <token>'. Valid 30 days.")
    user: UserRead


class Me(SQLModel):
    user: UserRead
    memberships: list[MembershipRead]
    invitations: list[InvitationRead] = Field(description="Pending invitations to your email.")


class AcceptInvitation(SQLModel):
    token: str = Field(description="From the invitation link.")
    org_id: str | None = Field(default=None, description="Accept for this org (you must be its admin or owner).")
    new_org: OrganizationCreate | None = Field(default=None, description="Or create an org and accept for it.")


class InvitationAccepted(SQLModel):
    invitation: InvitationRead
    organization: OrganizationRead


def invitation_read(session, inv: Invitation) -> InvitationRead:
    title = session.get(Agreement, inv.agreement_id).title if inv.agreement_id else None
    return InvitationRead.model_validate(inv, update={"agreement_title": title})


@router.post("/auth/magic-link", summary="Email a one-time sign-in link")
def request_link(body: MagicLinkRequest, session: SessionDep, request: Request) -> MagicLinkSent:
    settings = request.app.state.settings
    rate_limit(request, "magic_link", client_ip(request))
    rate_limit(request, "magic_link", "email:" + body.email.strip().lower())
    link = identity.request_magic_link(
        session, mailer(request), body.email, settings.public_base_url, app_url=settings.public_app_url
    )
    return MagicLinkSent(dev_link=link if show_links(request) else None)


@router.post("/auth/magic-link/verify", summary="Exchange a sign-in link token for a session token")
def verify_link(body: MagicLinkVerify, session: SessionDep) -> SessionCreated:
    user, token = identity.verify_magic_link(session, body.token)
    return SessionCreated(token=token, user=UserRead.model_validate(user))


@router.post("/auth/sign-out", status_code=204, summary="Revoke the current session")
def sign_out(session: SessionDep, token: Annotated[str | None, Depends(bearer_token)]) -> None:
    if token:
        identity.sign_out(session, token)


@router.get("/me", summary="Who you are, your organizations, and invitations waiting for you")
def me(principal: PrincipalDep, session: SessionDep) -> Me:
    if principal.user is None:
        raise AccessError("API keys act as an organization, not a person; use GET /orgs/{id}", 403)
    user = principal.user
    memberships = session.exec(select(Membership).where(Membership.user_id == user.id)).all()
    now = utcnow()
    invitations = [
        i
        for i in session.exec(select(Invitation).where(Invitation.email == user.email)).all()
        if i.accepted_at is None and i.revoked_at is None and as_utc(i.expires_at) > now
    ]
    return Me(
        user=UserRead.model_validate(user),
        memberships=[
            MembershipRead(
                role=m.role, org_id=m.org_id, org_name=m.organization.name, user=UserRead.model_validate(user)
            )
            for m in memberships
        ],
        invitations=[invitation_read(session, i) for i in invitations],
    )


@router.post("/invitations/accept", summary="Accept an invitation for one of your orgs, or for a new org")
def accept(body: AcceptInvitation, principal: PrincipalDep, session: SessionDep) -> InvitationAccepted:
    result = identity.accept_invitation(session, principal, body.token, body.org_id, body.new_org)
    return _accepted(session, principal, result)


class AcceptInvitationById(SQLModel):
    org_id: str | None = Field(default=None, description="Accept for this org (you must be its admin or owner).")
    new_org: OrganizationCreate | None = Field(default=None, description="Or create an org and accept for it.")


@router.post(
    "/invitations/{invitation_id}/accept",
    summary="Accept one of your pending invitations (from GET /me) without the emailed link",
)
def accept_by_id(
    invitation_id: str, body: AcceptInvitationById, principal: PrincipalDep, session: SessionDep
) -> InvitationAccepted:
    result = identity.accept_invitation_by_id(session, principal, invitation_id, body.org_id, body.new_org)
    return _accepted(session, principal, result)


def _accepted(session, principal, result) -> InvitationAccepted:
    return InvitationAccepted(
        invitation=invitation_read(session, result.invitation),
        organization=OrganizationRead.model_validate(
            result.org, update={"my_role": principal.roles.get(result.org.id)}
        ),
    )
