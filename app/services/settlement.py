"""Escrow settlement: one PayPal authorization hold per tranche, captured on release.

Money rules enforced here:
- A tranche is funded by an AUTHORIZE order the payer approves; funds stay on the payer's account.
- Release captures the hold to the payee named on the order. Only from `verified` (or a retry).
- Every capture carries an idempotency key `{milestone_id}-{attempt}`. An ambiguous failure
  (timeout, 5xx) keeps the attempt open and is retried with the same key; a definitive failure
  closes it and the next release opens attempt + 1. PayPal refuses a second capture of a fully
  captured authorization, so a new key can't pay twice either.
- Holds are honored for 3 days and valid for 29. Release after day 3 reauthorizes once first;
  past day 29 the hold is gone and the payer must fund again.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db import LedgerEntry, Milestone, Party, Settlement, WebhookEvent, as_utc, record_audit
from app.domain.models import AgreementStatus, MilestoneStatus, Role, SettlementMode, SettlementStatus
from app.integrations.paypal import CreatedOrder, PayPalError, PayPalGateway
from app.services import ServiceError

HONOR_PERIOD = timedelta(days=3)
AUTH_VALIDITY = timedelta(days=29)

NO_LIVE_HOLD_STATES = {MilestoneStatus.settling.value, MilestoneStatus.settled.value}
RELEASABLE = {MilestoneStatus.verified.value, MilestoneStatus.settlement_failed.value}


# --- helpers ---------------------------------------------------------------------------


def transition(session: Session, m: Milestone, to: MilestoneStatus, actor: str, action: str, **detail) -> None:
    previous = m.status
    m.status = to.value
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


def _ledger(session: Session, m: Milestone, kind: str, paypal_ref: str) -> None:
    session.add(
        LedgerEntry(agreement_id=m.agreement_id, milestone_id=m.id, kind=kind, amount=m.amount, paypal_ref=paypal_ref)
    )


def _payee(m: Milestone) -> Party:
    return next(p for p in m.agreement.parties if p.role == Role.payee.value)


def _paypal_failure(e: PayPalError) -> ServiceError:
    suffix = f" (PayPal debug id {e.debug_id})" if e.debug_id else ""
    return ServiceError(f"{e}{suffix}", 502)


def _mark_funded(session: Session, m: Milestone, authorization_id: str, actor: str, now: datetime) -> bool:
    if m.paypal_authorization_id is not None:
        return False  # already funded (return URL and webhook race; first one wins)
    m.paypal_authorization_id = authorization_id
    m.funded_at = now
    m.reauthorized_at = None
    _ledger(session, m, "authorized", authorization_id)
    if m.status == MilestoneStatus.pending.value:
        transition(session, m, MilestoneStatus.funded, actor, "funded", authorization_id=authorization_id)
    else:
        # Re-funded after an expired hold; verification state is kept.
        _note(session, m, actor, "refunded_hold", authorization_id=authorization_id)
    return True


def _drop_hold(session: Session, m: Milestone, kind: str, actor: str) -> None:
    ref = m.paypal_authorization_id or "unknown"
    _ledger(session, m, kind, ref)
    m.paypal_authorization_id = None
    m.funded_at = None
    m.reauthorized_at = None
    m.paypal_order_id = None
    if m.status == MilestoneStatus.funded.value:
        transition(session, m, MilestoneStatus.pending, actor, f"hold_{kind}", authorization_id=ref)
    else:
        _note(session, m, actor, f"hold_{kind}", authorization_id=ref)


# --- funding ---------------------------------------------------------------------------


def start_funding(session: Session, gw: PayPalGateway, m: Milestone, actor: str, base_url: str) -> CreatedOrder:
    a = m.agreement
    if a.status != AgreementStatus.active.value:
        raise ServiceError(f"agreement is {a.status}; both parties must confirm criteria before funding")
    if a.settlement_mode != SettlementMode.escrow_release.value:
        raise ServiceError("this agreement settles by invoice; there is nothing to fund")
    if m.paypal_authorization_id is not None:
        raise ServiceError("tranche already has a live hold")
    if m.status in NO_LIVE_HOLD_STATES:
        raise ServiceError(f"tranche is {m.status}")
    try:
        order = gw.create_authorize_order(
            reference_id=m.id,
            description=f"{a.title}: {m.title}",
            amount=m.amount,
            currency=a.currency,
            payee_email=_payee(m).paypal_email,
            return_url=f"{base_url}/paypal/return",
            cancel_url=f"{base_url}/paypal/cancel",
            request_id=f"{m.id}-order-{uuid.uuid4().hex[:12]}",
        )
    except PayPalError as e:
        raise _paypal_failure(e) from e
    m.paypal_order_id = order.order_id
    _note(session, m, actor, "funding_started", order_id=order.order_id)
    session.commit()
    return order


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
    if m.status != MilestoneStatus.funded.value or m.paypal_authorization_id is None:
        raise ServiceError("only a funded tranche with no evidence submitted can be voided")
    try:
        gw.void_authorization(m.paypal_authorization_id, request_id=f"{m.paypal_authorization_id}-void")
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
            auth = gw.reauthorize(old, amount=m.amount, currency=m.agreement.currency, request_id=f"{old}-reauth")
        except PayPalError as e:
            raise _paypal_failure(e) from e
        m.paypal_authorization_id = auth.authorization_id
        m.reauthorized_at = now
        _note(session, m, actor, "reauthorized", old_authorization_id=old, authorization_id=auth.authorization_id)
        session.commit()


def _apply_capture_status(session: Session, s: Settlement, status: str, actor: str) -> None:
    m = s.milestone
    if status == "COMPLETED":
        if s.status == SettlementStatus.succeeded.value:
            return
        s.status = SettlementStatus.succeeded.value
        _ledger(session, m, "captured", s.paypal_ref or "unknown")
        transition(session, m, MilestoneStatus.settled, actor, "settled", capture_id=s.paypal_ref, attempt=s.attempt)
    elif status == "PENDING":
        _note(session, m, actor, "capture_pending", capture_id=s.paypal_ref, attempt=s.attempt)
    else:
        if s.status == SettlementStatus.failed.value:
            return
        s.status = SettlementStatus.failed.value
        s.error = f"capture {status}"
        transition(
            session,
            m,
            MilestoneStatus.settlement_failed,
            actor,
            "settlement_failed",
            capture_id=s.paypal_ref,
            status=status,
            attempt=s.attempt,
        )


def release(session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime) -> Settlement:
    """Settle a verified tranche: capture its hold (escrow) or send a PayPal invoice (invoice mode)."""
    if m.agreement.settlement_mode == SettlementMode.invoice_on_verify.value:
        return _release_by_invoice(session, gw, m, actor)

    last = m.settlements[-1] if m.settlements else None
    if m.status == MilestoneStatus.settling.value and last and last.status == SettlementStatus.submitted.value:
        if last.paypal_ref:
            raise ServiceError("capture is pending at PayPal; the webhook will settle it", 409)
        s = last  # previous attempt's outcome unknown: resume with the same idempotency key
    elif m.status in RELEASABLE:
        _ensure_live_hold(session, gw, m, actor, now)
        attempt = last.attempt + 1 if last else 1
        s = Settlement(
            milestone=m,
            attempt=attempt,
            mode=SettlementMode.escrow_release.value,
            idempotency_key=f"{m.id}-{attempt}",
            status=SettlementStatus.submitted.value,
        )
        session.add(s)
        transition(session, m, MilestoneStatus.settling, actor, "release_requested", attempt=attempt)
        session.commit()  # record intent before money moves
    else:
        raise ServiceError(f"a tranche that is {m.status} can't be released")

    try:
        capture = gw.capture_authorization(
            m.paypal_authorization_id, amount=m.amount, currency=m.agreement.currency, request_id=s.idempotency_key
        )
    except PayPalError as e:
        s.error = str(e)[:500]
        if e.ambiguous:
            _note(session, m, actor, "capture_outcome_unknown", attempt=s.attempt, error=s.error)
            session.commit()
            raise ServiceError(
                "PayPal didn't confirm the capture; release again to resume with the same idempotency key", 502
            ) from e
        s.status = SettlementStatus.failed.value
        transition(
            session, m, MilestoneStatus.settlement_failed, actor, "settlement_failed", attempt=s.attempt, error=s.error
        )
        session.commit()
        raise _paypal_failure(e) from e

    s.paypal_ref = capture.capture_id
    _apply_capture_status(session, s, capture.status, actor)
    session.commit()
    return s


# --- invoice mode ----------------------------------------------------------------------

INVOICE_TIMEOUT = timedelta(days=30)


def _party(m: Milestone, role: Role) -> Party:
    return next(p for p in m.agreement.parties if p.role == role.value)


def _release_by_invoice(session: Session, gw: PayPalGateway, m: Milestone, actor: str) -> Settlement:
    """Invoice-on-verify: the payer gets a PayPal invoice for the tranche when it verifies.

    Invoicing v2 bills as the account that owns the API credentials, so the invoicer shown is the payee's
    PayPal email and, outside a single-payee demo, the payee must grant the platform third-party access.
    """
    last = m.settlements[-1] if m.settlements else None
    if m.status == MilestoneStatus.settling.value and last and last.status == SettlementStatus.submitted.value:
        if last.paypal_ref:
            raise ServiceError("the invoice is out; the tranche settles when the payer pays it", 409)
        s = last  # outcome of the previous send is unknown: resume with the same idempotency key
    elif m.status in RELEASABLE:
        attempt = last.attempt + 1 if last else 1
        s = Settlement(
            milestone=m,
            attempt=attempt,
            mode=SettlementMode.invoice_on_verify.value,
            idempotency_key=f"{m.id}-{attempt}",
            status=SettlementStatus.submitted.value,
        )
        session.add(s)
        transition(session, m, MilestoneStatus.settling, actor, "invoice_requested", attempt=attempt)
        session.commit()
    else:
        raise ServiceError(f"a tranche that is {m.status} can't be invoiced")

    a = m.agreement
    try:
        invoice = gw.create_and_send_invoice(
            invoicer_email=_party(m, Role.payee).paypal_email,
            recipient_email=_party(m, Role.payer).paypal_email,
            item_name=f"{a.title}: {m.title}",
            amount=m.amount,
            currency=a.currency,
            note=f"'{m.title}' met all {len(m.criteria)} acceptance criteria. Verdict and evidence are in the "
            "agreement's audit log.",
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
        s.status = SettlementStatus.failed.value
        transition(
            session, m, MilestoneStatus.settlement_failed, actor, "settlement_failed", attempt=s.attempt, error=s.error
        )
        session.commit()
        raise _paypal_failure(e) from e

    s.paypal_ref = invoice.invoice_id
    _ledger(session, m, "invoiced", invoice.invoice_id)
    _note(session, m, actor, "invoice_sent", invoice_id=invoice.invoice_id, payer_view_url=invoice.payer_view_url)
    session.commit()
    return s


def _close_invoice(session: Session, s: Settlement, paid: bool, actor: str, status: str = "") -> str:
    m = s.milestone
    if s.status != SettlementStatus.submitted.value:
        return "ignored: invoice already closed"
    if paid:
        s.status = SettlementStatus.succeeded.value
        _ledger(session, m, "paid", s.paypal_ref or "unknown")
        transition(session, m, MilestoneStatus.settled, actor, "settled", invoice_id=s.paypal_ref, attempt=s.attempt)
        return "invoice paid"
    s.status = status or SettlementStatus.failed.value
    _ledger(session, m, "invoice_cancelled", s.paypal_ref or "unknown")
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
    """Cancel invoices left unpaid past the timeout so the tranche can be re-invoiced or reviewed."""
    count = 0
    stale = session.exec(
        select(Settlement).where(
            Settlement.mode == SettlementMode.invoice_on_verify.value,
            Settlement.status == SettlementStatus.submitted.value,
            col(Settlement.paypal_ref).is_not(None),
        )
    ).all()
    for s in stale:
        if now - as_utc(s.created_at) <= timeout:
            continue
        try:
            gw.cancel_invoice(s.paypal_ref, request_id=f"{s.paypal_ref}-cancel")
        except PayPalError as e:
            if e.ambiguous:
                continue  # try again next run
        _close_invoice(session, s, paid=False, actor=actor, status=SettlementStatus.expired.value)
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


def _related(resource: dict, key: str) -> str | None:
    return (resource.get("supplementary_data") or {}).get("related_ids", {}).get(key)


def _dispatch(session: Session, event_type: str, res: dict, now: datetime) -> str:
    actor = "webhook:paypal"
    rid = res.get("id")

    if event_type == "PAYMENT.AUTHORIZATION.CREATED":
        order_id = _related(res, "order_id")
        m = session.exec(select(Milestone).where(Milestone.paypal_order_id == order_id)).first() if order_id else None
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
        s = session.exec(select(Settlement).where(Settlement.paypal_ref == rid)).first()
        if s is None:
            # Capture response was lost (ambiguous failure): match through the authorization.
            auth_id = _related(res, "authorization_id")
            m = session.exec(select(Milestone).where(Milestone.paypal_authorization_id == auth_id)).first()
            s = m.settlements[-1] if m and m.settlements else None
            if s is None or s.status != SettlementStatus.submitted.value:
                return "ignored: no open settlement for this capture"
            s.paypal_ref = rid
        _apply_capture_status(session, s, CAPTURE_EVENTS[event_type], actor)
        return f"capture {CAPTURE_EVENTS[event_type].lower()}"

    if event_type in ("INVOICING.INVOICE.PAID", "INVOICING.INVOICE.CANCELLED"):
        s = session.exec(select(Settlement).where(Settlement.paypal_ref == rid)).first()
        if s is None or s.mode != SettlementMode.invoice_on_verify.value:
            return "ignored: no settlement for this invoice"
        return _close_invoice(session, s, paid=event_type == "INVOICING.INVOICE.PAID", actor=actor)

    return "ignored: event type not handled"


def handle_webhook(session: Session, event: dict, now: datetime) -> str:
    event_id, event_type = event.get("id"), event.get("event_type")
    if not event_id or not event_type:
        raise ServiceError("webhook event missing id or event_type", 400)
    if session.get(WebhookEvent, event_id) is not None:
        return "duplicate"
    res = event.get("resource") or {}
    outcome = _dispatch(session, event_type, res, now)
    session.add(WebhookEvent(id=event_id, event_type=event_type, resource_id=res.get("id"), outcome=outcome))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()  # the same event delivered concurrently; the other copy won
        return "duplicate"
    return outcome
