import json
from html import escape

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import escrow
from app.db import utcnow
from app.domain import MilestoneStatus, Role, SettlementStatus
from app.paypal import MockPayPal, PayPalGateway
from app.routes import AgreementView, MilestonePartyDep, SessionDep, _view

router = APIRouter(tags=["escrow"])
mock_router = APIRouter(prefix="/mock", tags=["mock"])


def _gateway(request: Request) -> PayPalGateway:
    return request.app.state.paypal


def _require(party, *roles: Role) -> None:
    if party.role not in {r.value for r in roles}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"only {' or '.join(r.value for r in roles)} can do this")


def _page(title: str, message: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p></body>",
        status_code=code,
    )


class FundingStarted(BaseModel):
    order_id: str
    approve_url: str = Field(..., description="Send the payer here to approve the hold in PayPal.")


class SettlementView(BaseModel):
    id: str
    attempt: int
    idempotency_key: str
    status: SettlementStatus
    paypal_ref: str | None
    milestone_status: MilestoneStatus


@router.post("/milestones/{milestone_id}/fund", summary="Payer starts a PayPal hold for one tranche")
def fund(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> FundingStarted:
    party, m = auth
    _require(party, Role.payer)
    order = escrow.start_funding(
        session, _gateway(request), m, f"party:{party.id}", request.app.state.settings.public_base_url
    )
    return FundingStarted(order_id=order.order_id, approve_url=order.approve_url)


@router.get("/paypal/return", include_in_schema=False)
def paypal_return(token: str, session: SessionDep, request: Request) -> HTMLResponse:
    try:
        m = escrow.complete_funding(session, _gateway(request), token, "payer:paypal_return", utcnow())
    except escrow.EscrowError as e:
        return _page("Funding didn't complete", e.message, e.status_code)
    return _page(
        "Tranche funded", f"{m.agreement.currency} {m.amount} for '{m.title}' is on hold. You can close this tab."
    )


@router.get("/paypal/cancel", include_in_schema=False)
def paypal_cancel() -> HTMLResponse:
    return _page("Funding cancelled", "No hold was placed. You can start again from the agreement.")


@router.post(
    "/milestones/{milestone_id}/release",
    summary="Capture a verified tranche's hold to the payee (also resumes or retries a failed release)",
)
def release(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> SettlementView:
    party, m = auth
    _require(party, Role.payer, Role.approver)
    s = escrow.release(session, _gateway(request), m, f"party:{party.id}", utcnow())
    return SettlementView(
        id=s.id,
        attempt=s.attempt,
        idempotency_key=s.idempotency_key,
        status=SettlementStatus(s.status),
        paypal_ref=s.paypal_ref,
        milestone_status=MilestoneStatus(m.status),
    )


@router.post("/milestones/{milestone_id}/void", summary="Payer cancels a tranche's hold before evidence arrives")
def void(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> AgreementView:
    party, m = auth
    _require(party, Role.payer)
    escrow.void(session, _gateway(request), m, f"party:{party.id}")
    return _view(m.agreement)


@router.post("/webhooks/paypal", summary="PayPal webhook intake (signature-verified, deduplicated)")
async def paypal_webhook(request: Request, session: SessionDep) -> dict[str, str]:
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from e
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not _gateway(request).verify_webhook(headers, event):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "webhook signature not verified")
    return {"outcome": escrow.handle_webhook(session, event, utcnow())}


# --- mock mode only --------------------------------------------------------------------


@mock_router.get("/paypal/approve", include_in_schema=False)
def mock_approve(token: str, request: Request) -> RedirectResponse:
    """Stands in for PayPal's approval page: approves and sends the payer back like PayPal does."""
    gw: MockPayPal = request.app.state.paypal
    gw.approve(token)
    return RedirectResponse(f"{gw.orders[token]['return_url']}?token={token}", status_code=303)


@mock_router.post(
    "/milestones/{milestone_id}/mark-verified",
    summary="Mock mode only: mark a tranche verified until the verifier lands (Nov 6)",
)
def mock_mark_verified(milestone_id: str, auth: MilestonePartyDep, session: SessionDep) -> AgreementView:
    party, m = auth
    _require(party, Role.payer)
    allowed = {MilestoneStatus.pending, MilestoneStatus.funded, MilestoneStatus.evidence_received,
               MilestoneStatus.needs_review}  # fmt: skip
    if MilestoneStatus(m.status) not in allowed:
        raise HTTPException(status.HTTP_409_CONFLICT, f"tranche is {m.status}")
    escrow._transition(session, m, MilestoneStatus.verified, f"party:{party.id}", "mock_mark_verified")
    session.commit()
    return _view(m.agreement)
