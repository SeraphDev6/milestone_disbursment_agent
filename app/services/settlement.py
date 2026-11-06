"""Escrow settlement: one PayPal authorization hold per tranche, captured on release, then paid to the payee.

Money rules enforced here:
- A tranche is funded by an AUTHORIZE order the payer approves; funds stay on the payer's account.
- Payout route is fixed when the order is created: `direct` when the payee org connected PayPal (the order
  names their merchant id; the platform never holds the money), else `platform` (the platform account is the
  payee and pays the payee's email with PayPal Payouts).
- Release captures the hold. Only from `verified` (or a retry). With no dispute window a direct capture pays
  the payee at once; otherwise the tranche is `held` until the window closes, then paid out (referenced payout
  of a delayed-disbursement capture, or Payouts). The payer or a reviewer can contest while it's held; a
  contest ends with a release (payer withdraws it, or a reviewer decides) or a refund (payee accepts it, or a
  reviewer decides). PayPal pays a delayed capture out by itself 28 days after capture.
- Every capture carries an idempotency key `{milestone_id}-{attempt}`. An ambiguous failure
  (timeout, 5xx) keeps the attempt open and is retried with the same key; a definitive failure
  closes it and the next release opens attempt + 1. PayPal refuses a second capture of a fully
  captured authorization, so a new key can't pay twice either.
- Holds are honored for 3 days and valid for 29. Release after day 3 reauthorizes once first;
  past day 29 the hold is gone and the payer must fund again.
- Money is tracked per capture, not per tranche: every Settlement carries the amount it moves and every
  Disbursement points at the capture whose money it pays out or refunds. With `partial_release` on the agreement,
  a verdict that meets some criteria can release the share of the tranche those criteria account for by weight
  (`final_capture=false`, so the hold keeps the remainder); the tranche returns to `funded` with the share recorded
  and settles with a final capture once everything is met. A partial share reaches the payee when it is captured
  (direct route) or by Payouts at once (platform route); the dispute window, contests and refunds apply to the
  final capture. Reauthorizing a partially captured hold is modelled here but unverified against PayPal (#38).
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db import as_utc, record_audit, utcnow
from app.domain.criteria import share_amount, weighted_share
from app.integrations.paypal import CreatedOrder, PayPalError, PayPalGateway
from app.models import (
    AgreementStatus,
    AuditEvent,
    DeliverableRelease,
    Disbursement,
    DisbursementKind,
    LedgerEntry,
    Milestone,
    MilestoneStatus,
    Party,
    PayoutRoute,
    Role,
    Settlement,
    SettlementMode,
    SettlementStatus,
    WebhookEvent,
)
from app.services import ServiceError, handover, payee_accounts
from app.services.notifications import notify_side, tranche_subject
from app.services.paylinks import pay_links_for

HONOR_PERIOD = timedelta(days=3)
AUTH_VALIDITY = timedelta(days=29)
PAYPAL_AUTO_DISBURSE = timedelta(days=28)  # PayPal pays a delayed-disbursement capture out by itself after this
# A contested platform-route tranche (money in the platform account) is paid to the payee after the same period
# unless someone resolved the contest, so both routes end the same way. Direct-route contests end when PayPal pays.
CONTEST_TIMEOUT = PAYPAL_AUTO_DISBURSE

AFTER_CAPTURE = {
    MilestoneStatus.held,
    MilestoneStatus.contested,
    MilestoneStatus.settled,
    MilestoneStatus.refunded,
    MilestoneStatus.payout_failed,
}
NO_LIVE_HOLD_STATES = {MilestoneStatus.settling, *AFTER_CAPTURE}
RELEASABLE = {MilestoneStatus.verified, MilestoneStatus.settlement_failed}
PARTIAL_FROM = {MilestoneStatus.needs_review, MilestoneStatus.disputed}  # a verdict with some criteria met stands
OPEN = {SettlementStatus.submitted, SettlementStatus.unclaimed}  # disbursement outcome not final yet


# --- helpers ---------------------------------------------------------------------------


def transition(session: Session, m: Milestone, to: MilestoneStatus, actor: str, action: str, **detail) -> None:
    previous = m.status
    m.status = to
    record_audit(
        session,
        m.agreement_id,
        actor,
        action,
        milestone_id=m.id,
        from_status=previous,
        to_status=m.status,
        detail=detail,
    )


def _note(session: Session, m: Milestone, actor: str, action: str, **detail) -> None:
    record_audit(session, m.agreement_id, actor, action, milestone_id=m.id, detail=detail)


def _ledger(session: Session, m: Milestone, kind: str, paypal_ref: str, amount: Decimal) -> None:
    session.add(
        LedgerEntry(agreement_id=m.agreement_id, milestone_id=m.id, kind=kind, amount=amount, paypal_ref=paypal_ref)
    )


def _captures(m: Milestone) -> list[Settlement]:
    return [
        s
        for s in m.settlements
        if s.mode == SettlementMode.escrow_release and s.status == SettlementStatus.succeeded and s.paypal_ref
    ]


def released(m: Milestone) -> Decimal:
    """Captured from the payer's hold so far (partial shares and the final capture)."""
    return sum((s.amount for s in _captures(m)), start=Decimal("0.00"))


def refunded(m: Milestone) -> Decimal:
    return sum(
        (
            d.amount
            for d in m.disbursements
            if d.kind == DisbursementKind.refund and d.status == SettlementStatus.succeeded
        ),
        start=Decimal("0.00"),
    )


def remaining(m: Milestone) -> Decimal:
    """What the hold still has to cover: the tranche less what partial releases captured."""
    return m.amount - released(m)


def paid_criteria(m: Milestone) -> set[str]:
    """Criteria whose share a completed capture paid for."""
    return {cid for s in _captures(m) for cid in s.criterion_ids}


def _payee(m: Milestone) -> Party:
    return next(p for p in m.agreement.parties if p.role == Role.payee)


def _capture(m: Milestone) -> Settlement:
    s = next((s for s in reversed(m.settlements) if s.status == SettlementStatus.succeeded and s.paypal_ref), None)
    if s is None:
        raise ServiceError("tranche has no completed capture")
    return s


def _paypal_failure(e: PayPalError) -> ServiceError:
    suffix = f" (PayPal debug id {e.debug_id})" if e.debug_id else ""
    return ServiceError(f"{e}{suffix}", 502)


def _mark_funded(session: Session, m: Milestone, authorization_id: str, actor: str, now: datetime) -> bool:
    if m.paypal_authorization_id is not None:
        return False  # already funded (return URL and webhook race; first one wins)
    m.paypal_authorization_id = authorization_id
    m.funded_at = now
    m.reauthorized_at = None
    _ledger(session, m, "authorized", authorization_id, remaining(m))
    if m.status == MilestoneStatus.pending:
        transition(session, m, MilestoneStatus.funded, actor, "funded", authorization_id=authorization_id)
    else:
        # Funded while overdue, or re-funded after an expired hold; verification state is kept.
        _note(session, m, actor, "refunded_hold", authorization_id=authorization_id)
    return True


def _drop_hold(session: Session, m: Milestone, kind: str, actor: str) -> None:
    ref = m.paypal_authorization_id or "unknown"
    _ledger(session, m, kind, ref, remaining(m))
    m.paypal_authorization_id = None
    m.funded_at = None
    m.reauthorized_at = None
    m.paypal_order_id = None
    m.payout_route = None
    m.payee_merchant_id = None
    detail = {"authorization_id": ref}
    if released(m):
        detail["released"] = str(released(m))  # partial shares already paid stay paid; the rest needs a new hold
    if m.status == MilestoneStatus.funded:
        transition(session, m, MilestoneStatus.pending, actor, f"hold_{kind}", **detail)
    else:
        _note(session, m, actor, f"hold_{kind}", **detail)


# --- funding ---------------------------------------------------------------------------


def _route(
    session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime
) -> tuple[PayoutRoute, str | None]:
    """Direct to the payee's connected PayPal when it's ready; otherwise through the platform with Payouts."""
    payee = _payee(m)
    account = payee_accounts.account_for(session, payee.org_id)
    if account is not None and not account.ready and account.revoked_at is None:
        try:
            payee_accounts.refresh(session, gw, account, actor, now)
        except ServiceError as e:
            # The payee started connecting PayPal, so don't quietly move their money through the platform
            # because PayPal couldn't be reached: refuse and let the caller retry.
            raise ServiceError(
                f"couldn't confirm the payee's PayPal account; try again shortly ({e.message})", 502
            ) from e
    if account is not None and account.ready:
        return PayoutRoute.direct, account.merchant_id
    if not payee.paypal_email:
        raise ServiceError("the payee hasn't connected PayPal or given a PayPal email, so it can't be paid yet")
    if not getattr(gw, "platform_route_allowed", True):
        raise ServiceError("the payee must connect PayPal before this tranche can be funded (direct payments only)")
    return PayoutRoute.platform, None


