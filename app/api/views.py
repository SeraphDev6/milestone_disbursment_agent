"""Response models shared across endpoints, and the functions that build them from database rows."""

from datetime import date, datetime
from decimal import Decimal
from html import escape

from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import object_session
from sqlmodel import select

from app.db import Agreement, LedgerEntry, Verdict
from app.domain.criteria import criteria_hash, lint_agreement
from app.domain.models import (
    AgreementDraft,
    AgreementStatus,
    Criterion,
    CriterionResult,
    LintReport,
    MilestoneDraft,
    MilestoneStatus,
    PartyIn,
    Role,
    SettlementMode,
)


class PartyView(BaseModel):
    id: str
    role: Role
    display_name: str
    paypal_email: str
    confirmed_current: bool = Field(..., description="Whether this party has signed the current criteria hash.")


class MilestoneView(BaseModel):
    id: str
    position: int
    title: str
    amount: Decimal
    due_date: date | None
    status: MilestoneStatus
    criteria: list[Criterion]
    paypal_authorization_id: str | None = Field(None, description="Live PayPal hold for this tranche, if funded.")
    funded_at: datetime | None = Field(None, description="When the original hold was placed.")


class FundingSummary(BaseModel):
    held: Decimal = Field(..., description="Escrow: authorized on the payer's PayPal account, not yet captured.")
    invoiced: Decimal = Field(..., description="Invoice mode: invoices sent and not yet paid.")
    released: Decimal = Field(..., description="Paid to the payee: captured holds plus paid invoices.")
    unfunded: Decimal = Field(..., description="Total minus held, invoiced and released.")


class AgreementView(BaseModel):
    id: str
    title: str
    total_amount: Decimal
    currency: str
    settlement_mode: SettlementMode
    auto_release_enabled: bool
    auto_release_threshold: float
    status: AgreementStatus
    criteria_hash: str = Field(..., description="Hash of the current tranches and criteria; sign this to confirm.")
    locked: bool = Field(..., description="True once both parties signed and criteria can no longer change.")
    parties: list[PartyView]
    milestones: list[MilestoneView]
    funding: FundingSummary
    lint: LintReport


class VerdictView(BaseModel):
    id: str
    is_precheck: bool
    met: bool = Field(..., description="Every criterion met and cited. Computed by the server, not the model.")
    gate: str = Field(..., description="auto_release, needs_review or precheck.")
    model_confidence: float
    rationale: str
    criteria_results: list[CriterionResult]
    model: str
    prompt_version: str
    created_at: datetime


class EvidenceAccepted(BaseModel):
    evidence_id: str
    duplicate: bool = Field(..., description="True when identical evidence was already on file.")
    milestone_status: MilestoneStatus


def milestone_drafts(row: Agreement) -> list[MilestoneDraft]:
    return [
        MilestoneDraft(title=m.title, amount=m.amount, due_date=m.due_date, criteria=m.criteria) for m in row.milestones
    ]


def agreement_as_draft(row: Agreement) -> AgreementDraft:
    return AgreementDraft(
        title=row.title,
        total_amount=row.total_amount,
        currency=row.currency,
        settlement_mode=SettlementMode(row.settlement_mode),
        auto_release_threshold=row.auto_release_threshold,
        source_document_uri=row.source_document_uri,
        parties=[
            PartyIn(role=Role(p.role), paypal_email=p.paypal_email, display_name=p.display_name) for p in row.parties
        ],
        milestones=milestone_drafts(row),
    )


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


def agreement_view(row: Agreement) -> AgreementView:
    current = criteria_hash(milestone_drafts(row))
    return AgreementView(
        id=row.id,
        title=row.title,
        total_amount=row.total_amount,
        currency=row.currency,
        settlement_mode=SettlementMode(row.settlement_mode),
        auto_release_enabled=row.auto_release_enabled,
        auto_release_threshold=row.auto_release_threshold,
        status=AgreementStatus(row.status),
        criteria_hash=current,
        locked=row.criteria_hash is not None,
        parties=[
            PartyView(
                id=p.id,
                role=Role(p.role),
                display_name=p.display_name,
                paypal_email=p.paypal_email,
                confirmed_current=p.confirmed_criteria_hash == current,
            )
            for p in row.parties
        ],
        milestones=[
            MilestoneView(
                id=m.id,
                position=m.position,
                title=m.title,
                amount=m.amount,
                due_date=m.due_date,
                status=MilestoneStatus(m.status),
                criteria=m.criteria,
                paypal_authorization_id=m.paypal_authorization_id,
                funded_at=m.funded_at,
            )
            for m in row.milestones
        ],
        funding=funding_summary(row),
        lint=lint_agreement(agreement_as_draft(row)),
    )


def verdict_view(v: Verdict) -> VerdictView:
    return VerdictView(
        id=v.id,
        is_precheck=v.is_precheck,
        met=v.met,
        gate=v.gate,
        model_confidence=v.model_confidence,
        rationale=v.rationale,
        criteria_results=v.criteria_results,
        model=v.model,
        prompt_version=v.prompt_version,
        created_at=v.created_at,
    )


def html_page(title: str, message: str, code: int = 200) -> HTMLResponse:
    """Minimal page for browser redirects (PayPal return, mock pages)."""
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{escape(title)}</title>"
        f"<body style='font-family:system-ui;max-width:32rem;margin:4rem auto'>"
        f"<h1>{escape(title)}</h1><p>{escape(message)}</p></body>",
        status_code=code,
    )
