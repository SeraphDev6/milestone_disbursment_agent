"""Subscriptions (outbound webhooks), audit log, compliance packet, disputes, mock invoice payment."""

import secrets
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import escrow, verification
from app.config import Mode
from app.db import AuditEventRow, SubscriptionRow, record_audit, utcnow
from app.domain import MilestoneStatus, Role
from app.escrow_routes import _page, _require
from app.outbound import DestinationError, check_destination
from app.packet import build_packet, milestone_section, render_pdf
from app.routes import MilestonePartyDep, PartyDep, SessionDep

router = APIRouter()
mock_router = APIRouter(prefix="/mock", tags=["mock"])


# --- outbound webhook subscriptions ----------------------------------------------------


class SubscriptionIn(BaseModel):
    url: str = Field(..., max_length=1000, description="HTTPS endpoint that receives signed JSON events.")
    events: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Event types (milestone.status_changed), prefixes ending in '.' (milestone.), or '*'.",
    )


class SubscriptionView(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: datetime


class SubscriptionCreated(SubscriptionView):
    secret: str = Field(
        ..., description="Shown once. Verify X-MDE-Signature with HMAC-SHA256(secret, ts + '.' + body)."
    )


def _sub_view(s: SubscriptionRow) -> SubscriptionView:
    return SubscriptionView(id=s.id, url=s.url, events=s.events, active=s.active, created_at=s.created_at)


@router.post(
    "/agreements/{agreement_id}/subscriptions", tags=["events"], summary="Subscribe a URL to this agreement's events"
)
def subscribe(
    agreement_id: str, body: SubscriptionIn, party: PartyDep, session: SessionDep, request: Request
) -> SubscriptionCreated:
    state = request.app.state
    try:
        check_destination(body.url, allow_http=state.settings.mode is Mode.mock, resolve=state.resolve)
    except DestinationError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    sub = SubscriptionRow(
        agreement_id=agreement_id,
        url=body.url,
        events=body.events or ["*"],
        secret=secrets.token_hex(32),
        created_by=f"party:{party.id}",
    )
    session.add(sub)
    session.flush()
    record_audit(session, agreement_id, f"party:{party.id}", "subscription_created", detail={"url": body.url})
    session.commit()
    return SubscriptionCreated(**_sub_view(sub).model_dump(), secret=sub.secret)


@router.get("/agreements/{agreement_id}/subscriptions", tags=["events"], summary="Subscriptions on this agreement")
def list_subscriptions(agreement_id: str, party: PartyDep, session: SessionDep) -> list[SubscriptionView]:
    subs = session.scalars(select(SubscriptionRow).where(SubscriptionRow.agreement_id == agreement_id)).all()
    return [_sub_view(s) for s in subs]


@router.delete("/agreements/{agreement_id}/subscriptions/{subscription_id}", tags=["events"], status_code=204)
def unsubscribe(agreement_id: str, subscription_id: str, party: PartyDep, session: SessionDep) -> Response:
    sub = session.get(SubscriptionRow, subscription_id)
    if sub is None or sub.agreement_id != agreement_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    if sub.created_by != f"party:{party.id}" and party.role != Role.payer.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only its creator or the payer can remove a subscription")
    sub.active = False
    record_audit(session, agreement_id, f"party:{party.id}", "subscription_removed", detail={"url": sub.url})
    session.commit()
    return Response(status_code=204)


# --- audit -----------------------------------------------------------------------------


class AuditEntry(BaseModel):
    at: datetime
    actor: str
    action: str
    milestone_id: str | None
    from_status: str | None
    to_status: str | None
    detail: dict


def _audit(rows) -> list[AuditEntry]:
    return [
        AuditEntry(
            at=e.at,
            actor=e.actor,
            action=e.action,
            milestone_id=e.milestone_id,
            from_status=e.from_status,
            to_status=e.to_status,
            detail=e.detail,
        )
        for e in rows
    ]


@router.get("/agreements/{agreement_id}/audit", tags=["audit"], summary="Full event log for an agreement")
def agreement_audit(agreement_id: str, party: PartyDep, session: SessionDep) -> list[AuditEntry]:
    return _audit(
        session.scalars(
            select(AuditEventRow).where(AuditEventRow.agreement_id == agreement_id).order_by(AuditEventRow.at)
        ).all()
    )


@router.get(
    "/milestones/{milestone_id}/audit",
    tags=["audit"],
    openapi_extra={"x-mcp-tool": "explain_milestone"},
    summary="Every event for one tranche: who did what, when, and why",
)
def milestone_audit(milestone_id: str, auth: MilestonePartyDep, session: SessionDep) -> list[AuditEntry]:
    _, m = auth
    return _audit(
        session.scalars(
            select(AuditEventRow).where(AuditEventRow.milestone_id == m.id).order_by(AuditEventRow.at)
        ).all()
    )


@router.get(
    "/agreements/{agreement_id}/compliance-packet",
    tags=["audit"],
    openapi_extra={"x-mcp-tool": "export_compliance_packet"},
    summary="Criteria, confirmations, evidence, verdicts with citations, reviews, payments and audit trail",
)
def compliance_packet(
    agreement_id: str, party: PartyDep, session: SessionDep, format: Literal["json", "pdf"] = "json"
) -> Response:
    _require(party, Role.payer, Role.approver)
    packet = build_packet(session, party.agreement)
    if format == "pdf":
        return Response(
            render_pdf(packet),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="compliance-{agreement_id}.pdf"'},
        )
    import json

    return Response(json.dumps(packet, indent=2, default=str), media_type="application/json")


# --- disputes --------------------------------------------------------------------------


class DisputeIn(BaseModel):
    reason: str = Field(..., min_length=10, max_length=4000, description="Why the rejection is wrong.")


class DisputePack(BaseModel):
    milestone_status: MilestoneStatus
    reason: str
    pack: dict = Field(..., description="Criteria, evidence, verdicts, reviews, payments and audit for this tranche.")


@router.post(
    "/milestones/{milestone_id}/dispute",
    tags=["evidence"],
    summary="Payee disputes a rejection; the payer side must review the tranche again",
)
def dispute(milestone_id: str, body: DisputeIn, auth: MilestonePartyDep, session: SessionDep) -> DisputePack:
    party, m = auth
    _require(party, Role.payee)
    verification.dispute(session, m, party, body.reason)
    return DisputePack(
        milestone_status=MilestoneStatus(m.status), reason=body.reason, pack=milestone_section(session, m)
    )


# --- mock mode: the payer paying a PayPal invoice --------------------------------------


@mock_router.get("/paypal/invoices/{invoice_id}", include_in_schema=False)
def mock_invoice_page(invoice_id: str, request: Request) -> HTMLResponse:
    inv = request.app.state.paypal.invoices.get(invoice_id)
    if inv is None:
        return _page("Invoice not found", invoice_id, 404)
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>Mock PayPal invoice</title>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'><h1>Invoice {invoice_id}</h1>"
        f"<p>{inv['currency']} {inv['amount']} from {inv['invoicer']}: {inv['status']}</p>"
        f"<form method=post action='/mock/paypal/invoices/{invoice_id}/pay'><button>Pay invoice</button></form>"
    )


@mock_router.post("/paypal/invoices/{invoice_id}/pay", include_in_schema=False)
def mock_pay_invoice(invoice_id: str, request: Request, session: SessionDep) -> RedirectResponse:
    """Stands in for the payer paying in PayPal: marks it paid and sends the webhook PayPal would send."""
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
        escrow.handle_webhook(session, event, utcnow())
    return RedirectResponse(f"/mock/paypal/invoices/{invoice_id}", status_code=303)