def start_funding(
    session: Session, gw: PayPalGateway, m: Milestone, actor: str, base_url: str, now: datetime | None = None
) -> CreatedOrder:
    a = m.agreement
    if a.status != AgreementStatus.active:
        raise ServiceError(f"agreement is {a.status}; both parties must confirm criteria before funding")
    if a.settlement_mode != SettlementMode.escrow_release:
        raise ServiceError("this agreement settles by invoice; there is nothing to fund")
    if m.paypal_authorization_id is not None:
        raise ServiceError("tranche already has a live hold")
    if m.status in NO_LIVE_HOLD_STATES:
        raise ServiceError(f"tranche is {m.status}")
    route, merchant_id = _route(session, gw, m, actor, now or datetime.now(UTC))
    try:
        order = gw.create_authorize_order(
            reference_id=m.id,
            description=f"{a.title}: {m.title}",
            amount=remaining(m),  # after an expired hold, only what partial releases haven't paid yet
            currency=a.currency,
            payee_merchant_id=merchant_id,
            return_url=f"{base_url}/paypal/return",
            cancel_url=f"{base_url}/paypal/cancel",
            request_id=f"{m.id}-order-{uuid.uuid4().hex[:12]}",
        )
    except PayPalError as e:
        raise _paypal_failure(e) from e
    m.paypal_order_id = order.order_id
    m.payout_route, m.payee_merchant_id = route, merchant_id
    _note(session, m, actor, "funding_started", order_id=order.order_id, payout_route=route)
    session.commit()
    return order


def cancel_funding(session: Session, order_id: str, actor: str) -> Milestone | None:
    """The payer backed out of PayPal approval: forget the order so the tranche stops waiting on it."""
    m = session.exec(select(Milestone).where(Milestone.paypal_order_id == order_id)).first()
    if m is None or m.paypal_authorization_id is not None:
        return None
    _note(session, m, actor, "funding_cancelled", order_id=order_id)
    m.paypal_order_id = None
    session.commit()
    return m


def complete_funding(session: Session, gw: PayPalGateway, order_id: str, actor: str, now: datetime) -> Milestone:
    """Called when the payer returns from PayPal approval. Idempotent."""
    m = session.exec(select(Milestone).where(Milestone.paypal_order_id == order_id)).first()
    if m is None:
        raise ServiceError("unknown or superseded funding order", 404)
    if m.paypal_authorization_id is not None:
        return m
    try:
        auth = gw.authorize_order(order_id, request_id=f"{order_id}-authorize")
    except PayPalError as e:
        raise _paypal_failure(e) from e
    if auth.status not in ("CREATED", "PENDING"):
        _note(session, m, actor, "authorization_refused", order_id=order_id, status=auth.status)
        session.commit()
        raise ServiceError(f"PayPal did not place the hold (status {auth.status})", 402)
    _mark_funded(session, m, auth.authorization_id, actor, now)
    session.commit()
    return m


def void(session: Session, gw: PayPalGateway, m: Milestone, actor: str) -> None:
    if m.status not in (MilestoneStatus.funded, MilestoneStatus.overdue) or m.paypal_authorization_id is None:
        raise ServiceError("only a funded tranche with no evidence submitted can be voided")
    try:
        gw.void_authorization(
            m.paypal_authorization_id,
            payee_merchant_id=m.payee_merchant_id,
            request_id=f"{m.paypal_authorization_id}-void",
        )
    except PayPalError as e:
        raise _paypal_failure(e) from e
    _drop_hold(session, m, "voided", actor)
    session.commit()


def expire_stale_holds(session: Session, now: datetime, actor: str = "system:expiry") -> int:
    """Drop holds past PayPal's 29-day validity. Run daily (cron) and before every release."""
    count = 0
    for m in session.exec(select(Milestone).where(col(Milestone.paypal_authorization_id).is_not(None))):
        if m.status not in NO_LIVE_HOLD_STATES and m.funded_at and now - as_utc(m.funded_at) > AUTH_VALIDITY:
            _drop_hold(session, m, "expired", actor)
            count += 1
    session.commit()
    return count


# --- release ---------------------------------------------------------------------------


def _ensure_live_hold(session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime) -> None:
    if m.paypal_authorization_id is None or m.funded_at is None:
        raise ServiceError("tranche isn't funded; the payer must fund it first")
    if now - as_utc(m.funded_at) > AUTH_VALIDITY:
        _drop_hold(session, m, "expired", actor)
        session.commit()
        raise ServiceError("the PayPal hold expired after 29 days; the payer must fund this tranche again")
    if m.reauthorized_at is None and now - as_utc(m.funded_at) > HONOR_PERIOD:
        old = m.paypal_authorization_id
        try:
            auth = gw.reauthorize(
                old,
                amount=remaining(m),
                currency=m.agreement.currency,
                payee_merchant_id=m.payee_merchant_id,
                request_id=f"{old}-reauth",
            )
        except PayPalError as e:
            raise _paypal_failure(e) from e
        m.paypal_authorization_id = auth.authorization_id
        m.reauthorized_at = now
        _note(session, m, actor, "reauthorized", old_authorization_id=old, authorization_id=auth.authorization_id)
        session.commit()


def _reveal(session: Session, m: Milestone, actor: str, now: datetime) -> None:
    """deliverable_release=on_capture: the payer's money is captured, so the payer side gets the full evidence.
    Records the commitment (evidence ids and hashes) the payer can check downloads against. Never undone."""
    a = m.agreement
    if a.deliverable_release != DeliverableRelease.on_capture or m.revealed_at is not None:
        return
    m.revealed_at = now
    handover.queue(session, m, actor)  # code in a contractor's repo goes over by the handover loop
    commitment = [
        {"evidence_id": e.id, "pinned_ref": e.pinned_ref, "file_sha256": e.file.sha256 if e.file else None}
        for e in m.evidence
    ]
    window_ends = m.release_due_at if m.release_due_at and as_utc(m.release_due_at) > now else None
    _note(
        session,
        m,
        actor,
        "deliverable_released",
        evidence=commitment,
        dispute_window_ends=window_ends.isoformat() if window_ends else None,
    )
    window = (
        f" You can contest it until {as_utc(window_ends):%B %d, %Y %H:%M} UTC; after that the payee is paid."
        if window_ends
        else ""
    )
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Work delivered"),
        f"'{m.title}' is paid for, so the payee's work is now open to you: {len(commitment)} evidence item(s), each "
        f"checked against the hash that was verified when you download it.{window}",
        tone="review",
    )


def _paid_at_capture(m: Milestone) -> bool:
    return m.payout_route == PayoutRoute.direct and m.agreement.dispute_window_days == 0


def _partial_captured(session: Session, s: Settlement, actor: str, now: datetime) -> None:
    """A non-final capture completed: the share is paid (direct route) or due by Payouts (platform route), and the
    tranche goes back to `funded` with the remainder still on hold for the criteria not yet met."""
    m = s.milestone
    ref = s.paypal_ref or "unknown"
    detail = {
        "capture_id": ref,
        "attempt": s.attempt,
        "amount": str(s.amount),
        "criteria": s.criterion_ids,
        "share": weighted_share(m.criteria, paid_criteria(m)),
        "released": str(released(m)),
        "remaining": str(remaining(m)),
    }
    if m.payout_route == PayoutRoute.direct:
        _ledger(session, m, "disbursed", ref, s.amount)  # not delayed: the connected payee has it at once
        detail["paid"] = True
    else:
        session.info.setdefault("partial_payout_due", set()).add(s.id)  # pay it with Payouts (pay_due_partials)
    transition(session, m, MilestoneStatus.funded, actor, "partially_released", **detail)
    a = m.agreement
    notify_side(
        session,
        m,
        Role.payee,
        tranche_subject(m, "Share released"),
        f"{a.currency} {s.amount} of '{m.title}' was released for {', '.join(s.criterion_ids)}. "
        f"{a.currency} {remaining(m)} stays on hold until the remaining criteria are met; the tranche's page lists "
        "what's missing.",
        tone="review",
    )


