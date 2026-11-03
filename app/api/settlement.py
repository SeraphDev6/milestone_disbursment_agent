"""Money movement: fund a tranche's hold, release it, void it, and PayPal's callbacks."""

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlmodel import Field, SQLModel

from app.api.deps import MilestoneAccessDep, SessionDep, gateway
from app.api.views import agreement_read, html_page
from app.db import utcnow
from app.models import AgreementRead, DisbursementRead, SettlementRead
from app.services import ServiceError, settlement
from app.services.access import Action

router = APIRouter(tags=["settlement"])


class FundingStarted(SQLModel):
    order_id: str
    approve_url: str = Field(..., description="Send the payer here to approve the hold in PayPal.")


@router.post("/milestones/{milestone_id}/fund", summary="Payer starts a PayPal hold for one tranche")
def fund(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> FundingStarted:
    access, m = auth
    access.require(Action.fund)
    order = settlement.start_funding(
        session, gateway(request), m, access.actor, request.app.state.settings.public_base_url, utcnow()
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
def release(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> SettlementRead:
    access, m = auth
    access.require(Action.release)
    s = settlement.release(session, gateway(request), m, access.actor, utcnow())
    return SettlementRead.model_validate(s, update={"milestone_status": m.status})


class ContestIn(SQLModel):
    reason: str = Field(min_length=1, max_length=2000, description="What's wrong; goes in the audit log.")


class ResolveIn(SQLModel):
    decision: Literal["release", "refund"] = Field(
        description="release pays the payee (payer or reviewer); refund returns the money to the payer "
        "(payee or reviewer)."
    )
    note: str = Field(default="", max_length=2000)


def _disbursement_read(d, m) -> DisbursementRead:
    return DisbursementRead.model_validate(d, update={"milestone_status": m.status})


@router.post(
    "/milestones/{milestone_id}/payout",
    summary="Pay a captured tranche to the payee now (waives the rest of the dispute window; retries a failed payout)",
)
def payout(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> DisbursementRead:
    access, m = auth
    access.require(Action.release)
    return _disbursement_read(settlement.pay_out(session, gateway(request), m, access.actor, utcnow()), m)


@router.post(
    "/milestones/{milestone_id}/contest",
    summary="Object to a captured tranche during its dispute window; its payout waits for a resolution",
)
def contest(milestone_id: str, body: ContestIn, auth: MilestoneAccessDep, session: SessionDep) -> AgreementRead:
    access, m = auth
    access.require(Action.contest)
    settlement.contest(session, m, access.actor, body.reason, utcnow())
    return agreement_read(m.agreement)


@router.post("/milestones/{milestone_id}/resolve", summary="End a contest: pay the payee or refund the payer")
def resolve(
    milestone_id: str, body: ResolveIn, auth: MilestoneAccessDep, session: SessionDep, request: Request
) -> DisbursementRead:
    access, m = auth
    access.require(Action.resolve_for_payee if body.decision == "release" else Action.resolve_for_payer)
    d = settlement.resolve(session, gateway(request), m, access.actor, body.decision, body.note, utcnow())
    return _disbursement_read(d, m)


@router.post("/milestones/{milestone_id}/void", summary="Payer cancels a tranche's hold before evidence arrives")
def void(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> AgreementRead:
    access, m = auth
    access.require(Action.void)
    settlement.void(session, gateway(request), m, access.actor)
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
