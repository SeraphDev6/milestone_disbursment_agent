"""Sign-in (magic link), organizations, invitations and API keys."""

import secrets
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from sqlmodel import Session, select

from app.db import as_utc, record_audit, utcnow
from app.integrations.email import ConsoleMailer
from app.models import (
    Agreement,
    AgreementStatus,
    ApiKeyCreate,
    ApiToken,
    Invitation,
    InvitationKind,
    LoginToken,
    Membership,
    Organization,
    OrganizationCreate,
    OrgRole,
    Party,
    Role,
    TokenKind,
    User,
)
from app.services import ServiceError
from app.services.access import Principal, hash_token

LOGIN_TTL = timedelta(minutes=15)
SESSION_TTL = timedelta(days=30)
INVITATION_TTL = timedelta(days=14)


class IdentityError(ServiceError):
    pass


def _token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def normalize_email(email: str) -> str:
    return email.strip().lower()


# --- magic link ------------------------------------------------------------------------


def request_magic_link(
    session: Session, mailer: ConsoleMailer, email: str, base_url: str, next_path: str | None = None
) -> str:
    """Create a one-time sign-in link and email it. Returns the link (shown in mock mode only)."""
    from app.services import sso  # sso imports this module

    email = normalize_email(email)
    if sso.enforced_for(session, email):
        raise IdentityError("your organization requires single sign-on; use POST /auth/sso/start", 409)
    raw = _token("mdl")
    session.add(LoginToken(email=email, token_hash=hash_token(raw), expires_at=utcnow() + LOGIN_TTL))
    session.commit()
    link = f"{base_url}/auth/magic-link/verify?token={raw}"
    if next_path:
        link += f"&next={quote(next_path, safe='')}"
    mailer.send(email, "Your sign-in link", "Open this link to sign in. It expires in 15 minutes.", link)
    return link


def verify_magic_link(session: Session, raw: str) -> tuple[User, str]:
    """Exchange a magic-link token for a session token. Creates the user on first sign-in."""
    row = session.exec(select(LoginToken).where(LoginToken.token_hash == hash_token(raw))).first()
    now = utcnow()
    if row is None or row.used_at is not None or as_utc(row.expires_at) < now:
        raise IdentityError("this sign-in link is invalid, expired or already used", 401)
    row.used_at = now
    return sign_in(session, row.email)


def sign_in(session: Session, email: str) -> tuple[User, str]:
    """Start a session for a verified email (magic link or SSO)."""
    email = normalize_email(email)
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(email=email)
        session.add(user)
        session.flush()
    user.last_sign_in_at = utcnow()
    raw = _token("mds")
    session.add(
        ApiToken(kind=TokenKind.session, token_hash=hash_token(raw), user_id=user.id, expires_at=utcnow() + SESSION_TTL)
    )
    session.commit()
    return user, raw


def sign_out(session: Session, raw: str) -> None:
    row = session.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(raw))).first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        session.commit()


# --- organizations ---------------------------------------------------------------------


def create_org(session: Session, user: User, data: OrganizationCreate) -> Organization:
    org = Organization.model_validate(data)
    session.add(org)
    session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role=OrgRole.owner))
    session.commit()
    return org


def create_api_key(session: Session, org_id: str, data: ApiKeyCreate, actor: str) -> tuple[ApiToken, str]:
    if data.org_role == OrgRole.owner:
        raise IdentityError("API keys can be at most admin", 422)
    raw = _token("mdk")
    row = ApiToken(
        kind=TokenKind.api_key,
        token_hash=hash_token(raw),
        org_id=org_id,
        org_role=data.org_role,
        name=data.name,
        created_by=actor,
    )
    session.add(row)
    session.commit()
    return row, raw


# --- invitations -----------------------------------------------------------------------