def _apply_capture_status(session: Session, s: Settlement, status: str, actor: str, now: datetime) -> None:
    m = s.milestone
    if status == "COMPLETED":
        if s.status == SettlementStatus.succeeded:
            return
        s.status = SettlementStatus.succeeded
        ref = s.paypal_ref or "unknown"
        _ledger(session, m, "captured", ref, s.amount)
        if not s.final:
            _partial_captured(session, s, actor, now)
            return
        m.captured_at = now
        if _paid_at_capture(m):
            _ledger(session, m, "disbursed", ref, s.amount)
            transition(session, m, MilestoneStatus.settled, actor, "settled", capture_id=ref, attempt=s.attempt)
            _reveal(session, m, actor, now)
            return
        m.release_due_at = now + timedelta(days=m.agreement.dispute_window_days)
        transition(
            session,
            m,
            MilestoneStatus.held,
            actor,
            "captured",
            capture_id=ref,
            attempt=s.attempt,
            payout_route=m.payout_route,
            release_due_at=m.release_due_at.isoformat(),
        )
        _reveal(session, m, actor, now)
    elif status == "PENDING":
        _note(session, m, actor, "capture_pending", capture_id=s.paypal_ref, attempt=s.attempt)
    else:
        if s.status == SettlementStatus.failed:
            return
        s.status = SettlementStatus.failed
        s.error = f"capture {status}"
        _capture_failed(session, s, actor, status=status)


def _capture_failed(session: Session, s: Settlement, actor: str, **detail) -> None:
    """A definitive capture failure. A whole-tranche release lands in `settlement_failed` (release retries); a
    partial share goes back to review, where the verdict still stands and the share can be released again."""
    m = s.milestone
    if s.final:
        transition(
            session, m, MilestoneStatus.settlement_failed, actor, "settlement_failed", attempt=s.attempt, **detail
        )
        return
    transition(
        session,
        m,
        MilestoneStatus.needs_review,
        actor,
        "partial_release_failed",
        attempt=s.attempt,
        amount=str(s.amount),
        criteria=s.criterion_ids,
        **detail,
    )
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Share not released"),
        f"PayPal didn't capture {m.agreement.currency} {s.amount} of '{m.title}' for {', '.join(s.criterion_ids)} "
        f"({s.error}). The verdict stands; release the met share again from the tranche once the cause is fixed.",
        tone="alert",
    )


def _new_settlement(m: Milestone, amount: Decimal, final: bool, criterion_ids: list[str]) -> Settlement:
    last = m.settlements[-1] if m.settlements else None
    attempt = last.attempt + 1 if last else 1
    return Settlement(
        milestone=m,
        attempt=attempt,
        mode=SettlementMode.escrow_release,
        idempotency_key=f"{m.id}-{attempt}",
        status=SettlementStatus.submitted,
        amount=amount,
        final=final,
        criterion_ids=criterion_ids,
    )


def _start_partial(
    session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime, met: set[str]
) -> Settlement:
    """Open a capture for the share the newly met criteria add (or the final capture, when they complete the set)."""
    a = m.agreement
    if not a.partial_release:
        raise ServiceError(
            "this agreement pays each tranche in full once every criterion is met; partial release is off", 409
        )
    if m.status not in PARTIAL_FROM:
        raise ServiceError(f"a tranche that is {m.status} can't release a share")
    known = {c.id for c in m.criteria}
    if unknown := sorted(met - known):
        raise ServiceError(f"unknown criteria: {', '.join(unknown)}", 422)
    paid = paid_criteria(m)
    new = sorted(met - paid)
    if not new:
        raise ServiceError("every one of those criteria has been paid for already", 409)
    _ensure_live_hold(session, gw, m, actor, now)
    amount = share_amount(m.criteria, paid | met, m.amount) - released(m)
    if amount <= 0:
        raise ServiceError("the share those criteria add rounds to nothing", 409)
    final = known <= paid | met
    s = _new_settlement(m, amount, final, new)
    session.add(s)
    transition(
        session,
        m,
        MilestoneStatus.settling,
        actor,
        "release_requested" if final else "partial_release_requested",
        attempt=s.attempt,
        amount=str(amount),
        criteria=new,
        share=weighted_share(m.criteria, paid | met),
    )
    session.commit()  # record intent before money moves
    return s


def release(
    session: Session,
    gw: PayPalGateway,
    m: Milestone,
    actor: str,
    now: datetime,
    criterion_ids: Iterable[str] | None = None,
) -> Settlement:
    """Settle a verified tranche: capture its hold (escrow) or send a PayPal invoice (invoice mode). With
    `criterion_ids` (partial release), capture only the share those met criteria add and leave the rest on hold."""
    if m.agreement.settlement_mode == SettlementMode.invoice_on_verify:
        if criterion_ids is not None:
            raise ServiceError("partial release needs escrow; this agreement settles by invoice", 409)
        return _release_by_invoice(session, gw, m, actor, now)

    last = m.settlements[-1] if m.settlements else None
    if m.status == MilestoneStatus.settling and last and last.status == SettlementStatus.submitted:
        if last.paypal_ref:
            raise ServiceError("capture is pending at PayPal; its webhook or the hourly follow-up settles it", 409)
        s = last  # previous attempt's outcome unknown: resume with the same idempotency key
    elif criterion_ids is not None:
        s = _start_partial(session, gw, m, actor, now, set(criterion_ids))
    elif m.status in RELEASABLE:
        _ensure_live_hold(session, gw, m, actor, now)
        unpaid = sorted({c.id for c in m.criteria} - paid_criteria(m))
        s = _new_settlement(m, remaining(m), True, unpaid)
        session.add(s)
        transition(
            session, m, MilestoneStatus.settling, actor, "release_requested", attempt=s.attempt, amount=str(s.amount)
        )
        session.commit()  # record intent before money moves
    else:
        raise ServiceError(f"a tranche that is {m.status} can't be released")

    try:
        capture = gw.capture_authorization(
            m.paypal_authorization_id,
            amount=s.amount,
            currency=m.agreement.currency,
            payee_merchant_id=m.payee_merchant_id,
            # A partial share is paid at once; the final capture waits out the dispute window when there is one.
            delayed=s.final and m.payout_route == PayoutRoute.direct and not _paid_at_capture(m),
            request_id=s.idempotency_key,
            final=s.final,
        )
    except PayPalError as e:
        s.error = str(e)[:500]
        if e.ambiguous:
            _note(session, m, actor, "capture_outcome_unknown", attempt=s.attempt, error=s.error)
            session.commit()
            raise ServiceError(
                "PayPal didn't confirm the capture; release again to resume with the same idempotency key", 502
            ) from e
        s.status = SettlementStatus.failed
        _capture_failed(session, s, actor, error=s.error)
        session.commit()
        raise _paypal_failure(e) from e

    s.paypal_ref = capture.capture_id
    _apply_capture_status(session, s, capture.status, actor, now)
    session.commit()
    pay_due_partials(session, gw, actor, now)
    if m.status == MilestoneStatus.held and m.release_due_at is not None and as_utc(m.release_due_at) <= now:
        try:
            pay_out(session, gw, m, actor, now)  # no dispute window: pay the payee now
        except ServiceError:
            pass  # recorded on the tranche (payout_failed or outcome unknown); release/pay-out retries
    return s


# --- after capture: pay the payee, contest, refund ---------------------------------------


def _open(m: Milestone, kinds: set[DisbursementKind], capture: Settlement | None = None) -> Disbursement | None:
    """The latest disbursement of these kinds on this capture (any capture when None), if its outcome isn't final."""
    last = next(
        (
            d
            for d in reversed(m.disbursements)
            if d.kind in kinds and (capture is None or d.settlement_id == capture.id)
        ),
        None,
    )
    return last if last is not None and last.status in OPEN else None


def _partial(d: Disbursement) -> bool:
    return d.settlement is not None and not d.settlement.final


def _payout_kind(m: Milestone) -> DisbursementKind:
    return DisbursementKind.referenced_payout if m.payout_route == PayoutRoute.direct else DisbursementKind.payout


