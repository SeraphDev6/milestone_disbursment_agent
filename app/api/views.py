"""Building API responses from rows. The response models themselves live in app.models."""

from decimal import Decimal
from html import escape

from fastapi.responses import HTMLResponse
from sqlalchemy.orm import object_session
from sqlmodel import Field, SQLModel, select

from app.domain.criteria import criteria_hash, lint_agreement
from app.models import Agreement, AgreementRead, FundingSummary, LedgerEntry, MilestoneRead, MilestoneStatus, PartyRead


class EvidenceAccepted(SQLModel):
    """Result of an intake call (not an entity)."""

    evidence_id: str
    duplicate: bool = Field(description="True when identical evidence was already on file.")
    milestone_status: MilestoneStatus


def funding_summary(row: Agreement) -> FundingSummary:
    session = object_session(row)
    entries = session.exec(select(LedgerEntry).where(LedgerEntry.agreement_id == row.id)).all() if session else []
    by_kind: dict[str, Decimal] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, Decimal(0)) + e.amount

    def k(kind: str) -> Decimal:
        return by_kind.get(kind, Decimal(0))

    held = k("authorized") - k("captured") - k("voided") - k("expired")
    invoiced = k("invoiced") - k("paid") - k("invoice_cancelled")
    released = k("captured") + k("paid")
    cents = Decimal("0.01")
    return FundingSummary(
        held=held.quantize(cents),
        invoiced=invoiced.quantize(cents),
        released=released.quantize(cents),
        unfunded=(row.total_amount - held - invoiced - released).quantize(cents),
    )


def _pending(row: Agreement) -> list[str]:
    from app.services.identity import pending_agreement_invitations

    session = object_session(row)
    return [i.email for i in pending_agreement_invitations(session, row.id)] if session else []


def agreement_read(row: Agreement) -> AgreementRead:
    current = criteria_hash(row.milestones)
    return AgreementRead.model_validate(
        row,
        update={
            "criteria_hash": current,
            "locked": row.criteria_hash is not None,
            "parties": [
                PartyRead.model_validate(p, update={"confirmed_current": p.confirmed_criteria_hash == current})
                for p in row.parties
            ],
            "milestones": [MilestoneRead.model_validate(m) for m in row.milestones],
            "funding": funding_summary(row),
            "lint": lint_agreement(row),
            "pending_invitations": _pending(row),
        },
    )


def html_page(title: str, message: str, code: int = 200) -> HTMLResponse:
    """Minimal page for browser redirects (PayPal return, mock pages)."""
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p></body>",
        status_code=code,
    )
