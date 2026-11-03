"""Connecting a payee organization's PayPal account (PayPal partner onboarding).

A connected account is paid directly: tranches are held and captured in its name, and the platform never
holds the money. An org that never connects is paid through the platform account with PayPal Payouts.

What PayPal puts on the return URL is only used to find our record; the account's state always comes from
PayPal's merchant-integrations API.
"""

import uuid
from datetime import datetime

from sqlmodel import Session, select

from app.db import record_audit
from app.integrations.paypal import PayPalError, PayPalGateway
from app.models import Organization, Party, PayPalAccount, PayPalAccountRead, Role
from app.services import ServiceError


def account_for(session: Session, org_id: str) -> PayPalAccount | None:
    return session.exec(select(PayPalAccount).where(PayPalAccount.org_id == org_id)).first()


def read(row: PayPalAccount | None, org_id: str) -> PayPalAccountRead:
    if row is None:
        return PayPalAccountRead(org_id=org_id, ready=False, issues=["PayPal isn't connected"], checked_at=None)
    return PayPalAccountRead.model_validate(row, update={"ready": row.ready, "issues": row.issues()})


def _audit_all_agreements(session: Session, org_id: str, actor: str, action: str, **detail) -> None:
    for party in session.exec(select(Party).where(Party.org_id == org_id, Party.role == Role.payee)).all():
        record_audit(session, party.agreement_id, actor, action, detail=detail)


def start(session: Session, gw: PayPalGateway, org: Organization, actor: str, base_url: str) -> str:
    """Begin (or restart) onboarding. Returns PayPal's onboarding link for an admin of the org."""
    row = account_for(session, org.id)
    if row is not None and row.ready:
        raise ServiceError("this organization's PayPal account is already connected and ready")
    tracking_id = f"{org.id}-{uuid.uuid4().hex[:8]}"
    try:
        url = gw.create_partner_referral(
            tracking_id=tracking_id, return_url=f"{base_url}/paypal/onboarded", email=org.paypal_email
        )
    except PayPalError as e:
        raise ServiceError(f"PayPal didn't start onboarding: {e}", 502) from e
    if row is None:
        row = PayPalAccount(org_id=org.id, tracking_id=tracking_id, started_by=actor)
        session.add(row)
    else:  # restart: a new tracking id; a revoked account's merchant id is re-read when it finishes
        row.tracking_id, row.started_by, row.revoked_at = tracking_id, actor, None
    session.commit()
    return url


def refresh(session: Session, gw: PayPalGateway, row: PayPalAccount, actor: str, now: datetime) -> PayPalAccount:
    """Read the account's state from PayPal."""
    was_ready = row.ready
    try:
        if row.merchant_id is None:
            row.merchant_id = gw.find_merchant(row.tracking_id)
        if row.merchant_id is not None:
            status = gw.merchant_status(row.merchant_id)
            row.payments_receivable = status.payments_receivable
            row.primary_email_confirmed = status.primary_email_confirmed
            row.permissions_granted = status.permissions_granted
    except PayPalError as e:
        raise ServiceError(f"couldn't read the PayPal account's status: {e}", 502) from e
    row.checked_at = now
    if row.ready and not was_ready:
        _audit_all_agreements(session, row.org_id, actor, "paypal_connected", merchant_id=row.merchant_id)
    session.commit()
    return row


def finish(session: Session, gw: PayPalGateway, tracking_id: str, now: datetime) -> PayPalAccount:
    """The payee came back from PayPal's onboarding pages."""
    row = session.exec(select(PayPalAccount).where(PayPalAccount.tracking_id == tracking_id)).first()
    if row is None:
        raise ServiceError("unknown or superseded onboarding; start again from your organization's settings", 404)
    return refresh(session, gw, row, "payee:paypal_onboarding_return", now)


def consent_revoked(session: Session, merchant_id: str, now: datetime) -> str:
    """MERCHANT.PARTNER-CONSENT.REVOKED: stop routing new tranches to this account."""
    row = session.exec(select(PayPalAccount).where(PayPalAccount.merchant_id == merchant_id)).first()
    if row is None:
        return "ignored: unknown merchant"
    if row.revoked_at is None:
        row.revoked_at = now
        _audit_all_agreements(session, row.org_id, "webhook:paypal", "paypal_consent_revoked", merchant_id=merchant_id)
    return "consent revoked"


def onboarding_completed(session: Session, merchant_id: str | None, tracking_id: str | None) -> str:
    """MERCHANT.ONBOARDING.COMPLETED: record the merchant id; status is read from PayPal on next use."""
    row = session.exec(select(PayPalAccount).where(PayPalAccount.tracking_id == tracking_id)).first()
    if row is None or not merchant_id:
        return "ignored: unknown onboarding"
    row.merchant_id = merchant_id
    row.checked_at = None
    return "onboarding completed"
