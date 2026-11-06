"""Who is calling, which org they act for on an agreement, and what that lets them do.

Rights come from two things: the org's role on the agreement (payer, payee, reviewer) and the caller's role
in that org (owner > admin > member > viewer). The matrix below is the spec's "Who can do what" table.
"""

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum

from sqlmodel import Session, select

from app.db import utcnow
from app.models import Agreement, ApiToken, Membership, OrgRole, Party, Role, TokenKind, User
from app.services import ServiceError


class Action(StrEnum):
    view = "view"
    edit_tranches = "edit_tranches"
    confirm_criteria = "confirm_criteria"
    set_auto_release = "set_auto_release"
    invite = "invite"
    fund = "fund"
    void = "void"
    review = "review"
    release = "release"
    submit_evidence = "submit_evidence"
    connect_github = "connect_github"
    connect_email = "connect_email"
    dispute = "dispute"
    compliance_packet = "compliance_packet"
    dispute_pack = "dispute_pack"
    manage_subscriptions = "manage_subscriptions"
    assign = "assign"
    contest = "contest"
    resolve_for_payee = "resolve_for_payee"
    resolve_for_payer = "resolve_for_payer"


V, M, A = OrgRole.viewer, OrgRole.member, OrgRole.admin
P, E, R = Role.payer, Role.payee, Role.reviewer

# action -> {agreement role: minimum org role}. Missing agreement role = never allowed.
RULES: dict[Action, dict[Role, OrgRole]] = {
    Action.view: {P: V, E: V, R: V},
    Action.edit_tranches: {P: M},
    Action.confirm_criteria: {P: A, E: A, R: A},
    Action.set_auto_release: {P: A},
    Action.invite: {P: A},
    Action.fund: {P: M},
    Action.void: {P: M},
    Action.review: {P: A, R: M},
    Action.release: {P: A},
    Action.submit_evidence: {E: M},
    Action.connect_github: {E: A},
    Action.connect_email: {E: A},
    Action.dispute: {E: A},
    Action.compliance_packet: {P: M, R: M},
    Action.dispute_pack: {E: M, R: M},  # the payee's answer to a chargeback; the payer has the packet
    Action.manage_subscriptions: {P: A, E: A, R: A},
    Action.assign: {P: M, E: M},
    # A contest pauses a held tranche's payout. Each side can only concede: the payer withdraws it (pay the
    # payee), the payee accepts it (refund the payer); a reviewer can decide either way.
    Action.contest: {P: A, R: M},
    Action.resolve_for_payee: {P: A, R: M},
    Action.resolve_for_payer: {E: A, R: M},
}


# What an MCP client acting for a person may do on an agreement: exactly the tools it was shown at consent.
# Everything else (confirming criteria, funding, reviewing, releasing, contesting, disputing, inviting, editing
# tranches, managing subscriptions) stays with the person, whatever their role, even if the client calls the
# REST endpoint directly with its access token.
MCP_ACTIONS = frozenset({Action.view, Action.submit_evidence, Action.compliance_packet})


class AccessError(ServiceError):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Principal:
    """The authenticated caller: a signed-in user (with org memberships) or an org API key."""

    user: User | None = None
    api_key: ApiToken | None = None
    roles: dict[str, OrgRole] = field(default_factory=dict)  # org_id -> role
    oauth_client_id: str | None = None  # set when an MCP client acts for the user

    @property
    def actor(self) -> str:
        if self.user is None:
            return f"apikey:{self.api_key.id}"  # type: ignore[union-attr]
        via = f"+mcp:{self.oauth_client_id[:24]}" if self.oauth_client_id else ""
        return f"user:{self.user.id}{via}"

    @property
    def via_mcp(self) -> bool:
        return self.oauth_client_id is not None

    def require_person(self, what: str) -> User:
        """Steps that bind a person or an organization (creating orgs, accepting invitations) are not for API
        keys or MCP clients."""
        if self.user is None:
            raise AccessError(f"only a signed-in person can {what}", 403)
        if self.via_mcp:
            raise AccessError(f"an MCP client can't {what}; do it in the web app", 403)
        return self.user

    def role_in(self, org_id: str) -> OrgRole | None:
        return self.roles.get(org_id)

    def require_org(self, org_id: str, minimum: OrgRole) -> OrgRole:
        role = self.role_in(org_id)
        if role is None:
            raise AccessError("organization not found", 404)  # same answer as "doesn't exist"
        if not role.at_least(minimum):
            raise AccessError(f"needs {minimum} or above in this organization (you are {role})", 403)
        if self.via_mcp and minimum.at_least(OrgRole.admin):
            raise AccessError(
                "an MCP client can't manage the organization (members, keys, SSO); do it in the web app", 403
            )
        return role


