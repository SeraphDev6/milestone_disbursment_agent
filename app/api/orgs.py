"""Organizations: members, API keys, and the org-scoped views each side works from."""

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import Field, SQLModel, col, select

from app.api.auth import invitation_read
from app.api.deps import PrincipalDep, SessionDep, mailer, show_links
from app.api.views import funding_summary
from app.db import utcnow
from app.models import (
    Agreement,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    ApiToken,
    InvitationKind,
    InvitationRead,
    Membership,
    MembershipRead,
    MilestoneStatus,
    Organization,
    OrganizationCreate,
    OrganizationRead,
    OrgRole,
    Party,
    Role,
    TokenKind,
    UserRead,
)
from app.models.identity import EMAIL_PATTERN
from app.services import identity
from app.services.access import AccessError

router = APIRouter(tags=["organizations"])


class OrganizationUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    paypal_email: str | None = Field(default=None, max_length=320, schema_extra={"pattern": EMAIL_PATTERN})


class MemberInvite(SQLModel):
    email: str = Field(max_length=320, schema_extra={"pattern": EMAIL_PATTERN})
    role: OrgRole = OrgRole.member


class MemberUpdate(SQLModel):
    role: OrgRole


class InvitationSent(SQLModel):
    invitation: InvitationRead
    dev_link: str | None = Field(default=None, description="Mock mode only: the link that would have been emailed.")


class AgreementSummary(SQLModel):
    id: str
    title: str
    side: Role = Field(description="This org's role on the agreement.")
    status: str
    currency: str
    total_amount: Decimal
    held: Decimal
    in_dispute_window: Decimal = Field(description="Captured from the payer, waiting out the dispute window.")
    invoiced: Decimal
    released: Decimal
    refunded: Decimal = Field(description="Captured, then returned to the payer after a contest.")
    counterparties: list[str] = Field(description="The other parties' names.")
    tranches: int
    needs_attention: int = Field(
        description="Tranches waiting on this org: review/dispute for payer and reviewer, "
        "missing evidence or settlement problems for payee."
    )


class ReviewItem(SQLModel):
    agreement_id: str
    agreement_title: str
    milestone_id: str
    milestone_title: str
    amount: Decimal
    status: MilestoneStatus
    side: Role
    verdict_met: bool | None
    verdict_confidence: float | None
    second_opinion_disagreed: bool = Field(
        default=False,
        description="The skeptic pass disagreed with the verifier (or couldn't run), which is why it's in review.",
    )


class Portfolio(SQLModel):
    currency_totals: dict[str, dict[str, Decimal]] = Field(
        description="Per currency: total, held, in_dispute_window, invoiced, released, refunded."
    )
    agreements: list[AgreementSummary]


def _org(session, principal, org_id: str, minimum: OrgRole) -> Organization:
    principal.require_org(org_id, minimum)
    return session.get(Organization, org_id)


# --- organizations and members ---------------------------------------------------------


@router.post("/orgs", status_code=201, summary="Create an organization; you become its owner")
def create_org(body: OrganizationCreate, principal: PrincipalDep, session: SessionDep) -> OrganizationRead:
    org = identity.create_org(session, principal.require_person("create an organization"), body)
    return OrganizationRead.model_validate(org, update={"my_role": OrgRole.owner})


@router.get("/orgs/{org_id}", summary="An organization you belong to")
def get_org(org_id: str, principal: PrincipalDep, session: SessionDep) -> OrganizationRead:
    org = _org(session, principal, org_id, OrgRole.viewer)
    return OrganizationRead.model_validate(org, update={"my_role": principal.role_in(org_id)})


@router.patch("/orgs/{org_id}", summary="Rename or set the org's PayPal account (admin)")
def update_org(org_id: str, body: OrganizationUpdate, principal: PrincipalDep, session: SessionDep) -> OrganizationRead:
    org = _org(session, principal, org_id, OrgRole.admin)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(org, k, v)
    session.commit()
    return OrganizationRead.model_validate(org, update={"my_role": principal.role_in(org_id)})


@router.get("/orgs/{org_id}/members", summary="Members and their roles")
def list_members(org_id: str, principal: PrincipalDep, session: SessionDep) -> list[MembershipRead]:
    org = _org(session, principal, org_id, OrgRole.viewer)
    return [
        MembershipRead(role=m.role, org_id=org.id, org_name=org.name, user=UserRead.model_validate(m.user))
        for m in org.memberships
    ]


