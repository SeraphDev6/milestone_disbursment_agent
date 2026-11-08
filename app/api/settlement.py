"""Money movement: fund a tranche's hold, release it, void it, and PayPal's callbacks."""

import json
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Field, SQLModel

from app.api.deps import MilestoneAccessDep, SessionDep, client_ip, gateway, rate_limit
from app.api.views import agreement_read, html_page
from app.db import utcnow
from app.models import AgreementRead, DisbursementRead, MilestoneStatus, SettlementRead
from app.services import ServiceError, cofunding, settlement
from app.services.access import Action

router = APIRouter(tags=["settlement"])


class FundingStarted(SQLModel):
    order_id: str = Field(description="The PayPal order holding the authorization; the approve_url refers to it.")
    approve_url: str = Field(..., description="Send the payer here to approve the hold in PayPal.")


@router.post(
    "/milestones/{milestone_id}/fund",
    openapi_extra={"x-sdk-core": "fundMilestone"},
    summary="Payer starts a PayPal hold for one tranche (co-funded: for this funder's share of it)",
)
def fund(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> FundingStarted:
    access, m = auth
    access.require(Action.fund)
    order = settlement.start_funding(
        session,
        gateway(request),
        m,
        access.actor,
        request.app.state.settings.api_base_url,
        utcnow(),
        party=access.party,
    )
    return FundingStarted(order_id=order.order_id, approve_url=order.approve_url)


@router.get("/paypal/return", include_in_schema=False)
def paypal_return(token: str, session: SessionDep, request: Request) -> HTMLResponse:
    try:
        m = settlement.complete_funding(session, gateway(request), token, "payer:paypal_return", utcnow())
    except ServiceError as e:
        return html_page("Funding didn't complete", e.message, e.status_code)
    if settlement.fully_funded(m):
        text = f"{m.agreement.currency} {m.amount} for '{m.title}' is on hold. You can close this tab."
    else:
        waiting = ", ".join(p.display_name for p, _ in settlement.missing_funders(m))
        text = f"Your share of '{m.title}' is on hold; the tranche is funded once {waiting} hold theirs. You can close this tab."
    return html_page("Tranche funded" if settlement.fully_funded(m) else "Share held", text)


@router.get("/paypal/cancel", include_in_schema=False)
def paypal_cancel(session: SessionDep, token: str | None = None) -> HTMLResponse:
    if token:
        settlement.cancel_funding(session, token, "payer:paypal_cancel")
    return html_page("Funding cancelled", "No hold was placed. You can start again from the agreement.")


@router.post(
    "/milestones/{milestone_id}/release",
    openapi_extra={"x-sdk-core": "releaseMilestone"},
    summary="Settle a verified tranche: capture its hold or send its invoice (also resumes a failed release)",
)
def release(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> SettlementRead:
    access, m = auth
    access.require(Action.release)
    s = settlement.release(session, gateway(request), m, access.actor, utcnow())
    return settlement_read(s, m, request)


def settlement_read(s, m, request: Request) -> SettlementRead:
    path = settlement.pay_path(s)
    base = request.app.state.settings.api_base_url
    return SettlementRead.model_validate(
        s, update={"milestone_status": m.status, "pay_url": f"{base}{path}" if path else None}
    )


@router.get("/pay/{settlement_id}", include_in_schema=False)
def pay(settlement_id: str, session: SessionDep, request: Request, t: str | None = None) -> Response:
    """The payer's signed link for an invoice-mode tranche: a fresh PayPal order (or the PayPal invoice) each visit,
    for as long as the signature is valid (pay_link_valid_days)."""
    try:
        rate_limit(request, "pay_link", client_ip(request))
        base = request.app.state.settings.api_base_url
        url = settlement.payment_link(session, gateway(request), settlement_id, base, t, utcnow())
    except ServiceError as e:
        return html_page("Nothing to pay", e.message, e.status_code)
    return RedirectResponse(url, status_code=303)


@router.post(
    "/milestones/{milestone_id}/pay-link",
    openapi_extra={"x-sdk-core": "resendPayLink"},
    summary="Invoice mode: email the payer side a fresh pay link and return it (links expire; this reissues one)",
)
def resend_pay_link(
    milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request
) -> SettlementRead:
    access, m = auth
    access.require(Action.release)
    s = settlement.resend_pay_link(session, m, access.actor, utcnow())
    return settlement_read(s, m, request)


@router.get("/paypal/paid", include_in_schema=False)
def paypal_paid(token: str, session: SessionDep, request: Request) -> HTMLResponse:
    try:
        m = settlement.complete_payment(session, gateway(request), token, utcnow())
    except ServiceError as e:
        return html_page("Payment didn't complete", e.message, e.status_code)
    if m.status == MilestoneStatus.settled:
        return html_page("Paid", f"{m.agreement.currency} {m.amount} for '{m.title}' was paid. You can close this tab.")
    return html_page(
        "Payment processing", "PayPal is still processing this payment; the tranche settles when it clears."
    )


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
    openapi_extra={"x-sdk-core": "payoutMilestone"},
    summary="Pay a captured tranche to the payee now (waives the rest of the dispute window; retries a failed "
    "payout, including a partial share's)",
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
    d = settlement.resolve(
        session, gateway(request), m, access.actor, body.decision, body.note, utcnow(), party=access.party
    )
    return _disbursement_read(d, m)


@router.post(
    "/milestones/{milestone_id}/void",
    summary="Payer cancels a tranche's hold before evidence arrives (co-funded: the lead cancels every hold, a "
    "co-funder its own)",
)
def void(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, request: Request) -> AgreementRead:
    access, m = auth
    access.require(Action.void)
    settlement.void(session, gateway(request), m, access.actor, party=access.party)
    return agreement_read(m.agreement)


class CoverShare(SQLModel):
    email: str | None = Field(
        default=None, description="The co-funder (by invited email) taking on this part of the gap; null for the lead."
    )
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, description="How much of the gap they take on.")


class ShortfallIn(SQLModel):
    decision: Literal["void", "shrink", "cover"] = Field(
        description="void releases every hold placed so far (the tranche goes back to pending); shrink proposes the "
        "tranche at what was funded, for the payee to accept; cover reassigns the unfunded part to the funders named "
        "in `cover`, who agree by funding it."
    )
    cover: list[CoverShare] = Field(default_factory=list, description="cover: who takes how much of the gap.")
    note: str = Field(default="", max_length=2000, description="Kept in the audit record and sent with the notices.")


class ShortfallAnswer(SQLModel):
    accept: bool = Field(description="true: the tranche shrinks to the funded amount; false: the proposal is dropped.")
    note: str = Field(default="", max_length=2000, description="Kept in the audit record and sent to the lead funder.")


@router.post(
    "/milestones/{milestone_id}/shortfall",
    openapi_extra={"x-sdk-core": "resolveShortfall"},
    summary="Co-funded: the lead funder decides a tranche some funders never held (after the funding window)",
)
def shortfall(
    milestone_id: str, body: ShortfallIn, auth: MilestoneAccessDep, session: SessionDep, request: Request
) -> AgreementRead:
    access, m = auth
    access.require(Action.resolve_shortfall)
    cover = [(c.email, c.amount) for c in body.cover]
    cofunding.resolve_shortfall(session, gateway(request), m, access.actor, body.decision, cover, body.note, utcnow())
    return agreement_read(m.agreement)


@router.post(
    "/milestones/{milestone_id}/shortfall/answer",
    openapi_extra={"x-sdk-core": "answerShortfall"},
    summary="Co-funded: the payee accepts or declines the lead funder's proposal to shrink a tranche",
)
def answer_shortfall(
    milestone_id: str, body: ShortfallAnswer, auth: MilestoneAccessDep, session: SessionDep
) -> AgreementRead:
    access, m = auth
    access.require(Action.accept_shortfall)
    cofunding.answer_shrink(session, m, access.actor, body.accept, body.note, utcnow())
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
    return {"outcome": settlement.handle_webhook(session, event, utcnow(), gateway(request))}
