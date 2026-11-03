"""Mock mode only: stand-ins for PayPal's pages, so the whole flow runs without keys."""

import uuid

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.browser import api_path
from app.api.deps import MilestoneAccessDep, SessionDep
from app.api.views import agreement_read, html_page
from app.db import utcnow
from app.integrations.paypal import MockPayPal
from app.models import AgreementRead, MilestoneStatus
from app.services import settlement
from app.services.access import Action

router = APIRouter(prefix="/mock", tags=["mock"])


@router.get("/paypal/approve", include_in_schema=False)
def approve_order(token: str, request: Request) -> RedirectResponse:
    """PayPal's approval page: approves the hold and sends the payer back like PayPal does."""
    gw: MockPayPal = request.app.state.paypal
    gw.approve(token)
    return RedirectResponse(f"{gw.orders[token]['return_url']}?token={token}", status_code=303)


@router.get("/paypal/onboard", include_in_schema=False)
def onboard(tracking_id: str, request: Request) -> RedirectResponse:
    """PayPal's seller onboarding: the payee signs in, grants permissions, and is sent back like PayPal does."""
    gw: MockPayPal = request.app.state.paypal
    if tracking_id not in gw.referrals:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown onboarding")
    mid = gw.complete_onboarding(tracking_id)
    target = gw.referrals[tracking_id]["return_url"]
    return RedirectResponse(f"{target}?merchantId={tracking_id}&merchantIdInPayPal={mid}", status_code=303)


@router.get("/paypal/invoices/{invoice_id}", include_in_schema=False)
def invoice_page(invoice_id: str, request: Request) -> HTMLResponse:
    inv = request.app.state.paypal.invoices.get(invoice_id)
    if inv is None:
        return html_page("Invoice not found", invoice_id, 404)
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>Mock PayPal invoice</title>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'><h1>Invoice {invoice_id}</h1>"
        f"<p>{inv['currency']} {inv['amount']} from {inv['invoicer']}: {inv['status']}</p>"
        f"<form method=post action='{api_path(request, f'/mock/paypal/invoices/{invoice_id}/pay')}'>"
        "<button>Pay invoice</button></form>"
    )


@router.post("/paypal/invoices/{invoice_id}/pay", include_in_schema=False)
def pay_invoice(invoice_id: str, request: Request, session: SessionDep) -> RedirectResponse:
    """The payer paying in PayPal: marks it paid and sends the webhook PayPal would send."""
    inv = request.app.state.paypal.invoices.get(invoice_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown invoice")
    if inv["status"] == "SENT":
        inv["status"] = "PAID"
        event = {
            "id": f"MOCK-WH-{uuid.uuid4().hex}",
            "event_type": "INVOICING.INVOICE.PAID",
            "resource": {"id": invoice_id},
        }
        settlement.handle_webhook(session, event, utcnow(), request.app.state.paypal)
    return RedirectResponse(api_path(request, f"/mock/paypal/invoices/{invoice_id}"), status_code=303)


@router.post("/milestones/{milestone_id}/mark-verified", summary="Mock mode only: mark a tranche verified")
def mark_verified(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep) -> AgreementRead:
    access, m = auth
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
    settlement.transition(session, m, MilestoneStatus.verified, access.actor, "mock_mark_verified")
    session.commit()
    return agreement_read(m.agreement)
