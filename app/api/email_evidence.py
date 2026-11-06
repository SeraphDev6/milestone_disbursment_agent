"""Email as an evidence source (#44): the payee forwards evidence to an agreement-specific address, naming the
tranche with a tag in the subject; attachments go through the same parsing as uploads.

- `POST /agreements/{id}/integrations/email` (payee admin) registers the address: evidence-<integration id>@<inbox
  domain>, plus the subject tag per tranche (mde-<milestone id>, the same label GitHub uses).
- `POST /webhooks/email/resend` takes Resend's `email.received` event (Svix-signed), finds the agreement by the
  address, checks the sender is a member of the payee's organization and the subject names one of its tranches,
  then reads the message and attachments back from Resend in the background and submits them as evidence. Mail that
  matches no address is ignored; mail that matches one but fails a check is dropped and audited, never guessed onto a
  tranche.
"""

import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from sqlmodel import Field, SQLModel, select

from app.api.deps import AgreementAccessDep, SessionDep
from app.db import record_audit
from app.integrations import github
from app.integrations.inbound_email import ReceivedMail, address_of, verify_svix
from app.integrations.uploads import UploadError, parse_upload
from app.models import (
    Agreement,
    AgreementStatus,
    EvidenceCreate,
    EvidenceType,
    Integration,
    Membership,
    Milestone,
    Role,
    User,
    WebhookEvent,
)
from app.services import files as stored
from app.services import verification
from app.services.access import Action

router = APIRouter()
LOCAL_PART = "evidence-"


class MilestoneTag(SQLModel):
    milestone_id: str
    title: str
    tag: str = Field(description="Put this in the subject so the mail counts as evidence for the tranche.")


class EmailIntegration(SQLModel):
    integration_id: str
    address: str = Field(description="Forward evidence here, from an address that belongs to a member of your org.")
    tags: list[MilestoneTag]


def inbox_address(integration_id: str, domain: str) -> str:
    return f"{LOCAL_PART}{integration_id}@{domain}".lower()


def _domain(request: Request) -> str:
    domain = request.app.state.settings.email_inbox_domain
    if not domain or request.app.state.inbox is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this server doesn't receive email (EMAIL_INBOX_DOMAIN unset)")
    return domain


@router.post(
    "/agreements/{agreement_id}/integrations/email",
    tags=["integrations"],
    summary="Payee gets an address to forward evidence to; the subject tag picks the tranche",
)
def register_email(
    agreement_id: str, access: AgreementAccessDep, session: SessionDep, request: Request
) -> EmailIntegration:
    access.require(Action.connect_email)
    domain = _domain(request)
    a = access.party.agreement
    if a.status in (AgreementStatus.cancelled, AgreementStatus.completed):
        raise HTTPException(status.HTTP_409_CONFLICT, f"agreement is {a.status}")
    row = session.exec(select(Integration).where(Integration.agreement_id == a.id, Integration.kind == "email")).first()
    if row is None:
        row = Integration(agreement_id=a.id, kind="email", target="", secret="", created_by=access.actor)
        session.add(row)
        session.flush()
        row.target = inbox_address(row.id, domain)
        record_audit(session, a.id, access.actor, "email_connected", detail={"address": row.target})
        session.commit()
    return EmailIntegration(
        integration_id=row.id,
        address=row.target,
        tags=[MilestoneTag(milestone_id=m.id, title=m.title, tag=github.label_for(m.id)) for m in a.milestones],
    )


