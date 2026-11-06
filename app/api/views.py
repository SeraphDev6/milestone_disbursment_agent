"""Building API responses from rows. The response models themselves live in app.models."""

from decimal import Decimal
from html import escape

from fastapi.responses import HTMLResponse
from sqlalchemy.orm import object_session
from sqlmodel import Field, SQLModel, select

from app.domain.criteria import criteria_hash, lint_agreement
from app.models import (
    Agreement,
    AgreementRead,
    FundingSummary,
    InvitationKind,
    LedgerEntry,
    MilestoneRead,
    MilestoneStatus,
    PartyRead,
    Role,
)


class EvidenceAccepted(SQLModel):
    """Result of an intake call (not an entity)."""

    evidence_id: str = Field(description="The stored evidence item (the existing one when duplicate).")
    duplicate: bool = Field(description="True when identical evidence was already on file.")
    milestone_status: MilestoneStatus = Field(description="The tranche's status after intake, before verification.")


def funding_summary(row: Agreement) -> FundingSummary:
    session = object_session(row)
    entries = session.exec(select(LedgerEntry).where(LedgerEntry.agreement_id == row.id)).all() if session else []
    by_kind: dict[str, Decimal] = {}
    for e in entries:
        by_kind[e.kind] = by_kind.get(e.kind, Decimal(0)) + e.amount

    def k(kind: str) -> Decimal:
        return by_kind.get(kind, Decimal(0))

    held = k("authorized") - k("captured") - k("voided") - k("expired")
    in_window = k("captured") + k("collected") - k("disbursed") - k("refunded")
    invoiced = k("invoiced") - k("paid") - k("collected") - k("invoice_cancelled")
    released = k("disbursed") + k("paid")
    cents = Decimal("0.01")
    return FundingSummary(
        held=held.quantize(cents),
        in_dispute_window=in_window.quantize(cents),
        invoiced=invoiced.quantize(cents),
        released=released.quantize(cents),
        refunded=k("refunded").quantize(cents),
        unfunded=(row.total_amount - held - in_window - invoiced - released).quantize(cents),
    )


def pay_path(s) -> str | None:
    from app.services.settlement import pay_path as path

    return path(s)


def _money_so_far(m) -> dict:
    from app.services.settlement import paid_criteria, refunded, released

    return {"amount_released": released(m) - refunded(m), "criteria_paid": sorted(paid_criteria(m))}


def _awaiting_approval(m) -> bool:
    """Funding started (an order exists) but the payer hasn't approved it in PayPal yet."""
    return (
        m.status in (MilestoneStatus.pending, MilestoneStatus.overdue)
        and m.paypal_order_id is not None
        and m.paypal_authorization_id is None
    )


def _refund_failed(m) -> bool:
    from app.models import DisbursementKind, SettlementStatus

    if m.status != MilestoneStatus.contested:
        return False
    last = next((d for d in reversed(m.disbursements) if d.kind == DisbursementKind.refund), None)
    return last is not None and last.status == SettlementStatus.failed


def _paypal_connected(party) -> bool | None:
    from app.services.payee_accounts import account_for

    if party.role != Role.payee:
        return None
    session = object_session(party)
    account = account_for(session, party.org_id) if session else None
    return account is not None and account.ready


def _pending(row: Agreement) -> list[str]:
    from app.services.identity import pending_agreement_invitations

    session = object_session(row)
    return [i.email for i in pending_agreement_invitations(session, row.id)] if session else []


def _reviewer_invited(row: Agreement) -> bool:
    from app.services.identity import pending_agreement_invitations

    session = object_session(row)
    pending = pending_agreement_invitations(session, row.id) if session else []
    return any(i.kind == InvitationKind.reviewer for i in pending)


def agreement_read(row: Agreement) -> AgreementRead:
    current = criteria_hash(row.milestones, row.deliverable_release, row.min_evidence_visibility, row.partial_release)
    return AgreementRead.model_validate(
        row,
        update={
            "criteria_hash": current,
            "locked": row.criteria_hash is not None,
            "parties": [
                PartyRead.model_validate(
                    p,
                    update={
                        "confirmed_current": p.confirmed_criteria_hash == current,
                        "paypal_connected": _paypal_connected(p),
                    },
                )
                for p in row.parties
            ],
            "milestones": [
                MilestoneRead.model_validate(
                    m,
                    update={
                        "pay_path": pay_path(m.settlements[-1]) if m.settlements else None,
                        "refund_failed": _refund_failed(m),
                        "awaiting_approval": _awaiting_approval(m),
                        **_money_so_far(m),
                    },
                )
                for m in row.milestones
            ],
            "funding": funding_summary(row),
            "lint": lint_agreement(row, reviewer_invited=_reviewer_invited(row)),
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
