"""Connecting a payee organization's PayPal account, so tranches are paid straight into it."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.deps import PrincipalDep, SessionDep, gateway
from app.api.views import html_page
from app.db import utcnow
from app.models import Organization, OrgRole, PayPalAccountRead, PayPalConnectStarted
from app.services import ServiceError, payee_accounts

router = APIRouter(tags=["paypal"])


@router.post(
    "/orgs/{org_id}/paypal/connect",
    summary="Start connecting this org's PayPal account (admin); returns PayPal's onboarding link",
)
def connect(org_id: str, principal: PrincipalDep, session: SessionDep, request: Request) -> PayPalConnectStarted:
    principal.require_org(org_id, OrgRole.admin)
    org = session.get(Organization, org_id)
    url = payee_accounts.start(
        session, gateway(request), org, principal.actor, request.app.state.settings.public_base_url
    )
    return PayPalConnectStarted(onboarding_url=url)


@router.get("/orgs/{org_id}/paypal", summary="Whether this org's PayPal account is connected and can be paid")
def status(org_id: str, principal: PrincipalDep, session: SessionDep) -> PayPalAccountRead:
    principal.require_org(org_id, OrgRole.viewer)
    return payee_accounts.read(payee_accounts.account_for(session, org_id), org_id)


@router.post("/orgs/{org_id}/paypal/refresh", summary="Re-read this org's PayPal account status from PayPal (admin)")
def refresh(org_id: str, principal: PrincipalDep, session: SessionDep, request: Request) -> PayPalAccountRead:
    principal.require_org(org_id, OrgRole.admin)
    row = payee_accounts.account_for(session, org_id)
    if row is None:
        raise ServiceError("PayPal isn't connected; start with POST /orgs/{org_id}/paypal/connect", 404)
    return payee_accounts.read(
        payee_accounts.refresh(session, gateway(request), row, principal.actor, utcnow()), org_id
    )


@router.get("/paypal/onboarded", include_in_schema=False)
def onboarded(request: Request, session: SessionDep, merchantId: str = "") -> HTMLResponse:
    """PayPal sends the payee back here; merchantId is our tracking id. Status is read from PayPal, not the URL."""
    try:
        row = payee_accounts.finish(session, gateway(request), merchantId, utcnow())
    except ServiceError as e:
        return html_page("PayPal not connected", e.message, e.status_code)
    if row.ready:
        return html_page(
            "PayPal connected", "Tranches will be paid straight into this account. You can close this tab."
        )
    return html_page("PayPal connection incomplete", "; ".join(row.issues()).capitalize() + ".", 409)
