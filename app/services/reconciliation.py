"""Reconciliation: does PayPal agree with our ledger?

Every PayPal object we hold a reference to is read back and compared with what we recorded: live holds must still
be CREATED, captures COMPLETED (or PENDING while we also say so), invoices PAID or OPEN as we have them, pay-now
orders captured when we say paid, Payouts batches SUCCESS, UNCLAIMED or FAILED as we have them. A disagreement is
a `reconciliation_mismatch` audit event on the tranche (once per object and PayPal status, so a daily run doesn't
repeat itself) and a line in the report. Nothing is changed on either side: the payouts job is what follows PayPal's
asynchronous outcomes; this is the control that says whether the two records agree.

Referenced payouts and refunds have no read-back here yet and are counted as unchecked.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlmodel import Session, col, select

from app.db import record_audit
from app.integrations.paypal import PayPalError, PayPalGateway
from app.models import (
    AuditEvent,
    Disbursement,
    DisbursementKind,
    Milestone,
    MilestoneStatus,
    PayoutRoute,
    Settlement,
    SettlementMode,
    SettlementStatus,
)

# Tranche statuses in which the payer's authorization should still be a live hold at PayPal.
HOLD_LIVE = {
    MilestoneStatus.funded,
    MilestoneStatus.evidence_received,
    MilestoneStatus.verifying,
    MilestoneStatus.needs_review,
    MilestoneStatus.verified,
    MilestoneStatus.overdue,
    MilestoneStatus.disputed,
    MilestoneStatus.settlement_failed,
}

# Our status -> the PayPal statuses that agree with it.
CAPTURE_OK = {
    SettlementStatus.succeeded: {"COMPLETED"},
    SettlementStatus.submitted: {"PENDING", "COMPLETED"},  # COMPLETED: PayPal finished, the payouts job will follow
    SettlementStatus.failed: {"DECLINED", "FAILED"},
}
INVOICE_OK = {
    SettlementStatus.succeeded: {"PAID"},
    SettlementStatus.submitted: {"OPEN", "PAID"},
    SettlementStatus.expired: {"CANCELLED"},
    SettlementStatus.failed: {"CANCELLED", "OPEN"},
}
ORDER_OK = {  # pay-now: "none" means the payer hasn't approved an order yet
    SettlementStatus.succeeded: {"COMPLETED"},
    SettlementStatus.submitted: {"none", "PENDING", "COMPLETED"},
}
PAYOUT_OK = {
    SettlementStatus.succeeded: {"SUCCESS"},
    SettlementStatus.unclaimed: {"UNCLAIMED", "SUCCESS", "FAILED"},  # claimed, or returned after 30 days
    SettlementStatus.submitted: {"PENDING", "SUCCESS", "UNCLAIMED", "FAILED"},
    SettlementStatus.failed: {"FAILED"},
}


@dataclass
class Mismatch:
    agreement_id: str
    milestone_id: str
    object: str  # authorization, capture, invoice, order, payout
    paypal_ref: str
    ours: str
    paypal: str

    def as_dict(self) -> dict:
        return {
            "agreement_id": self.agreement_id,
            "milestone_id": self.milestone_id,
            "object": self.object,
            "paypal_ref": self.paypal_ref,
            "ours": self.ours,
            "paypal": self.paypal,
        }


@dataclass
class ReconciliationReport:
    checked: int = 0
    unchecked: int = 0  # objects with no read-back (referenced payouts, refunds)
    errors: int = 0  # PayPal unreachable or the object gone; try again next run
    mismatches: list[Mismatch] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "checked": self.checked,
            "unchecked": self.unchecked,
            "errors": self.errors,
            "mismatches": [m.as_dict() for m in self.mismatches],
        }


def _note(
    session: Session, report: ReconciliationReport, m: Milestone, obj: str, ref: str, ours: str, theirs: str, actor: str
) -> None:
    report.mismatches.append(Mismatch(m.agreement_id or "", m.id, obj, ref, ours, theirs))
    already = session.exec(
        select(AuditEvent).where(AuditEvent.milestone_id == m.id, AuditEvent.action == "reconciliation_mismatch")
    ).all()
    if any(e.detail.get("paypal_ref") == ref and e.detail.get("paypal") == theirs for e in already):
        return  # reported on an earlier run and not yet resolved
    record_audit(
        session,
        m.agreement_id or "",
        actor,
        "reconciliation_mismatch",
        milestone_id=m.id,
        detail={"object": obj, "paypal_ref": ref, "ours": ours, "paypal": theirs},
    )
    session.commit()


def reconcile(
    session: Session, gw: PayPalGateway, now: datetime, actor: str = "system:reconcile"
) -> ReconciliationReport:
    """Read every referenced PayPal object back and compare. Records mismatches; changes nothing else."""
    report = ReconciliationReport()

    def check(m: Milestone, obj: str, ref: str, ours: str, read: Callable[[str], str], agreeing: set[str]) -> None:
        try:
            theirs = read(ref)
        except PayPalError:
            report.errors += 1
            return
        report.checked += 1
        if theirs not in agreeing:
            _note(session, report, m, obj, ref, ours, theirs, actor)

    def authorization(ref: str) -> str:
        return gw.authorization_status(ref).status

    def capture(ref: str) -> str:
        return gw.capture_status(ref).status

    def order(ref: str) -> str:
        c = gw.order_capture(ref)
        return c.status if c else "none"

    def payout(ref: str) -> str:
        return gw.payout_status(ref).status

    holds = session.exec(
        select(Milestone).where(
            col(Milestone.paypal_authorization_id).is_not(None), col(Milestone.status).in_(tuple(HOLD_LIVE))
        )
    ).all()
    for m in holds:
        check(m, "authorization", m.paypal_authorization_id or "", f"hold ({m.status})", authorization, {"CREATED"})

    settlements = session.exec(select(Settlement).where(col(Settlement.paypal_ref).is_not(None))).all()
    for s in settlements:
        m = s.milestone
        ref = s.paypal_ref or ""
        if s.mode == SettlementMode.escrow_release:
            if ok := CAPTURE_OK.get(s.status):
                check(m, "capture", ref, s.status, capture, ok)
        elif m.payout_route == PayoutRoute.direct:  # pay-now link: the latest order
            if ok := ORDER_OK.get(s.status):
                check(m, "order", ref, s.status, order, ok)
        elif ok := INVOICE_OK.get(s.status):
            check(m, "invoice", ref, s.status, gw.invoice_status, ok)

    disbursements = session.exec(select(Disbursement).where(col(Disbursement.paypal_ref).is_not(None))).all()
    for d in disbursements:
        if d.kind == DisbursementKind.payout:
            if ok := PAYOUT_OK.get(d.status):
                check(d.milestone, "payout", d.paypal_ref or "", d.status, payout, ok)
        else:
            report.unchecked += 1
    return report