@router.post("/webhooks/email/resend", tags=["integrations"], summary="Resend receiving webhook (Svix-signed)")
async def resend_webhook(
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
    svix_id: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    state = request.app.state
    secret = state.settings.resend_webhook_secret
    if secret is None or state.inbox is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "inbound email isn't configured")
    raw = await request.body()
    if not verify_svix(secret.get_secret_value(), dict(request.headers), raw):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "signature doesn't match")
    if not svix_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "svix-id header required")
    delivery_id = f"resend:{svix_id}"
    if session.get(WebhookEvent, delivery_id) is not None:
        return {"outcome": "duplicate"}
    try:
        event = json.loads(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from e
    data = event.get("data") or {}
    email_id = data.get("email_id") or ""
    if event.get("type") != "email.received" or not email_id:
        outcome = f"ignored: {event.get('type')}"
    else:
        recipients = [
            address_of(x) for x in (data.get("to") or []) + (data.get("cc") or []) + (data.get("received_for") or [])
        ]
        integration = _integration_for(session, recipients)
        if integration is None:
            outcome = "ignored: no evidence address among the recipients"
        else:
            outcome = "queued"
            background.add_task(
                ingest_mail,
                state,
                integration.id,
                email_id,
                address_of(data.get("from") or ""),
                data.get("subject") or "",
            )
    session.add(
        WebhookEvent(id=delivery_id, event_type="resend.email.received", resource_id=email_id or None, outcome=outcome)
    )
    session.commit()
    return {"outcome": outcome}


def _integration_for(session, recipients: list[str]) -> Integration | None:
    for address in recipients:
        local = address.partition("@")[0]
        if local.startswith(LOCAL_PART):
            row = session.get(Integration, local[len(LOCAL_PART) :])
            if row is not None and row.kind == "email" and row.target == address:
                return row
    return None


def _drop(session, integration: Integration, actor: str, reason: str, **detail) -> None:
    record_audit(
        session, integration.agreement_id, actor, "email_evidence_dropped", detail={"reason": reason, **detail}
    )
    session.commit()


def ingest_mail(state, integration_id: str, email_id: str, sender: str, subject: str) -> None:
    """Background: the checks that need the database, then the message read back from the inbox and stored as
    evidence on the tagged tranche."""
    actor = f"email:{sender}"
    to_verify = None
    with state.sessionmaker() as session:
        integration = session.get(Integration, integration_id)
        if integration is None or integration.kind != "email":
            return
        agreement = session.get(Agreement, integration.agreement_id)
        if agreement is None:
            return
        payee_org = next((p.org_id for p in agreement.parties if p.role == Role.payee), None)
        user = session.exec(select(User).where(User.email == sender)).first()
        member = (
            session.exec(
                select(Membership).where(Membership.org_id == payee_org, Membership.user_id == user.id)
            ).first()
            if user and payee_org
            else None
        )
        if member is None:
            _drop(
                session,
                integration,
                actor,
                "sender isn't a member of the payee's organization",
                sender=sender,
                subject=subject,
            )
            return
        tagged = [m for m in agreement.milestones if github.label_for(m.id) in subject]
        if len(tagged) != 1:
            _drop(
                session,
                integration,
                actor,
                "subject names no tranche" if not tagged else "subject names several tranches",
                sender=sender,
                subject=subject,
            )
            return
        m: Milestone = tagged[0]
        actor = f"user:{user.id}@org:{payee_org}"
        try:
            mail: ReceivedMail = state.inbox.fetch(email_id)
        except Exception as e:  # noqa: BLE001 (the provider's error is the detail)
            _drop(
                session, integration, actor, f"couldn't read the message from the inbox: {e}"[:300], email_id=email_id
            )
            return
        items = []
        body = (mail.text or "").strip()
        if body:
            items.append(
                (
                    EvidenceCreate(
                        evidence_type=EvidenceType.document,
                        title=f"Email: {mail.subject or '(no subject)'}"[:200],
                        content=body[:200_000],
                        external_ref=f"email:{email_id}"[:500],
                    ),
                    None,
                    None,
                )
            )
        for att in mail.attachments:
            try:
                for parsed in parse_upload(att.filename, att.data, None, None):
                    items.append((parsed.evidence, parsed.data, parsed.media_type))
            except UploadError as e:
                record_audit(
                    session,
                    agreement.id,
                    actor,
                    "email_attachment_skipped",
                    milestone_id=m.id,
                    detail={"filename": att.filename, "reason": str(e)},
                )
                session.commit()
        if not items:
            _drop(session, integration, actor, "the message had no text and no readable attachment", email_id=email_id)
            return
        created_any = False
        for ev, data, media_type in items:
            file_id = (
                stored.save(session, data, filename=ev.title, media_type=media_type).id if data is not None else None
            )
            try:
                _, created = verification.submit_evidence(session, m, ev, source="email", actor=actor, file_id=file_id)
            except verification.VerificationError as e:
                _drop(session, integration, actor, e.message, milestone_id=m.id, email_id=email_id)
                return
            created_any = created_any or created
        record_audit(
            session,
            agreement.id,
            actor,
            "email_evidence_received",
            milestone_id=m.id,
            detail={"email_id": email_id, "subject": mail.subject, "items": len(items)},
        )
        session.commit()
        if created_any and m.status.value == "evidence_received":
            to_verify = m.id
    if to_verify:
        verification.verify_in_background(state.sessionmaker, state.ai, state.paypal, to_verify, "system:verifier")