def _apply_payout_status(session: Session, d: Disbursement, status: str, actor: str) -> None:
    m = d.milestone
    if d.status in (SettlementStatus.succeeded, SettlementStatus.failed):
        return  # final
    if status == "SUCCESS":
        d.status = SettlementStatus.succeeded
        _ledger(session, m, "disbursed", d.paypal_ref or "unknown", d.amount)
        if _partial(d):  # a share of a tranche still on hold for the rest: the tranche stays funded
            _note(session, m, actor, "share_paid_out", payout_ref=d.paypal_ref, attempt=d.attempt, amount=str(d.amount))
            return
        transition(session, m, MilestoneStatus.settled, actor, "settled", payout_ref=d.paypal_ref, attempt=d.attempt)
    elif status == "UNCLAIMED":
        if d.status != SettlementStatus.unclaimed:
            d.status = SettlementStatus.unclaimed
            _note(
                session,
                m,
                actor,
                "payout_unclaimed",
                payout_ref=d.paypal_ref,
                detail="The payee's email has no PayPal account yet; PayPal holds the payout for 30 days for them "
                "to claim it, then returns it.",
            )
            email = _payee(m).paypal_email or "the PayPal email on file"
            notify_side(
                session,
                m,
                Role.payee,
                tranche_subject(m, "Claim your payment"),
                f"{m.agreement.currency} {d.amount} for '{m.title}' was sent to {email}, but that address has no "
                "PayPal account yet. Open the PayPal email to claim it or create a PayPal account with that "
                "address. PayPal returns the money after 30 days if it isn't claimed.",
                tone="review",
            )
    elif status == "PENDING":
        _note(session, m, actor, "payout_pending", payout_ref=d.paypal_ref, attempt=d.attempt)
    else:
        d.status = SettlementStatus.failed
        d.error = d.error or f"payout {status}"
        if _partial(d):
            _note(
                session, m, actor, "share_payout_failed", payout_ref=d.paypal_ref, status=status, amount=str(d.amount)
            )
        else:
            transition(
                session,
                m,
                MilestoneStatus.payout_failed,
                actor,
                "payout_failed",
                payout_ref=d.paypal_ref,
                status=status,
            )
        notify_side(
            session,
            m,
            Role.payer,
            tranche_subject(m, "Payout failed"),
            f"Paying the payee {m.agreement.currency} {d.amount} for '{m.title}' failed at PayPal ({d.error}). The "
            "money was captured and is not lost; retry the payout from the tranche once the cause is fixed (for "
            "example the payee's PayPal email).",
            tone="alert",
        )


def pay_out(
    session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime, capture: Settlement | None = None
) -> Disbursement:
    """Pay a captured tranche to the payee: when the dispute window closes, when the payer waives it, after a
    contest is resolved for the payee, or to retry a failed payout. With `capture` (or when the tranche's latest
    capture is a partial share), pays that capture's amount. Idempotent per attempt."""
    kinds = {DisbursementKind.referenced_payout, DisbursementKind.payout}
    capture = capture or _capture(m)
    partial = not capture.final
    if not partial and _open(m, {DisbursementKind.refund}, capture) is not None:
        raise ServiceError("a refund to the payer is pending at PayPal; the payee can't be paid as well", 409)
    d = _open(m, kinds, capture)
    if d is not None and d.paypal_ref:
        if d.kind == DisbursementKind.payout:  # Payouts settle asynchronously: ask PayPal where it is
            try:
                result = gw.payout_status(d.paypal_ref)
            except PayPalError as e:
                raise _paypal_failure(e) from e
            _apply_payout_status(session, d, result.status, actor)
            session.commit()
        return d
    if d is None:
        if partial:
            if any(
                x.kind in kinds and x.settlement_id == capture.id and x.status == SettlementStatus.succeeded
                for x in m.disbursements
            ):
                raise ServiceError("that share has been paid out already", 409)
        elif m.status not in (MilestoneStatus.held, MilestoneStatus.payout_failed):
            raise ServiceError(f"a tranche that is {m.status} can't be paid out")
        attempt = sum(1 for x in m.disbursements if x.kind in kinds) + 1
        d = Disbursement(
            milestone=m,
            settlement=capture,
            kind=_payout_kind(m),
            attempt=attempt,
            idempotency_key=f"{m.id}-payout-{attempt}",
            status=SettlementStatus.submitted,
            amount=capture.amount,
        )
        session.add(d)
        if m.status == MilestoneStatus.payout_failed:
            transition(session, m, MilestoneStatus.held, actor, "payout_retried", attempt=attempt)
        _note(
            session,
            m,
            actor,
            "payout_requested",
            kind=d.kind,
            attempt=attempt,
            amount=str(d.amount),
            capture_id=capture.paypal_ref,
        )
        session.commit()  # record intent before money moves

    try:
        if d.kind == DisbursementKind.referenced_payout:
            result = gw.release_delayed(capture.paypal_ref, request_id=d.idempotency_key)
        else:
            what = (
                f"share for {', '.join(capture.criterion_ids)}"
                if partial
                else f"met all {len(m.criteria)} acceptance criteria"
            )
            result = gw.send_payout(
                email=_payee(m).paypal_email,
                amount=d.amount,
                currency=m.agreement.currency,
                note=f"{m.agreement.title}: '{m.title}' {what}.",
                request_id=d.idempotency_key,
            )
    except PayPalError as e:
        d.error = str(e)[:500]
        if "PAYOUT_ALREADY_COMPLETED" in str(e):  # PayPal already paid it (e.g. its own 28-day release)
            _apply_payout_status(session, d, "SUCCESS", actor)
            session.commit()
            return d
        if e.ambiguous or e.duplicate:
            _note(session, m, actor, "payout_outcome_unknown", attempt=d.attempt, error=d.error)
            session.commit()
            if e.duplicate:
                return d  # PayPal already has it; its webhook (matched by our key) settles the tranche
            raise ServiceError(
                "PayPal didn't confirm the payout; pay out again to resume with the same key", 502
            ) from e
        _apply_payout_status(session, d, "FAILED", actor)
        session.commit()
        raise _paypal_failure(e) from e
    d.paypal_ref = result.ref
    _apply_payout_status(session, d, result.status, actor)
    session.commit()
    return d


def pay_due_partials(session: Session, gw: PayPalGateway, actor: str, now: datetime) -> int:
    """Platform-route shares captured since the last call (`_partial_captured` queued them): send each by Payouts.
    A failure is recorded on the tranche; the payouts job and the pay-out endpoint retry it."""
    paid = 0
    for sid in session.info.pop("partial_payout_due", set()):
        s = session.get(Settlement, sid)
        if s is None:
            continue
        try:
            pay_out(session, gw, s.milestone, actor, now, capture=s)
            paid += 1
        except ServiceError:
            pass
    return paid


def follow_partials(session: Session, gw: PayPalGateway, now: datetime, actor: str) -> int:
    """Shares whose Payouts outcome isn't final yet (PENDING, UNCLAIMED): ask PayPal where they stand."""
    count = 0
    waiting = session.exec(
        select(Disbursement).where(
            Disbursement.kind == DisbursementKind.payout, col(Disbursement.status).in_(tuple(OPEN))
        )
    ).all()
    for d in waiting:
        if d.settlement is None or d.settlement.final:
            continue  # the tranche-level loop in disburse_due follows these
        try:
            pay_out(session, gw, d.milestone, actor, now, capture=d.settlement)
            count += 1
        except ServiceError:
            pass
    return count


def contest(session: Session, m: Milestone, actor: str, reason: str, now: datetime) -> None:
    """Payer or reviewer objects while the tranche is held: the payout waits until the contest is resolved."""
    if m.status != MilestoneStatus.held:
        raise ServiceError(f"tranche is {m.status}; only a tranche held in its dispute window can be contested")
    if m.release_due_at is None or as_utc(m.release_due_at) <= now:
        raise ServiceError("the dispute window has closed")
    if _open(m, {DisbursementKind.referenced_payout, DisbursementKind.payout}, _capture(m)) is not None:
        raise ServiceError("the payout is already under way")
    detail = {"reason": reason}
    if m.payout_route == PayoutRoute.direct and m.captured_at is not None:
        detail["paypal_auto_payout_at"] = (as_utc(m.captured_at) + PAYPAL_AUTO_DISBURSE).isoformat()
    transition(session, m, MilestoneStatus.contested, actor, "contested", **detail)
    session.commit()