@dataclass
class Access:
    """A principal acting for one org's seat (party) on one agreement."""

    principal: Principal
    party: Party
    org_role: OrgRole

    @property
    def actor(self) -> str:
        return f"{self.principal.actor}@org:{self.party.org_id}"

    def can(self, action: Action) -> bool:
        if self.principal.via_mcp and action not in MCP_ACTIONS:
            return False
        minimum = RULES[action].get(self.party.role)
        return minimum is not None and self.org_role.at_least(minimum)

    def require(self, action: Action) -> None:
        if self.can(action):
            return
        if self.principal.via_mcp and action not in MCP_ACTIONS:
            raise AccessError(f"an MCP client can't {action.replace('_', ' ')}; that step stays with a person", 403)
        minimum = RULES[action].get(self.party.role)
        if minimum is None:
            raise AccessError(f"a {self.party.role} on this agreement can't {action.replace('_', ' ')}", 403)
        raise AccessError(
            f"{action.replace('_', ' ')} needs {minimum} or above in your organization (you are {self.org_role})", 403
        )


def principal_from_token(session: Session, token: str | None) -> Principal:
    if not token:
        raise AccessError("sign in: send 'Authorization: Bearer <token>'", 401)
    row = session.exec(select(ApiToken).where(ApiToken.token_hash == hash_token(token))).first()
    now = utcnow()
    if row is None or row.revoked_at is not None or (row.expires_at and _utc(row.expires_at) < now):
        raise AccessError("token is invalid or expired; sign in again", 401)
    if row.kind == TokenKind.oauth_refresh:
        raise AccessError("refresh tokens can't be used as bearer tokens", 401)
    row.last_used_at = now
    if row.kind == TokenKind.api_key:
        return Principal(api_key=row, roles={row.org_id: OrgRole(row.org_role)})  # type: ignore[dict-item]
    user = session.get(User, row.user_id)
    if user is None:
        raise AccessError("token is invalid or expired; sign in again", 401)
    memberships = session.exec(select(Membership).where(Membership.user_id == user.id)).all()
    roles = {m.org_id: OrgRole(m.role) for m in memberships}
    if row.kind == TokenKind.oauth_access:
        # An MCP client acts for the person only within the org they chose at consent, with their current role
        # there; leaving the org (or being demoted) takes effect on the next call.
        roles = {row.org_id: roles[row.org_id]} if row.org_id in roles else {}
    return Principal(user=user, roles=roles, oauth_client_id=row.client_id)  # type: ignore[arg-type]


def _utc(dt):
    from app.db import as_utc

    return as_utc(dt)


def access_for(principal: Principal, agreement: Agreement, acting_org_id: str | None = None) -> Access:
    """Find the caller's seat on the agreement. A caller in several of its orgs must say which (X-Org-Id)."""
    seats = [p for p in agreement.parties if p.org_id in principal.roles]
    if acting_org_id:
        seats = [p for p in seats if p.org_id == acting_org_id]
    if not seats:
        raise AccessError("agreement not found", 404)  # same answer as "doesn't exist"
    if len(seats) > 1:
        raise AccessError("you belong to more than one party on this agreement; send X-Org-Id to choose", 409)
    party = seats[0]
    return Access(principal=principal, party=party, org_role=principal.roles[party.org_id])  # type: ignore[index]
