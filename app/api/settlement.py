"""Money movement: fund a tranche's hold, release it, void it, and PayPal's callbacks."""

import json

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlmodel import Field, SQLModel

from app.api.deps import MilestonePartyDep, SessionDep, gateway, require_role
from app.api.views import agreement_read, html_page
from app.db import utcnow
from app.models import AgreementRead, Role, SettlementRead
from app.services import ServiceError, settlement

router = APIRouter(tags=["settlement"])


class FundingStarted(SQLModel):
    order_id: str
    approve_url: str = Field(..., description="Send the payer here to approve the hold in PayPal.")


@router.post("/milestones/{milestone_id}/fund", summary="Payer starts a PayPal hold for one tranche")
def fund(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> FundingStarted:
    party, m = auth
    require_role(party, Role.payer)
    order = settlement.start_funding(
        session, gateway(request), m, f"party:{party.id}", request.app.state.settings.public_base_url
    )
    return FundingStarted(order_id=order.order_id, approve_url=order.approve_url)


@router.get("/paypal/return", include_in_schema=False)
def paypal_return(token: str, session: SessionDep, request: Request) -> HTMLResponse:
    try:
        m = settlement.complete_funding(session, gateway(request), token, "payer:paypal_return", utcnow())
    except ServiceError as e:
        return html_page("Funding didn't complete", e.message, e.status_code)
    return html_page(
        "Tranche funded", f"{m.agreement.currency} {m.amount} for '{m.title}' is on hold. You can close this tab."
    )


@router.get("/paypal/cancel", include_in_schema=False)
def paypal_cancel() -> HTMLResponse:
    return html_page("Funding cancelled", "No hold was placed. You can start again from the agreement.")


@router.post(
    "/milestones/{milestone_id}/release",
    summary="Settle a verified tranche: capture its hold or send its invoice (also resumes a failed release)",
)
def release(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> SettlementRead:
    party, m = auth
    require_role(party, Role.payer, Role.approver)
    s = settlement.release(session, gateway(request), m, f"party:{party.id}", utcnow())
    return SettlementRead.model_validate(s, update={"milestone_status": m.status})


@router.post("/milestones/{milestone_id}/void", summary="Payer cancels a tranche's hold before evidence arrives")
def void(milestone_id: str, auth: MilestonePartyDep, session: SessionDep, request: Request) -> AgreementRead:
    party, m = auth
    require_role(party, Role.payer)
    settlement.void(session, gateway(request), m, f"party:{party.id}")
    return agreement_read(m.agreement)


@router.post("/webhooks/paypal", summary="PayPal webhook intake (signature-verified, deduplicated)")
async def paypal_webhook(request: Request, session: SessionDep) -> dict[str, str]:
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from e
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not gateway(request).verify_webhook(headers, event):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "webhook signature not verified")
    return {"outcome": settlement.handle_webhook(session, event, utcnow())}