def _refund(session: Session, gw: PayPalGateway, m: Milestone, actor: str) -> Disbursement:
    """Return the final capture to the payer. Shares paid before it were accepted as paid; they stay with the payee."""
    capture = _capture(m)
    d = _open(m, {DisbursementKind.refund}, capture)
    if d is not None and d.paypal_ref:
        raise ServiceError("the refund is pending at PayPal; its webhook will finish it", 409)
    if d is None:
        attempt = sum(1 for x in m.disbursements if x.kind == DisbursementKind.refund) + 1
        d = Disbursement(
            milestone=m,
            settlement=capture,
            kind=DisbursementKind.refund,
            attempt=attempt,
            idempotency_key=f"{m.id}-refund-{attempt}",
            status=SettlementStatus.submitted,
            amount=capture.amount,
        )
        session.add(d)
        session.commit()
    try:
        refund = gw.refund_capture(
            capture.paypal_ref,
            amount=d.amount,
            currency=m.agreement.currency,
            payee_merchant_id=m.payee_merchant_id,
            request_id=d.idempotency_key,
        )
    except PayPalError as e:
        d.error = str(e)[:500]
        if not e.ambiguous:
            d.status = SettlementStatus.failed
        session.commit()
        raise _paypal_failure(e) from e
    d.paypal_ref = refund.refund_id
    _apply_refund_status(session, d, refund.status, actor)
    session.commit()
    return d


def _apply_refund_status(session: Session, d: Disbursement, status: str, actor: str) -> None:
    m = d.milestone
    if d.status != SettlementStatus.submitted:
        return
    if status == "COMPLETED":
        d.status = SettlementStatus.succeeded
        _ledger(session, m, "refunded", d.paypal_ref or "unknown", d.amount)
        transition(
            session, m, MilestoneStatus.refunded, actor, "refunded", refund_id=d.paypal_ref, amount=str(d.amount)
        )
    elif status in ("FAILED", "CANCELLED"):
        d.status = SettlementStatus.failed
        _note(session, m, actor, "refund_failed", refund_id=d.paypal_ref, status=status)
    else:
        _note(session, m, actor, "refund_pending", refund_id=d.paypal_ref)


def resolve(
    session: Session,
    gw: PayPalGateway,
    m: Milestone,
    actor: str,
    decision: Literal["release", "refund"],
    note: str,
    now: datetime,
) -> Disbursement:
    """End a contest: pay the payee, or refund the payer. Who may choose which is decided by the caller's rights."""
    if m.status != MilestoneStatus.contested:
        raise ServiceError(f"tranche is {m.status}; only a contested tranche can be resolved")
    pending_refund = _open(m, {DisbursementKind.refund}, _capture(m))
    if decision == "refund":
        if pending_refund is not None and pending_refund.paypal_ref:
            raise ServiceError("a refund to the payer is pending at PayPal; its webhook will finish it", 409)
        _note(session, m, actor, "contest_resolved", decision=decision, note=note)
        return _refund(session, gw, m, actor)
    if pending_refund is not None:
        # Both sides conceding at once (or a refund whose outcome is still unknown): never pay the payee as well.
        raise ServiceError("a refund to the payer is under way; the payee can't be paid as well", 409)
    transition(session, m, MilestoneStatus.held, actor, "contest_resolved", decision=decision, note=note)
    session.commit()
    return pay_out(session, gw, m, actor, now)


def follow_pending(session: Session, gw: PayPalGateway, now: datetime, actor: str = "system:payouts") -> dict[str, int]:
    """Catch up on what PayPal finishes asynchronously, by asking PayPal instead of waiting for a webhook:
    escrow captures left PENDING, pay-now payments left PENDING, and platform invoices paid or cancelled in
    PayPal. Webhooks normally get there first; this keeps a missed or late webhook from stranding a tranche.
    Applies the same state changes as the webhook handlers, so the two can't disagree."""
    counts = {"captures_followed": 0, "payments_followed": 0, "invoices_followed": 0}
    waiting = session.exec(
        select(Settlement).where(
            Settlement.status == SettlementStatus.submitted, col(Settlement.paypal_ref).is_not(None)
        )
    ).all()
    for s in waiting:
        m = s.milestone
        try:
            if s.mode == SettlementMode.escrow_release:
                if m.status != MilestoneStatus.settling:
                    continue
                capture = gw.capture_status(s.paypal_ref)
                if capture.status == "PENDING":
                    continue
                _apply_capture_status(session, s, capture.status, actor, now)
                counts["captures_followed"] += 1
            elif m.payout_route == PayoutRoute.direct:  # pay-now link: paypal_ref is the latest order
                capture = gw.order_capture(s.paypal_ref)
                if capture is None or capture.status != "COMPLETED":
                    continue  # not paid yet; after a decline the payer pays again from the link
                _apply_payment_status(session, s, capture.capture_id, capture.status, actor, now)
                counts["payments_followed"] += 1
            else:  # platform invoice
                status = gw.invoice_status(s.paypal_ref)
                if status == "OPEN":
                    continue
                _close_invoice(session, s, paid=status == "PAID", actor=actor, now=now)
                counts["invoices_followed"] += 1
            session.commit()
        except PayPalError:
            session.rollback()  # PayPal unreachable or the object is gone: try again next run
    session.info.pop("payout_due", None)  # a paid platform invoice leaves the tranche held and due now; paid below
    counts["shares_paid"] = pay_due_partials(session, gw, actor, now)  # platform-route shares whose capture completed
    return counts


def disburse_due(session: Session, gw: PayPalGateway, now: datetime, actor: str = "system:payouts") -> dict[str, int]:
    """Catch up on captures, payments and invoices PayPal finished without a webhook reaching us (follow_pending),
    then pay tranches whose dispute window closed, follow Payouts still in flight, and mirror PayPal's own 28-day
    release of contested direct captures. Run from the scheduler (hourly is plenty)."""
    counts = {"paid_out": 0, "checked": 0, "auto_released": 0, "errors": 0}
    counts.update(follow_pending(session, gw, now, actor))
    counts.update(poll_disputes(session, gw, now))  # before paying, so a dispute PayPal didn't announce pauses it
    counts["shares_followed"] = follow_partials(session, gw, now, actor)
    for m in session.exec(
        select(Milestone).where(col(Milestone.status).in_((MilestoneStatus.held, MilestoneStatus.contested)))
    ).all():
        try:
            if m.status == MilestoneStatus.contested:
                if not m.captured_at or now - as_utc(m.captured_at) <= PAYPAL_AUTO_DISBURSE:
                    continue
                if m.payout_route == PayoutRoute.direct:
                    _ledger(session, m, "disbursed", _capture(m).paypal_ref, _capture(m).amount)
                    transition(session, m, MilestoneStatus.settled, actor, "paypal_auto_released")
                    session.commit()
                    counts["auto_released"] += 1
                elif _open(m, {DisbursementKind.refund}, _capture(m)) is None:
                    # Platform route: nobody resolved the contest within the same period PayPal allows a direct
                    # capture, so it ends the same way, paid to the payee. A refund under way is left alone.
                    transition(
                        session,
                        m,
                        MilestoneStatus.held,
                        actor,
                        "contest_timed_out",
                        detail=f"unresolved for {CONTEST_TIMEOUT.days} days after capture; paying the payee",
                    )
                    session.commit()
                    pay_out(session, gw, m, actor, now)
                    counts["auto_released"] += 1
                continue
            open_payout = _open(m, {DisbursementKind.referenced_payout, DisbursementKind.payout}, _capture(m))
            if open_payout is not None:
                pay_out(session, gw, m, actor, now)
                counts["checked"] += 1
            elif m.release_due_at is not None and as_utc(m.release_due_at) <= now:
                pay_out(session, gw, m, actor, now)
                counts["paid_out"] += 1
        except ServiceError:
            counts["errors"] += 1
    return counts


# --- invoice mode ----------------------------------------------------------------------
#
# When a tranche verifies, the payer is asked to pay it:
# - connected payee (direct): a pay-now link, /pay/{settlement id}, which creates a fresh PayPal order with the
#   payee's merchant id as payee on each visit (links never go stale); the money goes straight to the payee.
# - otherwise (platform): a PayPal invoice from the platform account; when it's paid, the payee is paid with
#   PayPal Payouts. Invoicing as the payee needs PayPal's INVOICE_READ_WRITE partner feature, which isn't
#   self-serve (sandbox: FEATURES_UNAUTHORIZED).

INVOICE_TIMEOUT = timedelta(days=30)


