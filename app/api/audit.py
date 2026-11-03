"""The record: event logs and the compliance packet."""

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import MilestonePartyDep, PartyDep, SessionDep, require_role
from app.db import AuditEvent
from app.domain.models import Role
from app.services.packet import build_packet, render_pdf

router = APIRouter(tags=["audit"])


class AuditEntry(BaseModel):
    at: datetime
    actor: str
    action: str
    milestone_id: str | None
    from_status: str | None
    to_status: str | None
    detail: dict


def _entries(rows) -> list[AuditEntry]:
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


@router.get("/agreements/{agreement_id}/audit", summary="Full event log for an agreement")
def agreement_audit(agreement_id: str, party: PartyDep, session: SessionDep) -> list[AuditEntry]:
    query = select(AuditEvent).where(AuditEvent.agreement_id == agreement_id).order_by(AuditEvent.at)
    return _entries(session.exec(query).all())


@router.get(
    "/milestones/{milestone_id}/audit",
    openapi_extra={"x-mcp-tool": "explain_milestone"},
    summary="Every event for one tranche: who did what, when, and why",
)
def milestone_audit(milestone_id: str, auth: MilestonePartyDep, session: SessionDep) -> list[AuditEntry]:
    _, m = auth
    query = select(AuditEvent).where(AuditEvent.milestone_id == m.id).order_by(AuditEvent.at)
    return _entries(session.exec(query).all())


@router.get(
    "/agreements/{agreement_id}/compliance-packet",
    openapi_extra={"x-mcp-tool": "export_compliance_packet"},
    summary="Criteria, confirmations, evidence, verdicts with citations, reviews, payments and audit trail",
)
def compliance_packet(
    agreement_id: str, party: PartyDep, session: SessionDep, format: Literal["json", "pdf"] = "json"
) -> Response:
    require_role(party, Role.payer, Role.approver)
    packet = build_packet(session, party.agreement)
    if format == "pdf":
        return Response(
            render_pdf(packet),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="compliance-{agreement_id}.pdf"'},
        )
    return Response(json.dumps(packet, indent=2, default=str), media_type="application/json")
