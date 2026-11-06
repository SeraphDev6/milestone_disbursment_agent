"""What the payer side may see of a tranche's evidence before capture (deliverable_release = on_capture).

Until the payer's money is captured, the payer side sees the verdict, not the work: each evidence item carries a
`payer_visibility` level the payee chose (at or above the agreement's minimum), and every payer-side read path
(evidence list, file download, verdict, review outcome, compliance packet, notifications) goes through here. The
payee, reviewers and the verifier always see everything. Audit details and outbound webhook payloads never carry
evidence content, so they need no filtering.

At capture the tranche is revealed (`Milestone.revealed_at`) and the restriction lifts for good.

Which side saw what, and when, is recorded (`record_view`): each side's reads of an on_capture tranche's evidence,
filtered or full, so a reviewer's decision and a chargeback answer can point to what each side had in front of it.
"""

from collections.abc import Iterable
from datetime import timedelta

from sqlmodel import Session, select

from app.db import record_audit, utcnow
from app.domain.criteria import weighted_share
from app.models import (
    AuditEvent,
    CriterionResult,
    DeliverableRelease,
    Evidence,
    EvidenceRead,
    EvidenceVisibility,
    Milestone,
    Role,
    SkepticPass,
    Verdict,
    VerdictRead,
)

WITHHELD_RATIONALE = (
    "Withheld until capture: the payee's evidence opens to the payer side once the tranche is paid for. The "
    "per-criterion results above are what the verifier decided."
)


def withheld(m: Milestone) -> bool:
    """The tranche's evidence is still held back from the payer side."""
    return m.agreement.deliverable_release == DeliverableRelease.on_capture and m.revealed_at is None


def restricted(role: Role | str | None, m: Milestone) -> bool:
    """This caller (by their side's role on the agreement) gets the filtered view of this tranche."""
    return role == Role.payer and withheld(m)


def has_reviewer(m: Milestone) -> bool:
    return any(p.role == Role.reviewer and p.org_id for p in m.agreement.parties)


def reviewer_decides(m: Milestone) -> bool:
    """Only a reviewer's approval releases this tranche from review: the agreement requires it outright, or the
    work is withheld from the payer and the agreement leaves withheld tranches to its reviewer. The payer may
    still reject either way. Falls back to the payer when no reviewer has joined."""
    a = m.agreement
    if not has_reviewer(m):
        return False
    return bool(a.require_reviewer_approval or (a.reviewer_decides_withheld and withheld(m)))


def approver_sides(m: Milestone) -> list[Role]:
    """Who is asked to review this tranche."""
    return [Role.reviewer] if reviewer_decides(m) else [Role.payer, Role.reviewer]


def level_of(m: Milestone, evidence_ids: Iterable[str]) -> EvidenceVisibility:
    """The least disclosed level among the cited evidence: a quote can only be shown if every source allows it."""
    by_id = {e.id: e for e in m.evidence}
    levels = [EvidenceVisibility(by_id[i].payer_visibility) for i in evidence_ids if i in by_id]
    if not levels:
        return EvidenceVisibility(m.agreement.min_evidence_visibility)
    return min(levels, key=lambda v: v.rank)


def redact_result(r: CriterionResult, level: EvidenceVisibility) -> CriterionResult:
    keep_observed = level.rank >= EvidenceVisibility.verdict_only.rank
    keep_quote = level.rank >= EvidenceVisibility.excerpt.rank
    return r.model_copy(
        update={
            "observed": r.observed if keep_observed else None,
            "missing": r.missing if keep_observed else None,
            "quote": r.quote if keep_quote else None,
        }
    )


def _results(m: Milestone, results: list[CriterionResult]) -> list[CriterionResult]:
    return [redact_result(r, level_of(m, r.evidence_ids)) for r in results]


def _rationale(m: Milestone, evidence_ids: list[str], text: str) -> str:
    return text if level_of(m, evidence_ids).rank >= EvidenceVisibility.excerpt.rank else WITHHELD_RATIONALE


def verdict_read(v: Verdict, m: Milestone, role: Role | str | None) -> VerdictRead:
    met = {r.criterion_id for r in v.criteria_results if r.met}
    out = VerdictRead.model_validate(v, update={"weighted_share": weighted_share(m.criteria, met)})
    if not restricted(role, m):
        return out
    skeptic = None
    if v.skeptic is not None:
        s = SkepticPass.model_validate(v.skeptic)
        skeptic = s.model_copy(
            update={"results": _results(m, s.results), "rationale": _rationale(m, v.evidence_ids, s.rationale)}
        )
    return out.model_copy(
        update={
            "criteria_results": _results(m, v.criteria_results),
            "rationale": _rationale(m, v.evidence_ids, v.rationale),
            "skeptic": skeptic,
        }
    )


def missing_for(v: Verdict | None, m: Milestone, role: Role | str | None) -> list[CriterionResult]:
    """Unmet criteria as shown to this caller (review outcome)."""
    unmet = [r for r in (v.criteria_results if v else []) if not r.met]
    return _results(m, unmet) if restricted(role, m) else unmet


def evidence_read(e: Evidence, m: Milestone, role: Role | str | None) -> EvidenceRead:
    return EvidenceRead.from_row(e, withheld=restricted(role, m), now=utcnow())


def rationale_for_payer(m: Milestone, v: Verdict) -> str:
    """The verdict rationale as the payer side may read it (emails)."""
    return _rationale(m, v.evidence_ids, v.rationale) if withheld(m) else v.rationale


VIEW_LOG_EVERY = timedelta(hours=1)  # one evidence_viewed per reader, tranche and view per hour


def record_view(session: Session, m: Milestone, actor: str, role: Role | str | None, evidence_ids: list[str]) -> None:
    """On on_capture agreements, note that this side read the tranche's evidence, and whether it saw the filtered
    (withheld) or the full view. Repeated reads within VIEW_LOG_EVERY that add nothing aren't logged again."""
    if m.agreement.deliverable_release != DeliverableRelease.on_capture or not evidence_ids:
        return
    view = "filtered" if restricted(role, m) else "full"
    recent = session.exec(
        select(AuditEvent).where(
            AuditEvent.milestone_id == m.id,
            AuditEvent.actor == actor,
            AuditEvent.action == "evidence_viewed",
            AuditEvent.at >= utcnow() - VIEW_LOG_EVERY,
        )
    ).all()
    if any(
        e.detail.get("view") == view and set(e.detail.get("evidence_ids") or []) >= set(evidence_ids) for e in recent
    ):
        return
    record_audit(
        session,
        m.agreement_id,
        actor,
        "evidence_viewed",
        milestone_id=m.id,
        detail={"side": str(role) if role else None, "view": view, "evidence_ids": evidence_ids},
    )
    session.commit()