def _party(m: Milestone, role: Role) -> Party:
    return next(p for p in m.agreement.parties if p.role == role)


def pay_path(s: Settlement, now: datetime | None = None) -> str | None:
    """Where the payer pays an open invoice-mode request, relative to the API: a link signed for the next
    `pay_link_valid_days` (see app.services.paylinks). None when there is nothing open to pay."""
    if s.mode != SettlementMode.invoice_on_verify or s.status != SettlementStatus.submitted:
        return None
    links = pay_links_for(Session.object_session(s)) if Session.object_session(s) is not None else None
    if links is None:
        raise ServiceError("pay links aren't configured on this session", 500)
    return links.path(s.id, now or utcnow())


def _release_by_invoice(session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime) -> Settlement:
    last = m.settlements[-1] if m.settlements else None
    if m.status == MilestoneStatus.settling and last and last.status == SettlementStatus.submitted:
        if m.payout_route == PayoutRoute.direct or last.paypal_ref:
            raise ServiceError(f"payment is requested; the payer pays at {pay_path(last)}", 409)
        s = last  # outcome of the previous invoice send is unknown: resume with the same idempotency key
    elif m.status in RELEASABLE:
        route, merchant_id = _route(session, gw, m, actor, now)
        attempt = last.attempt + 1 if last else 1
        s = Settlement(
            milestone=m,
            attempt=attempt,
            mode=SettlementMode.invoice_on_verify,
            idempotency_key=f"{m.id}-{attempt}",
            status=SettlementStatus.submitted,
            amount=m.amount,
            criterion_ids=[c.id for c in m.criteria if c.id],
        )
        session.add(s)
        m.payout_route, m.payee_merchant_id = route, merchant_id
        transition(
            session, m, MilestoneStatus.settling, actor, "invoice_requested", attempt=attempt, payout_route=route
        )
        session.commit()
        if route == PayoutRoute.direct:
            _ledger(session, m, "invoiced", f"request:{s.id}", s.amount)
            _note(session, m, actor, "payment_requested", settlement_id=s.id, pay_path=f"/pay/{s.id}")
            session.commit()
            _notify_payment_due(session, m, pay_path(s))
            return s
    else:
        raise ServiceError(f"a tranche that is {m.status} can't be invoiced")

    a = m.agreement
    payer_email = _party(m, Role.payer).paypal_email
    if not payer_email:
        raise ServiceError("the payer has no PayPal email to invoice")
    try:
        invoice = gw.create_and_send_invoice(
            recipient_email=payer_email,
            item_name=f"{a.title}: {m.title}",
            amount=m.amount,
            currency=a.currency,
            note=f"'{m.title}' met all {len(m.criteria)} acceptance criteria. Verdict and evidence are in the "
            "agreement's audit log. Paid to the payee once received.",
            reference=s.idempotency_key,
            request_id=s.idempotency_key,
        )
    except PayPalError as e:
        s.error = str(e)[:500]
        if e.ambiguous:
            _note(session, m, actor, "invoice_outcome_unknown", attempt=s.attempt, error=s.error)
            session.commit()
            raise ServiceError(
                "PayPal didn't confirm the invoice; release again to resume with the same key", 502
            ) from e
        s.status = SettlementStatus.failed
        transition(
            session, m, MilestoneStatus.settlement_failed, actor, "settlement_failed", attempt=s.attempt, error=s.error
        )
        session.commit()
        raise _paypal_failure(e) from e

    s.paypal_ref = invoice.invoice_id
    s.payer_url = invoice.payer_view_url
    _ledger(session, m, "invoiced", invoice.invoice_id, s.amount)
    _note(session, m, actor, "invoice_sent", invoice_id=invoice.invoice_id, payer_view_url=invoice.payer_view_url)
    session.commit()
    _notify_payment_due(session, m, pay_path(s))
    return s


def resend_pay_link(session: Session, m: Milestone, actor: str, now: datetime) -> Settlement:
    """A payer-side person asks for a fresh pay link: emailed to the payer side again, and returned."""
    last = m.settlements[-1] if m.settlements else None
    if m.status != MilestoneStatus.settling or last is None or pay_path(last, now) is None:
        raise ServiceError("no payment is open on this tranche", 409)
    if m.payout_route != PayoutRoute.direct:
        raise ServiceError("this tranche is paid through a PayPal invoice, which PayPal emailed to the payer", 409)
    _note(session, m, actor, "pay_link_resent", settlement_id=last.id)
    session.commit()
    _notify_payment_due(session, m, pay_path(last, now))
    return last


def _notify_payment_due(session: Session, m: Milestone, path: str | None) -> None:
    from app.services.notifications import notifier_for

    n = notifier_for(session)
    if n is None or path is None:
        return
    notify_side(
        session,
        m,
        Role.payer,
        tranche_subject(m, "Payment due"),
        f"'{m.title}' met all {len(m.criteria)} acceptance criteria. Pay {m.agreement.currency} {m.amount} to the "
        "payee through PayPal with the link below; the verdict and evidence are in the agreement's audit log.",
        link=f"{n.base_url}{path}",
        tone="held",
    )


def payment_link(
    session: Session, gw: PayPalGateway, settlement_id: str, base_url: str, token: str | None, now: datetime
) -> str:
    """The payer opened a signed /pay/{id} link: send them to a fresh PayPal order (direct) or the PayPal invoice
    (platform). The signature is checked before anything about the request is revealed."""
    links = pay_links_for(session)
    problem = links.check(settlement_id, token, now) if links else "invalid"
    if problem == "expired":
        raise ServiceError(
            "this pay link has expired; open the tranche in the app for a fresh one, or ask the payee to resend it", 410
        )
    if problem:
        raise ServiceError("this pay link is not valid", 404)
    s = session.get(Settlement, settlement_id)
    if s is None or s.mode != SettlementMode.invoice_on_verify:
        raise ServiceError("unknown payment request", 404)
    if s.status == SettlementStatus.succeeded:
        raise ServiceError("this tranche is already paid", 409)
    if s.status != SettlementStatus.submitted:
        raise ServiceError("this payment request was closed; the payee can ask for a new one", 410)
    m = s.milestone
    if m.payout_route != PayoutRoute.direct:
        if not s.payer_url:
            raise ServiceError("the invoice is still being sent; try again in a minute", 409)
        return s.payer_url
    a = m.agreement
    try:
        order = gw.create_payment_order(
            reference_id=m.id,
            description=f"{a.title}: {m.title}",
            amount=m.amount,
            currency=a.currency,
            payee_merchant_id=m.payee_merchant_id,
            return_url=f"{base_url}/paypal/paid",
            cancel_url=f"{base_url}/paypal/cancel",
            request_id=f"{s.idempotency_key}-order-{uuid.uuid4().hex[:12]}",
        )
    except PayPalError as e:
        raise _paypal_failure(e) from e
    s.paypal_ref = order.order_id  # only the latest order can complete the payment
    _note(session, m, "payer:pay_link", "payment_started", order_id=order.order_id)
    session.commit()
    return order.approve_url


def complete_payment(session: Session, gw: PayPalGateway, order_id: str, now: datetime) -> Milestone:
    """The payer approved a pay-now order and came back: capture it. Idempotent."""
    s = session.exec(
        select(Settlement).where(Settlement.paypal_ref == order_id, Settlement.mode == SettlementMode.invoice_on_verify)
    ).first()
    if s is None:
        raise ServiceError("unknown or superseded payment; open the latest pay link", 404)
    if s.status != SettlementStatus.submitted:
        return s.milestone
    actor = "payer:paypal_return"
    try:
        capture = gw.capture_order(order_id, request_id=f"{order_id}-capture")
    except PayPalError as e:
        _note(session, s.milestone, actor, "payment_failed", order_id=order_id, error=str(e)[:300])
        session.commit()
        raise _paypal_failure(e) from e
    _apply_payment_status(session, s, capture.capture_id, capture.status, actor, now)
    session.commit()
    if capture.status not in ("COMPLETED", "PENDING"):
        raise ServiceError("PayPal declined the payment; try again from the pay link", 402)
    return s.milestone


def _apply_payment_status(
    session: Session, s: Settlement, capture_id: str, status: str, actor: str, now: datetime
) -> str:
    m = s.milestone
    if status == "COMPLETED":
        return _close_invoice(session, s, True, actor, now, ref=capture_id)
    if status == "PENDING":
        _note(session, m, actor, "payment_pending", capture_id=capture_id)
        return "payment pending"
    _note(session, m, actor, "payment_declined", capture_id=capture_id, status=status)  # payer may try again
    return "payment declined"