@router.post("/orgs/{org_id}/invitations", status_code=201, summary="Invite someone to join the org (admin)")
def invite_member(
    org_id: str, body: MemberInvite, principal: PrincipalDep, session: SessionDep, request: Request
) -> InvitationSent:
    org = _org(session, principal, org_id, OrgRole.admin)
    if body.role == OrgRole.owner and principal.role_in(org_id) != OrgRole.owner:
        raise AccessError("only an owner can invite another owner", 403)
    inv, link = identity.invite(
        session,
        mailer(request),
        kind=InvitationKind.member,
        email=body.email,
        actor=principal.actor,
        base_url=request.app.state.settings.api_base_url,
        app_url=request.app.state.app_url,
        org=org,
        org_role=body.role,
    )
    return InvitationSent(invitation=invitation_read(session, inv), dev_link=link if show_links(request) else None)


@router.patch("/orgs/{org_id}/members/{user_id}", summary="Change a member's role (admin; owners by owners)")
def update_member(
    org_id: str, user_id: str, body: MemberUpdate, principal: PrincipalDep, session: SessionDep
) -> MembershipRead:
    org = _org(session, principal, org_id, OrgRole.admin)
    m = session.exec(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)).first()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    if OrgRole.owner in (m.role, body.role) and principal.role_in(org_id) != OrgRole.owner:
        raise AccessError("only an owner can grant or change the owner role", 403)
    if m.role == OrgRole.owner and body.role != OrgRole.owner and _owner_count(org) == 1:
        raise AccessError("an organization needs at least one owner", 409)
    m.role = body.role
    session.commit()
    return MembershipRead(role=m.role, org_id=org.id, org_name=org.name, user=UserRead.model_validate(m.user))


@router.delete("/orgs/{org_id}/members/{user_id}", status_code=204, summary="Remove a member (admin)")
def remove_member(org_id: str, user_id: str, principal: PrincipalDep, session: SessionDep) -> Response:
    org = _org(session, principal, org_id, OrgRole.admin)
    m = session.exec(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)).first()
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "member not found")
    if m.role == OrgRole.owner and (principal.role_in(org_id) != OrgRole.owner or _owner_count(org) == 1):
        raise AccessError("owners are removed by another owner, and the last owner can't be removed", 409)
    session.delete(m)
    session.commit()
    return Response(status_code=204)


def _owner_count(org: Organization) -> int:
    return sum(1 for m in org.memberships if m.role == OrgRole.owner)


# --- API keys --------------------------------------------------------------------------


@router.post("/orgs/{org_id}/api-keys", status_code=201, summary="Create an API key that acts as this org (admin)")
def create_api_key(org_id: str, body: ApiKeyCreate, principal: PrincipalDep, session: SessionDep) -> ApiKeyCreated:
    _org(session, principal, org_id, OrgRole.admin)
    if not principal.role_in(org_id).at_least(body.org_role):  # type: ignore[union-attr]
        raise AccessError("you can't create a key with more rights than you have", 403)
    row, token = identity.create_api_key(session, org_id, body, principal.actor)
    return ApiKeyCreated(**ApiKeyRead.model_validate(row).model_dump(), token=token)


@router.get("/orgs/{org_id}/api-keys", summary="This org's API keys (admin)")
def list_api_keys(org_id: str, principal: PrincipalDep, session: SessionDep) -> list[ApiKeyRead]:
    _org(session, principal, org_id, OrgRole.admin)
    rows = session.exec(select(ApiToken).where(ApiToken.org_id == org_id, ApiToken.kind == TokenKind.api_key)).all()
    return [ApiKeyRead.model_validate(r) for r in rows]


@router.delete("/orgs/{org_id}/api-keys/{key_id}", status_code=204, summary="Revoke an API key (admin)")
def revoke_api_key(org_id: str, key_id: str, principal: PrincipalDep, session: SessionDep) -> Response:
    _org(session, principal, org_id, OrgRole.admin)
    row = session.get(ApiToken, key_id)
    if row is None or row.org_id != org_id or row.kind != TokenKind.api_key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    row.revoked_at = utcnow()
    session.commit()
    return Response(status_code=204)


# --- what each side works from ---------------------------------------------------------