def invite(
    session: Session,
    mailer: ConsoleMailer,
    *,
    kind: InvitationKind,
    email: str,
    actor: str,
    base_url: str,
    agreement: Agreement | None = None,
    org: Organization | None = None,
    org_role: OrgRole | None = None,
    commit: bool = True,
) -> tuple[Invitation, str]:
    """Create and email an invitation. Returns it with its accept link (shown in mock mode only)."""
    email = normalize_email(email)
    raw = _token("mdi")
    inv = Invitation(
        kind=kind,
        email=email,
        agreement_id=agreement.id if agreement else None,
        org_id=org.id if org else None,
        org_role=org_role,
        token_hash=hash_token(raw),
        invited_by=actor,
        expires_at=utcnow() + INVITATION_TTL,
    )
    session.add(inv)
    link = f"{base_url}/invitations/accept?token={raw}"
    if agreement is not None:
        what = "receive payments under" if kind == InvitationKind.counterparty else "review"
        subject = f"You're invited to {what} '{agreement.title}'"
        record_audit(session, agreement.id, actor, "invited", detail={"email": email, "kind": kind})
    else:
        subject = f"You're invited to join {org.name if org else 'an organization'}"
    mailer.send(email, subject, "Sign in with this email, then accept the invitation.", link)
    if commit:
        session.commit()
    return inv, link


def pending_agreement_invitations(session: Session, agreement_id: str) -> list[Invitation]:
    now = utcnow()
    return [
        i
        for i in session.exec(select(Invitation).where(Invitation.agreement_id == agreement_id)).all()
        if i.accepted_at is None and i.revoked_at is None and as_utc(i.expires_at) > now
    ]


@dataclass
class Accepted:
    invitation: Invitation
    org: Organization


def accept_invitation(
    session: Session,
    principal: Principal,
    raw: str,
    org_id: str | None,
    new_org: OrganizationCreate | None,
) -> Accepted:
    if principal.user is None:
        raise IdentityError("sign in as a person to accept invitations", 403)
    inv = session.exec(select(Invitation).where(Invitation.token_hash == hash_token(raw))).first()
    if inv is None or inv.revoked_at is not None or inv.accepted_at is not None or as_utc(inv.expires_at) < utcnow():
        raise IdentityError("this invitation is invalid, expired, revoked or already used", 404)
    if normalize_email(principal.user.email) != inv.email:
        raise IdentityError(f"this invitation is for {inv.email}; sign in with that email to accept it", 403)

    if inv.kind == InvitationKind.member:
        org = session.get(Organization, inv.org_id)
        if principal.role_in(org.id) is None:  # type: ignore[union-attr]
            session.add(Membership(org_id=org.id, user_id=principal.user.id, role=inv.org_role))  # type: ignore[union-attr]
        inv.accepted_at, inv.accepted_org_id = utcnow(), org.id  # type: ignore[union-attr]
        session.commit()
        return Accepted(inv, org)  # type: ignore[arg-type]

    # Agreement invitations bind an organization, so the acceptor must be able to commit it.
    if new_org is not None:
        org = create_org(session, principal.user, new_org)
        principal.roles[org.id] = OrgRole.owner
    elif org_id is not None:
        principal.require_org(org_id, OrgRole.admin)
        org = session.get(Organization, org_id)
    else:
        raise IdentityError("choose the organization you're accepting for (org_id) or create one (new_org)", 422)

    agreement = session.get(Agreement, inv.agreement_id)
    if agreement is None or agreement.status in (AgreementStatus.cancelled, AgreementStatus.completed):
        raise IdentityError("this agreement is no longer open", 409)
    if any(p.org_id == org.id for p in agreement.parties):  # type: ignore[union-attr]
        raise IdentityError("this organization is already a party to the agreement", 409)

    if inv.kind == InvitationKind.counterparty:
        if any(p.role == Role.payee for p in agreement.parties):
            raise IdentityError("this agreement already has a payee", 409)
        if not org.paypal_email:  # type: ignore[union-attr]
            raise IdentityError("set your organization's PayPal email before accepting payments", 422)
        role = Role.payee
    else:
        role = Role.reviewer

    agreement.parties.append(
        Party(
            role=role,
            org_id=org.id,
            display_name=org.name,  # type: ignore[union-attr]
            paypal_email=org.paypal_email if role == Role.payee else None,
        )  # type: ignore[union-attr]
    )
    inv.accepted_at, inv.accepted_org_id = utcnow(), org.id  # type: ignore[union-attr]
    record_audit(
        session,
        agreement.id,
        f"{principal.actor}@org:{org.id}",
        "party_joined",  # type: ignore[union-attr]
        detail={"role": role, "org": org.name},
    )  # type: ignore[union-attr]
    session.commit()
    return Accepted(inv, org)  # type: ignore[arg-type]