def _close_invoice(
    session: Session, s: Settlement, paid: bool, actor: str, now: datetime, status: str = "", ref: str | None = None
) -> str:
    m = s.milestone
    if s.status != SettlementStatus.submitted:
        return "ignored: invoice already closed"
    ref = ref or s.paypal_ref or "unknown"
    if paid and m.payout_route == PayoutRoute.platform:
        # Paid into the platform account: pay the payee with Payouts now.
        s.status = SettlementStatus.succeeded
        m.captured_at = m.release_due_at = now
        _ledger(session, m, "collected", ref, s.amount)
        transition(session, m, MilestoneStatus.held, actor, "invoice_paid", invoice_id=s.paypal_ref, attempt=s.attempt)
        _reveal(session, m, actor, now)
        session.info.setdefault("payout_due", set()).add(m.id)
        return "invoice paid"
    if paid:
        s.status = SettlementStatus.succeeded
        _ledger(session, m, "paid", ref, s.amount)
        transition(session, m, MilestoneStatus.settled, actor, "settled", paypal_ref=ref, attempt=s.attempt)
        _reveal(session, m, actor, now)
        return "invoice paid"
    s.status = status or SettlementStatus.failed
    _ledger(session, m, "invoice_cancelled", s.paypal_ref or f"request:{s.id}", s.amount)
    transition(
        session,
        m,
        MilestoneStatus.settlement_failed,
        actor,
        "invoice_closed_unpaid",
        invoice_id=s.paypal_ref,
        status=s.status,
    )
    return "invoice closed unpaid"


def expire_stale_invoices(
    session: Session,
    gw: PayPalGateway,
    now: datetime,
    actor: str = "system:expiry",
    timeout: timedelta = INVOICE_TIMEOUT,
) -> int:
    """Close payment requests left unpaid past the timeout (cancelling PayPal invoices) so the tranche can be
    asked for again or reviewed. A pay-now order the payer never approved simply lapses at PayPal."""
    count = 0
    stale = session.exec(
        select(Settlement).where(
            Settlement.mode == SettlementMode.invoice_on_verify,
            Settlement.status == SettlementStatus.submitted,
        )
    ).all()
    for s in stale:
        if now - as_utc(s.created_at) <= timeout:
            continue
        if s.milestone.payout_route != PayoutRoute.direct:
            if not s.paypal_ref:
                continue  # invoice send outcome unknown; release resumes it
            try:
                paid = gw.invoice_status(s.paypal_ref) == "PAID"
            except PayPalError as e:
                if e.ambiguous:
                    continue  # can't tell whether it was paid; don't cancel blind, try again next run
                paid = False  # PayPal has no such invoice: nothing to settle, so close it as below
            if paid:  # paid in PayPal but the webhook never reached us: settle it rather than cancel it
                _close_invoice(session, s, paid=True, actor=actor, now=now)
                continue
            try:
                gw.cancel_invoice(s.paypal_ref, request_id=f"{s.paypal_ref}-cancel")
            except PayPalError as e:
                if e.ambiguous:
                    continue  # try again next run
        _close_invoice(session, s, paid=False, actor=actor, now=now, status=SettlementStatus.expired)
        count += 1
    session.commit()
    return count


# --- webhooks --------------------------------------------------------------------------

CAPTURE_EVENTS = {
    "PAYMENT.CAPTURE.COMPLETED": "COMPLETED",
    "PAYMENT.CAPTURE.PENDING": "PENDING",
    "PAYMENT.CAPTURE.DENIED": "DECLINED",
    "PAYMENT.CAPTURE.DECLINED": "DECLINED",
}


PAYOUT_ITEM_EVENTS = {
    "PAYMENT.PAYOUTS-ITEM.SUCCEEDED": "SUCCESS",
    "PAYMENT.PAYOUTS-ITEM.UNCLAIMED": "UNCLAIMED",
    "PAYMENT.PAYOUTS-ITEM.HELD": "PENDING",
    "PAYMENT.PAYOUTS-ITEM.FAILED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.RETURNED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.BLOCKED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.DENIED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.REFUNDED": "FAILED",
    "PAYMENT.PAYOUTS-ITEM.CANCELED": "FAILED",
}
REFERENCED_PAYOUT_EVENTS = {
    "PAYMENT.REFERENCED-PAYOUT-ITEM.COMPLETED": "SUCCESS",
    "PAYMENT.REFERENCED-PAYOUT-ITEM.FAILED": "FAILED",
}


DISPUTE_EVENTS = {"CUSTOMER.DISPUTE.CREATED", "CUSTOMER.DISPUTE.UPDATED", "CUSTOMER.DISPUTE.RESOLVED"}


def _customer_dispute(session: Session, event_type: str, res: dict, actor: str, now: datetime) -> str:
    """A PayPal customer dispute or chargeback against one of our captures. Recorded on the tranche; a held
    tranche's payout is paused (as a contest) so a reviewer decides. Money PayPal reverses on its own shows up
    through the capture and refund events."""
    dispute_id = res.get("dispute_id") or res.get("id")
    txns = [
        t.get("seller_transaction_id") or t.get("buyer_transaction_id") for t in res.get("disputed_transactions") or []
    ]
    entry = next(
        (
            e
            for txn in txns
            if txn
            for e in session.exec(select(LedgerEntry).where(LedgerEntry.paypal_ref == txn)).all()
            if e.milestone_id
        ),
        None,
    )
    m = session.get(Milestone, entry.milestone_id) if entry else None
    if m is None:
        return "ignored: no tranche for this dispute"
    detail = {
        "dispute_id": dispute_id,
        "reason": res.get("reason"),
        "status": res.get("status"),
        "outcome": (res.get("dispute_outcome") or {}).get("outcome_code"),
        "amount": (res.get("dispute_amount") or {}).get("value"),
    }
    if event_type == "CUSTOMER.DISPUTE.CREATED" and m.revealed_at is not None:
        _dispute_pack_ready(session, m, actor, detail)
    if (
        event_type == "CUSTOMER.DISPUTE.CREATED"
        and m.status == MilestoneStatus.held
        and _open(m, {DisbursementKind.referenced_payout, DisbursementKind.payout}) is None
    ):
        transition(session, m, MilestoneStatus.contested, actor, "contested", **detail)
        return "dispute opened: payout paused"
    action = {"CREATED": "paypal_dispute_opened", "UPDATED": "paypal_dispute_updated"}.get(
        event_type.rsplit(".", 1)[-1], "paypal_dispute_resolved"
    )
    _note(session, m, actor, action, **detail)
    return f"dispute {event_type.rsplit('.', 1)[-1].lower()} recorded"


def _dispute_pack_ready(session: Session, m: Milestone, actor: str, detail: dict) -> None:
    """The payer disputed a payment for work already opened to them: the payee's answer is the record. Tell the
    payee side where its dispute pack is (behind sign-in; evidence isn't emailed)."""
    seen = session.exec(
        select(AuditEvent).where(AuditEvent.milestone_id == m.id, AuditEvent.action == "dispute_pack_ready")
    ).all()
    if any((e.detail or {}).get("dispute_id") == detail.get("dispute_id") for e in seen):
        return
    _note(session, m, actor, "dispute_pack_ready", dispute_id=detail.get("dispute_id"))
    notify_side(
        session,
        m,
        Role.payee,
        tranche_subject(m, "Payment disputed"),
        f"The payer opened a PayPal dispute ({detail.get('reason') or 'no reason given'}) on the payment for "
        f"'{m.title}', whose work was opened to them on {as_utc(m.revealed_at):%B %d, %Y}. Your dispute pack is on "
        "the tranche: the criteria both sides signed, the hashes of what was verified, the verdict with citations, "
        "and every time the payer side opened or downloaded the work. Answer the dispute on PayPal with it.",
        tone="alert",
    )


DISPUTE_POLL_LOOKBACK = timedelta(days=3)  # re-read disputes updated this recently; covers missed hourly runs
DISPUTE_POLL_HORIZON = timedelta(days=180)  # PayPal's window for a buyer to open a dispute


