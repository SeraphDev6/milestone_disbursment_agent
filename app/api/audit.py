"""The record: event logs and the compliance packet."""

import json
from typing import Literal

from fastapi import APIRouter, Response
from sqlmodel import select

from app.api.deps import AgreementAccessDep, MilestoneAccessDep, SessionDep
from app.models import AuditEvent, AuditEventRead
from app.services.access import Action
from app.services.packet import build_dispute_pack, build_packet, render_pdf

router = APIRouter(tags=["audit"])


@router.get("/agreements/{agreement_id}/audit", summary="Full event log for an agreement")
def agreement_audit(agreement_id: str, access: AgreementAccessDep, session: SessionDep) -> list[AuditEventRead]:
    access.require(Action.view)
    query = select(AuditEvent).where(AuditEvent.agreement_id == agreement_id).order_by(AuditEvent.at)
    return [AuditEventRead.model_validate(e) for e in session.exec(query).all()]


@router.get(
    "/milestones/{milestone_id}/audit",
    openapi_extra={"x-mcp-tool": "explain_milestone"},
    summary="Every event for one tranche: who did what, when, and why",
)
def milestone_audit(milestone_id: str, auth: MilestoneAccessDep, session: SessionDep) -> list[AuditEventRead]:
    access, m = auth
    access.require(Action.view)
    query = select(AuditEvent).where(AuditEvent.milestone_id == m.id).order_by(AuditEvent.at)
    return [AuditEventRead.model_validate(e) for e in session.exec(query).all()]


@router.get(
    "/milestones/{milestone_id}/dispute-pack",
    summary="One tranche's record for answering a chargeback (payee or reviewer)",
    description="The signed criteria, the commitment (hashes of what was verified), when the work was opened to "
    "the payer, every payer-side view and download since, the verdict with citations, and the payments.",
)
def dispute_pack(
    milestone_id: str, auth: MilestoneAccessDep, session: SessionDep, format: Literal["json", "pdf"] = "json"
) -> Response:
    access, m = auth
    access.require(Action.dispute_pack)
    pack = build_dispute_pack(session, m)
    if format == "pdf":
        return Response(
            render_pdf(pack),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="dispute-pack-{milestone_id}.pdf"'},
        )
    return Response(json.dumps(pack, indent=2, default=str), media_type="application/json")


@router.get(
    "/agreements/{agreement_id}/compliance-packet",
    openapi_extra={"x-mcp-tool": "export_compliance_packet"},
    summary="Criteria, confirmations, evidence, verdicts with citations, reviews, payments and audit trail",
)
def compliance_packet(
    agreement_id: str, access: AgreementAccessDep, session: SessionDep, format: Literal["json", "pdf"] = "json"
) -> Response:
    access.require(Action.compliance_packet)
    packet = build_packet(session, access.party.agreement, access.party.role)
    if format == "pdf":
        return Response(
            render_pdf(packet),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="compliance-{agreement_id}.pdf"'},
        )
    return Response(json.dumps(packet, indent=2, default=str), media_type="application/json")
