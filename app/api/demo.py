"""The demo (/demo), on sandbox deployments only: a ready-made grant for judges and anyone trying the product.

Everything the demo does goes through the ordinary API as its three people (funder, grantee, reviewer); this
module only provides what they can't do for themselves:

- `POST /demo/cast` signs the three people in without email. Their addresses are at DEMO_EMAIL_DOMAIN, a reserved
  domain nothing is delivered to, so nobody else can ever sign in as them, and the mailer drops their mail.
- `POST /demo/milestones/{id}/mark-verified` skips evidence and the verifier, on demo agreements only (every member
  of every party's organization is a demo person).
- `GET /demo` tells the demo page which PayPal sandbox accounts to use, when the deployment publishes them.

PayPal is the real sandbox: holds are approved and payees connected on PayPal's sandbox pages, and no real money
moves. The web app's /demo page drives all of this (web/src/features/demo).
"""

import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from sqlmodel import Field, SQLModel, select

from app.api.deps import MilestoneAccessDep, SessionDep, client_ip, rate_limit
from app.api.views import agreement_read
from app.config import DEMO_EMAIL_DOMAIN
from app.models import Agreement, AgreementRead, Membership, MilestoneStatus, User, UserRead
from app.models.agreements import Milestone
from app.services import identity, settlement
from app.services.access import Access, Action

router = APIRouter(prefix="/demo", tags=["demo"])

Key = Literal["funder", "grantee", "reviewer"]
CAST: list[tuple[Key, str]] = [("funder", "officer"), ("grantee", "director"), ("reviewer", "auditor")]


class PayPalAccount(SQLModel):
    email: str
    password: str


class DemoInfo(SQLModel):
    mode: str
    paypal_web_url: str = Field(description="Where the demo's PayPal pages are: PayPal's sandbox.")
    paypal_payer: PayPalAccount | None = Field(
        default=None,
        description="A sandbox account to approve holds and pay links with, when the deployment publishes one.",
    )
    paypal_payee: PayPalAccount | None = Field(
        default=None, description="A sandbox business account to connect as the grantee's PayPal, when published."
    )


class DemoPerson(SQLModel):
    key: Key
    email: str
    token: str = Field(description="A session for this person: 'Authorization: Bearer <token>'.")
    user: UserRead


class DemoCast(SQLModel):
    code: str
    people: list[DemoPerson]


def is_demo_email(email: str) -> bool:
    return email.lower().endswith("@" + DEMO_EMAIL_DOMAIN)


def is_demo_agreement(session, agreement: Agreement) -> bool:
    """Every member of every party's organization is a demo person. A real organization that joined a demo
    agreement, or a demo person who joined a real one, makes it a real agreement."""
    org_ids = [p.org_id for p in agreement.parties if p.org_id]
    if not org_ids:
        return False
    emails = session.exec(
        select(User.email).join(Membership, Membership.user_id == User.id).where(Membership.org_id.in_(org_ids))  # type: ignore[attr-defined]
    ).all()
    return bool(emails) and all(is_demo_email(e) for e in emails)


def mark_verified(session, access: Access, m: Milestone, action: str) -> AgreementRead:
    """The shortcut itself, shared with the stand-in router used by tests."""
    access.require(Action.release)
    allowed = {
        MilestoneStatus.pending,
        MilestoneStatus.funded,
        MilestoneStatus.overdue,
        MilestoneStatus.evidence_received,
        MilestoneStatus.needs_review,
    }
    if MilestoneStatus(m.status) not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tranche is {m.status}")
    settlement.transition(session, m, MilestoneStatus.verified, access.actor, action)
    session.commit()
    return agreement_read(m.agreement)


@router.get("", summary="What the demo page needs: which PayPal, and the sandbox accounts to use, if published")
def info(request: Request) -> DemoInfo:
    s = request.app.state.settings
    payer = payee = None
    if s.paypal_test_payer_email and s.paypal_test_payer_password:
        payer = PayPalAccount(email=s.paypal_test_payer_email, password=s.paypal_test_payer_password.get_secret_value())
    if s.paypal_test_payee_email and s.paypal_test_payee_password:
        payee = PayPalAccount(email=s.paypal_test_payee_email, password=s.paypal_test_payee_password.get_secret_value())
    return DemoInfo(mode=s.mode.value, paypal_web_url=s.paypal_web_url, paypal_payer=payer, paypal_payee=payee)


@router.post("/cast", summary="Sign in the demo's three people (funder, grantee, reviewer) without email")
def cast(request: Request, session: SessionDep) -> DemoCast:
    """Three fresh people at the demo domain, each with a session. They then use the API like anyone else: the
    funder creates an organization and the grant, the others accept their invitations from GET /me."""
    rate_limit(request, "demo", client_ip(request))
    code = secrets.token_hex(3)
    people = []
    for key, name in CAST:
        user, token = identity.sign_in(session, f"{name}-{code}@{DEMO_EMAIL_DOMAIN}")
        people.append(DemoPerson(key=key, email=user.email, token=token, user=UserRead.model_validate(user)))
    return DemoCast(code=code, people=people)


@router.post("/milestones/{milestone_id}/mark-verified", summary="Demo only: mark a demo tranche verified")
def mark_verified_demo(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep) -> AgreementRead:
    """Skips evidence and the verifier so a visitor can get to funding and release. Only on agreements whose
    every participant is a demo person."""
    access, m = auth
    if not is_demo_agreement(session, m.agreement):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this shortcut only works on demo agreements")
    return mark_verified(session, access, m, "demo_mark_verified")