ATTENTION = {
    Role.payer: {
        MilestoneStatus.needs_review,
        MilestoneStatus.disputed,
        MilestoneStatus.contested,
        MilestoneStatus.settlement_failed,
        MilestoneStatus.payout_failed,
    },
    Role.reviewer: {MilestoneStatus.needs_review, MilestoneStatus.disputed, MilestoneStatus.contested},
    Role.payee: {
        MilestoneStatus.pending,
        MilestoneStatus.funded,
        MilestoneStatus.contested,
        MilestoneStatus.settlement_failed,
        MilestoneStatus.payout_failed,
    },
}


def _seats(session, org_id: str, side: Role | None) -> list[Party]:
    query = select(Party).where(Party.org_id == org_id)
    if side is not None:
        query = query.where(Party.role == side)
    return list(session.exec(query).all())


def _summary(party: Party) -> AgreementSummary:
    a: Agreement = party.agreement
    f = funding_summary(a)
    return AgreementSummary(
        id=a.id,
        title=a.title,
        side=party.role,
        status=a.status,
        currency=a.currency,
        total_amount=a.total_amount,
        held=f.held,
        in_dispute_window=f.in_dispute_window,
        invoiced=f.invoiced,
        released=f.released,
        refunded=f.refunded,
        counterparties=[p.display_name for p in a.parties if p.id != party.id],
        tranches=len(a.milestones),
        needs_attention=sum(1 for m in a.milestones if m.status in ATTENTION[party.role]),
    )


@router.get(
    "/orgs/{org_id}/agreements",
    openapi_extra={"x-mcp-tool": "list_agreements"},
    summary="Agreements this org is party to, optionally by side",
)
def list_agreements(
    org_id: str,
    principal: PrincipalDep,
    session: SessionDep,
    side: Literal["payer", "payee", "reviewer"] | None = None,
) -> list[AgreementSummary]:
    principal.require_org(org_id, OrgRole.viewer)
    seats = _seats(session, org_id, Role(side) if side else None)
    return sorted((_summary(p) for p in seats), key=lambda s: (-s.needs_attention, s.title))


@router.get(
    "/orgs/{org_id}/review-queue",
    openapi_extra={"x-mcp-tool": "review_queue"},
    summary="Tranches waiting on this org's decision (as payer or reviewer)",
)
def review_queue(org_id: str, principal: PrincipalDep, session: SessionDep) -> list[ReviewItem]:
    principal.require_org(org_id, OrgRole.viewer)
    items = []
    for party in session.exec(
        select(Party).where(Party.org_id == org_id, col(Party.role).in_((Role.payer, Role.reviewer)))
    ).all():
        for m in party.agreement.milestones:
            if m.status not in (MilestoneStatus.needs_review, MilestoneStatus.disputed, MilestoneStatus.contested):
                continue
            v = next((v for v in reversed(m.verdicts) if not v.is_precheck), None)
            items.append(
                ReviewItem(
                    agreement_id=party.agreement_id,
                    agreement_title=party.agreement.title,
                    milestone_id=m.id,
                    milestone_title=m.title,
                    amount=m.amount,
                    status=m.status,
                    side=party.role,
                    verdict_met=v.met if v else None,
                    verdict_confidence=v.model_confidence if v else None,
                    second_opinion_disagreed=bool(v and v.skeptic and (v.skeptic.disagrees_on or v.skeptic.error)),
                )
            )
    return items


@router.get(
    "/orgs/{org_id}/portfolio",
    openapi_extra={"x-mcp-tool": "get_portfolio"},
    summary="Money across all agreements this org pays: total, held, invoiced, released",
)
def portfolio(org_id: str, principal: PrincipalDep, session: SessionDep) -> Portfolio:
    principal.require_org(org_id, OrgRole.viewer)
    summaries = [_summary(p) for p in _seats(session, org_id, Role.payer)]
    totals: dict[str, dict[str, Decimal]] = {}
    for s in summaries:
        buckets = ("total", "held", "in_dispute_window", "invoiced", "released", "refunded")
        t = totals.setdefault(s.currency, {k: Decimal("0.00") for k in buckets})
        t["total"] += s.total_amount
        t["held"] += s.held
        t["in_dispute_window"] += s.in_dispute_window
        t["invoiced"] += s.invoiced
        t["released"] += s.released
        t["refunded"] += s.refunded
    return Portfolio(currency_totals=totals, agreements=summaries)
