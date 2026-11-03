"""Evidence intake, verification and the release gate.

Order of authority for each criterion:
1. verification="check": the deterministic check decides; the model never sees it.
2. verification="model" or "human": the model judges with citations; "human" criteria always send
   the tranche to review.
The overall verdict is computed here (every criterion met and cited), never taken from the model.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, col, select

from app.ai.agents import AIError, AIRuntime
from app.db import AuditEvent, Evidence, Milestone, Party, Verdict, as_utc, record_audit, utcnow
from app.domain import checks
from app.domain.models import (
    AgreementStatus,
    Criterion,
    CriterionResult,
    EvidenceEvent,
    EvidenceIn,
    MilestoneStatus,
    SettlementMode,
    VerificationMethod,
)
from app.integrations.paypal import PayPalGateway
from app.services import ServiceError, settlement

ACCEPTS_EVIDENCE = {
    MilestoneStatus.pending.value,
    MilestoneStatus.funded.value,
    MilestoneStatus.evidence_received.value,
    MilestoneStatus.needs_review.value,
    MilestoneStatus.verifying.value,
}
MAX_RERUNS = 3


class VerificationError(ServiceError):
    """Same HTTP mapping as escrow errors."""


@dataclass
class Judgment:
    results: list[CriterionResult]
    confidence: float
    rationale: str
    model: str
    prompt_version: str

    @property
    def met(self) -> bool:
        return bool(self.results) and all(r.met and r.evidence_ids for r in self.results)


def criteria_of(m: Milestone) -> list[Criterion]:
    return [Criterion.model_validate(c) for c in m.criteria]


def to_event(row: Evidence) -> EvidenceEvent:
    return EvidenceEvent(
        id=row.id,
        milestone_id=row.milestone_id,
        source=row.source,
        external_ref=row.external_ref,
        pinned_ref=row.pinned_ref,
        evidence_type=row.evidence_type,
        title=row.title,
        content=row.content,
        dated=row.dated,
        artifact_uris=row.artifact_uris,
        check_results=row.check_results,
        received_at=as_utc(row.received_at),
    )


def attachments_of(rows: list[Evidence]) -> dict[str, tuple[bytes, str]]:
    return {e.id: (e.file.data, e.file.media_type) for e in rows if e.file is not None}


# --- judging ---------------------------------------------------------------------------


def judge(
    ai: AIRuntime,
    criteria: list[Criterion],
    evidence: list[EvidenceEvent],
    attachments: dict[str, tuple[bytes, str]] | None = None,
) -> Judgment:
    from app.ai.agents import VERIFY_PROMPT_VERSION

    results: dict[str, CriterionResult] = {}
    for c in criteria:
        if c.verification is VerificationMethod.check:
            results[c.id] = checks.evaluate(c, evidence)  # type: ignore[index]

    judged = [c for c in criteria if c.verification is not VerificationMethod.check]
    confidence, rationale, model = 1.0, "All criteria were decided by deterministic checks.", "checks"
    if judged and not evidence:
        for c in judged:
            results[c.id] = CriterionResult(  # type: ignore[index]
                criterion_id=c.id or "",
                met=False,
                decided_by="model",
                evidence_ids=[],
                missing=f"Submit {c.evidence_description}.",
            )
        rationale = "No evidence has been submitted."
    elif judged:
        out, aliases = ai.verify(judged, evidence, attachments)
        by_id = {r.criterion_id: r for r in out.results}
        for c in judged:
            r = by_id.get(c.id or "")
            if r is None:
                results[c.id] = CriterionResult(  # type: ignore[index]
                    criterion_id=c.id or "",
                    met=False,
                    decided_by="model",
                    evidence_ids=[],
                    missing="The verifier returned no judgment for this criterion; a reviewer must decide.",
                )
                continue
            cited = [aliases[a] for a in r.evidence_ids if a in aliases]
            met = r.met and bool(cited)
            missing = r.missing if not met else None
            if r.met and not cited:
                missing = "The verifier said this was met but cited no evidence, so it counts as not met."
            results[c.id] = CriterionResult(  # type: ignore[index]
                criterion_id=c.id or "",
                met=met,
                decided_by="model",
                observed=r.observed,
                evidence_ids=cited,
                quote=(r.quote or None) and r.quote[:300],
                missing=missing,
            )
        confidence, rationale, model = out.confidence, out.rationale, ai.model_name

    return Judgment(
        results=[results[c.id] for c in criteria],  # type: ignore[index]
        confidence=confidence,
        rationale=rationale,
        model=model,
        prompt_version=VERIFY_PROMPT_VERSION,
    )


def _gate(m: Milestone, criteria: list[Criterion], j: Judgment) -> str:
    a = m.agreement
    if (
        a.auto_release_enabled
        and j.met
        and not any(c.verification is VerificationMethod.human for c in criteria)
        and j.confidence >= a.auto_release_threshold
    ):
        return "auto_release"
    return "needs_review"


def _settle_if_possible(session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime) -> None:
    a = m.agreement
    if a.settlement_mode == SettlementMode.escrow_release.value and m.paypal_authorization_id is None:
        record_audit(session, a.id, actor, "release_waiting", milestone_id=m.id, detail={"reason": "not funded"})
        session.commit()
        return
    try:
        settlement.release(session, gw, m, actor, now)
    except ServiceError as e:
        record_audit(session, a.id, actor, "release_failed", milestone_id=m.id, detail={"error": e.message})
        session.commit()


# --- intake ----------------------------------------------------------------------------


def submit_evidence(
    session: Session,
    m: Milestone,
    ev: EvidenceIn,
    *,
    source: str,
    actor: str,
    check_results: dict[str, bool] | None = None,
    pinned_ref: str | None = None,
    file_id: str | None = None,
) -> tuple[Evidence, bool]:
    """Store evidence. Returns (row, created). Re-submitting identical evidence is a no-op."""
    if m.agreement.status != AgreementStatus.active.value:
        raise VerificationError(f"agreement is {m.agreement.status}; evidence is accepted once it is active")
    if m.status not in ACCEPTS_EVIDENCE:
        raise VerificationError(f"tranche is {m.status}; it no longer accepts evidence")
    pinned = pinned_ref or "sha256:" + hashlib.sha256(ev.content.encode()).hexdigest()
    external = ev.external_ref or pinned
    existing = next(
        (e for e in m.evidence if (e.source, e.external_ref, e.pinned_ref) == (source, external, pinned)), None
    )
    if existing:
        return existing, False
    row = Evidence(
        milestone_id=m.id,
        submitted_by=actor,
        source=source,
        external_ref=external,
        pinned_ref=pinned,
        evidence_type=ev.evidence_type.value,
        title=ev.title,
        content=ev.content,
        dated=ev.dated,
        artifact_uris=ev.artifact_uris,
        check_results=check_results or {},
        file_id=file_id,
    )
    m.evidence.append(row)
    if m.status in (MilestoneStatus.pending.value, MilestoneStatus.funded.value, MilestoneStatus.needs_review.value):
        settlement.transition(
            session,
            m,
            MilestoneStatus.evidence_received,
            actor,
            "evidence_received",
            evidence_id=row.id,
            title=ev.title,
        )
    else:
        record_audit(session, m.agreement_id, actor, "evidence_received", milestone_id=m.id, detail={"title": ev.title})
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise VerificationError("this evidence was already submitted", 409) from None
    return row, True


# --- verification run ------------------------------------------------------------------


def run_verification(
    session: Session, ai: AIRuntime, gw: PayPalGateway, milestone_id: str, actor: str, now: datetime
) -> Verdict | None:
    """Verify a tranche against all its evidence, record the verdict, apply the gate.

    Safe to call from a background task. If evidence arrives while the model is working, it runs again.
    """
    for _ in range(MAX_RERUNS):
        m = session.get(Milestone, milestone_id)
        if m is None or m.status != MilestoneStatus.evidence_received.value:
            return None
        snapshot = {e.id for e in m.evidence}
        settlement.transition(
            session, m, MilestoneStatus.verifying, actor, "verification_started", evidence_count=len(snapshot)
        )
        session.commit()

        criteria = criteria_of(m)
        try:
            rows = [e for e in m.evidence if e.id in snapshot]
            j = judge(ai, criteria, [to_event(e) for e in rows], attachments_of(rows))
        except AIError as e:
            settlement.transition(
                session, m, MilestoneStatus.evidence_received, actor, "verification_failed", error=str(e)[:500]
            )
            session.commit()
            return None

        session.refresh(m)
        if {e.id for e in m.evidence} - snapshot:
            settlement.transition(session, m, MilestoneStatus.evidence_received, actor, "verification_superseded")
            session.commit()
            continue

        gate = _gate(m, criteria, j)
        verdict = Verdict(
            milestone_id=m.id,
            criteria_hash=m.agreement.criteria_hash or "",
            evidence_ids=sorted(snapshot),
            criteria_results=[r.model_dump(mode="json") for r in j.results],
            met=j.met,
            model_confidence=j.confidence,
            rationale=j.rationale,
            model=j.model,
            prompt_version=j.prompt_version,
            is_precheck=False,
            gate=gate,
        )
        session.add(verdict)
        session.flush()
        if gate == "auto_release":
            settlement.transition(
                session,
                m,
                MilestoneStatus.verified,
                "system:gate",
                "auto_verified",
                verdict_id=verdict.id,
                confidence=j.confidence,
            )
            session.commit()
            _settle_if_possible(session, gw, m, "system:gate", now)
        else:
            settlement.transition(
                session,
                m,
                MilestoneStatus.needs_review,
                "system:gate",
                "sent_to_review",
                verdict_id=verdict.id,
                met=j.met,
                confidence=j.confidence,
            )
            session.commit()
        return verdict
    return None


def verify_in_background(
    factory: sessionmaker, ai: AIRuntime, gw: PayPalGateway, milestone_id: str, actor: str
) -> None:
    """Entry point for background tasks: runs verification in its own session."""
    with factory() as session:
        run_verification(session, ai, gw, milestone_id, actor, utcnow())


def precheck(session: Session, ai: AIRuntime, m: Milestone, items: list[EvidenceIn], actor: str) -> Verdict:
    """Run checks and the verifier on draft evidence. Records the result; never changes state."""
    if m.agreement.status != AgreementStatus.active.value:
        raise VerificationError("pre-check is available once the agreement is active")
    drafts = [
        EvidenceEvent(
            id=f"draft-{i}",
            milestone_id=m.id,
            source="precheck",
            external_ref=f"draft-{i}",
            pinned_ref="draft",
            evidence_type=ev.evidence_type,
            title=ev.title,
            content=ev.content,
            dated=ev.dated,
            artifact_uris=ev.artifact_uris,
            received_at=utcnow(),
        )
        for i, ev in enumerate(items, start=1)
    ]
    stored = [to_event(e) for e in m.evidence]
    try:
        j = judge(ai, criteria_of(m), stored + drafts, attachments_of(m.evidence))
    except AIError as e:
        raise VerificationError(str(e), 502) from e
    verdict = Verdict(
        milestone_id=m.id,
        criteria_hash=m.agreement.criteria_hash or "",
        evidence_ids=[e.id for e in stored + drafts],
        criteria_results=[r.model_dump(mode="json") for r in j.results],
        met=j.met,
        model_confidence=j.confidence,
        rationale=j.rationale,
        model=j.model,
        prompt_version=j.prompt_version,
        is_precheck=True,
        gate="precheck",
    )
    session.add(verdict)
    record_audit(session, m.agreement_id, actor, "precheck", milestone_id=m.id, detail={"met": j.met})
    session.commit()
    return verdict


# --- human review ----------------------------------------------------------------------


def latest_verdict(m: Milestone) -> Verdict | None:
    return next((v for v in reversed(m.verdicts) if not v.is_precheck), None)


def review(
    session: Session,
    gw: PayPalGateway,
    m: Milestone,
    party: Party,
    approve: bool,
    note: str | None,
    now: datetime,
) -> None:
    if m.status not in (MilestoneStatus.needs_review.value, MilestoneStatus.disputed.value):
        raise VerificationError(f"tranche is {m.status}; only tranches in needs_review or disputed can be reviewed")
    actor = f"party:{party.id}"
    verdict = latest_verdict(m)
    detail = {"note": note, "verdict_id": verdict.id if verdict else None}
    if approve:
        settlement.transition(session, m, MilestoneStatus.verified, actor, "review_approved", **detail)
        session.commit()
        _settle_if_possible(session, gw, m, actor, now)
    else:
        back = MilestoneStatus.funded if m.paypal_authorization_id else MilestoneStatus.pending
        unmet = [r for r in (verdict.criteria_results if verdict else []) if not r["met"]]
        settlement.transition(
            session,
            m,
            back,
            actor,
            "review_rejected",
            **detail,
            missing=[{"criterion_id": r["criterion_id"], "missing": r["missing"]} for r in unmet],
        )
        session.commit()


def dispute(session: Session, m: Milestone, party: Party, reason: str) -> None:
    """Payee escalates a rejection. The tranche waits in `disputed` until the payer side reviews it again."""
    if m.status not in (MilestoneStatus.pending.value, MilestoneStatus.funded.value):
        raise VerificationError(f"tranche is {m.status}; only a rejected tranche can be disputed")
    last_review = session.exec(
        select(AuditEvent)
        .where(AuditEvent.milestone_id == m.id, col(AuditEvent.action).in_(("review_rejected", "review_approved")))
        .order_by(col(AuditEvent.at).desc())
        .limit(1)
    ).first()
    if last_review is None or last_review.action != "review_rejected":
        raise VerificationError("only a rejected tranche can be disputed")
    settlement.transition(session, m, MilestoneStatus.disputed, f"party:{party.id}", "disputed", reason=reason)
    session.commit()