def poll_disputes(session: Session, gw: PayPalGateway, now: datetime) -> dict[str, int]:
    """Backstop to CUSTOMER.DISPUTE.* webhooks (another team saw none in sandbox): ask the Disputes API about
    disputes on captures we know about and handle each new state exactly like the webhook would. Asks the platform
    account (platform-route captures) and each connected payee with a recent direct-route capture."""
    counts = {"disputes_recorded": 0, "dispute_accounts_unreadable": 0}
    recent = session.exec(
        select(Milestone.payout_route, Milestone.payee_merchant_id)
        .where(col(Milestone.captured_at).is_not(None), col(Milestone.captured_at) >= now - DISPUTE_POLL_HORIZON)
        .distinct()
    ).all()
    accounts = {merchant if route == PayoutRoute.direct else None for route, merchant in recent}
    for merchant in sorted(accounts, key=lambda a: a or ""):
        try:
            disputes = gw.list_disputes(updated_after=now - DISPUTE_POLL_LOOKBACK, payee_merchant_id=merchant)
        except PayPalError:
            counts["dispute_accounts_unreadable"] += 1  # e.g. a payee onboarded before READ_SELLER_DISPUTE
            continue
        for res in disputes:
            dispute_id = res.get("dispute_id")
            if not dispute_id:
                continue
            event_id = f"poll:{dispute_id}:{res.get('update_time') or res.get('status')}"[:80]
            if session.get(WebhookEvent, event_id) is not None:
                continue
            seen = session.exec(
                select(WebhookEvent.id).where(
                    WebhookEvent.resource_id == dispute_id, col(WebhookEvent.event_type).in_(DISPUTE_EVENTS)
                )
            ).first()
            kind = "RESOLVED" if res.get("status") == "RESOLVED" else ("UPDATED" if seen else "CREATED")
            event_type = f"CUSTOMER.DISPUTE.{kind}"
            outcome = _customer_dispute(session, event_type, res, "system:dispute_poll", now)
            session.add(WebhookEvent(id=event_id, event_type=event_type, resource_id=dispute_id, outcome=outcome))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()  # a concurrent run recorded it
                continue
            if not outcome.startswith("ignored"):
                counts["disputes_recorded"] += 1
    return counts


def _related(resource: dict, key: str) -> str | None:
    return (resource.get("supplementary_data") or {}).get("related_ids", {}).get(key)


def _dispatch(session: Session, event_type: str, res: dict, now: datetime, gw: PayPalGateway | None = None) -> str:
    actor = "webhook:paypal"
    rid = res.get("id")

    if event_type == "PAYMENT.AUTHORIZATION.CREATED":
        order_id = _related(res, "order_id")
        m = session.exec(select(Milestone).where(Milestone.paypal_order_id == order_id)).first() if order_id else None
        if m is None and rid:  # PayPal's v2 sample has no related_ids; we may already hold this authorization
            m = session.exec(select(Milestone).where(Milestone.paypal_authorization_id == rid)).first()
        if m is None:
            return "ignored: no tranche for this order"
        return "funded" if _mark_funded(session, m, rid, actor, now) else "already funded"

    if event_type == "PAYMENT.AUTHORIZATION.VOIDED":
        m = session.exec(select(Milestone).where(Milestone.paypal_authorization_id == rid)).first()
        if m is None:
            return "ignored: hold already released"
        _drop_hold(session, m, "voided", actor)
        return "hold voided"

    if event_type in CAPTURE_EVENTS:
        order_id = _related(res, "order_id")
        paid = (
            session.exec(
                select(Settlement).where(
                    Settlement.paypal_ref == order_id, Settlement.mode == SettlementMode.invoice_on_verify
                )
            ).first()
            if order_id
            else None
        )
        if paid is not None:  # invoice mode, pay-now order
            return _apply_payment_status(session, paid, rid, CAPTURE_EVENTS[event_type], actor, now)
        s = session.exec(select(Settlement).where(Settlement.paypal_ref == rid)).first()
        if s is None:
            # Capture response was lost (ambiguous failure): match through the authorization.
            auth_id = _related(res, "authorization_id")
            m = session.exec(select(Milestone).where(Milestone.paypal_authorization_id == auth_id)).first()
            s = m.settlements[-1] if m and m.settlements else None
            if s is None or s.status != SettlementStatus.submitted:
                return "ignored: no open settlement for this capture"
            s.paypal_ref = rid
        _apply_capture_status(session, s, CAPTURE_EVENTS[event_type], actor, now)
        return f"capture {CAPTURE_EVENTS[event_type].lower()}"

    if event_type in PAYOUT_ITEM_EVENTS:
        key = (res.get("payout_item") or {}).get("sender_item_id")
        d = session.exec(select(Disbursement).where(Disbursement.idempotency_key == key)).first() if key else None
        if d is None:
            d = session.exec(select(Disbursement).where(Disbursement.paypal_ref == res.get("payout_batch_id"))).first()
        if d is None or d.kind != DisbursementKind.payout:
            return "ignored: no payout for this item"
        d.paypal_ref = d.paypal_ref or res.get("payout_batch_id")
        _apply_payout_status(session, d, PAYOUT_ITEM_EVENTS[event_type], actor)
        return f"payout {PAYOUT_ITEM_EVENTS[event_type].lower()}"

    if event_type in REFERENCED_PAYOUT_EVENTS:
        d = session.exec(select(Disbursement).where(Disbursement.paypal_ref == (res.get("item_id") or rid))).first()
        if d is None:  # response was lost: match through the capture it releases
            s = session.exec(select(Settlement).where(Settlement.paypal_ref == res.get("reference_id"))).first()
            d = _open(s.milestone, {DisbursementKind.referenced_payout}, s) if s else None
            if d is None:
                return "ignored: no payout for this capture"
            d.paypal_ref = res.get("item_id") or rid
        _apply_payout_status(session, d, REFERENCED_PAYOUT_EVENTS[event_type], actor)
        return f"payout {REFERENCED_PAYOUT_EVENTS[event_type].lower()}"

    if event_type == "PAYMENT.CAPTURE.REFUNDED":
        d = session.exec(select(Disbursement).where(Disbursement.paypal_ref == rid)).first()
        if d is None or d.kind != DisbursementKind.refund:
            return "ignored: no refund for this event"
        _apply_refund_status(session, d, res.get("status", "COMPLETED"), actor)
        return "refund recorded"

    if event_type in DISPUTE_EVENTS:
        return _customer_dispute(session, event_type, res, actor, now)

    if event_type == "MERCHANT.ONBOARDING.COMPLETED":
        return payee_accounts.onboarding_completed(session, res.get("merchant_id"), res.get("tracking_id"), gw)

    if event_type == "MERCHANT.PARTNER-CONSENT.REVOKED":
        return payee_accounts.consent_revoked(session, res.get("merchant_id"), now)

    if event_type in ("INVOICING.INVOICE.PAID", "INVOICING.INVOICE.CANCELLED"):
        s = session.exec(select(Settlement).where(Settlement.paypal_ref == rid)).first()
        if s is None or s.mode != SettlementMode.invoice_on_verify:
            return "ignored: no settlement for this invoice"
        return _close_invoice(session, s, paid=event_type == "INVOICING.INVOICE.PAID", actor=actor, now=now)

    return "ignored: event type not handled"


def handle_webhook(session: Session, event: dict, now: datetime, gw: PayPalGateway | None = None) -> str:
    event_id, event_type = event.get("id"), event.get("event_type")
    if not event_id or not event_type:
        raise ServiceError("webhook event missing id or event_type", 400)
    if session.get(WebhookEvent, event_id) is not None:
        return "duplicate"
    res = event.get("resource") or {}
    outcome = _dispatch(session, event_type, res, now, gw)
    resource_id = res.get("id") or res.get("dispute_id")  # dispute resources carry dispute_id, not id
    session.add(WebhookEvent(id=event_id, event_type=event_type, resource_id=resource_id, outcome=outcome))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()  # the same event delivered concurrently; the other copy won
        return "duplicate"
    for mid in session.info.pop("payout_due", set()):  # invoice paid into the platform: pay the payee now
        m = session.get(Milestone, mid)
        if gw is not None and m is not None and m.status == MilestoneStatus.held:
            try:
                pay_out(session, gw, m, "webhook:paypal", now)
            except ServiceError:
                pass  # recorded on the tranche; the payouts job retries
    if gw is not None:
        pay_due_partials(session, gw, "webhook:paypal", now)  # a share's capture completed: Payouts (platform route)
    else:
        session.info.pop("partial_payout_due", None)
    return outcome
