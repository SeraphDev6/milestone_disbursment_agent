"""Evidence intake, verification and the release gate.

Order of authority for each criterion:
1. verification="check": the deterministic check decides; the model never sees it.
2. verification="model" or "human": the model judges with citations; "human" criteria always send
   the tranche to review.
The overall verdict is computed here (every criterion met and cited), never taken from the model.
"""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, col, select

from app.ai.agents import AIError, AIRuntime
from app.db import record_audit, utcnow
from app.domain import checks
from app.integrations.paypal import PayPalGateway
from app.models import (
    AgreementStatus,
    AuditEvent,
    Criterion,
    CriterionResult,
    DeliverableRelease,
    Evidence,
    EvidenceCreate,
    EvidenceVisibility,
    Milestone,
    MilestoneStatus,
    Role,
    SettlementMode,
    SkepticPass,
    Verdict,
    VerificationMethod,
)
from app.services import ServiceError, disclosure, files, settlement
from app.services.notifications import notify_side, tranche_subject

ACCEPTS_EVIDENCE = {
    MilestoneStatus.pending,
    MilestoneStatus.funded,
    MilestoneStatus.overdue,
    MilestoneStatus.evidence_received,
    MilestoneStatus.needs_review,
    MilestoneStatus.verifying,
}
MAX_RERUNS = 3
PREVIEW_LINK_DAYS = timedelta(days=30)  # how long a payee's preview link is shown, unless they set an expiry


class VerificationError(ServiceError):
    """Same HTTP mapping as escrow errors."""


def attachments_of(rows: list[Evidence]) -> dict[str, tuple[bytes, str]]:
    return {e.id: (files.read(e.file), e.file.media_type) for e in rows if e.file is not None}


# --- judging ---------------------------------------------------------------------------


def judge(
    ai: AIRuntime,
    criteria: list[Criterion],
    evidence: list[Evidence],
    attachments: dict[str, tuple[bytes, str]] | None = None,
) -> Verdict:
    """Decide every criterion and return an unsaved Verdict; the caller sets gate and provenance and stores it."""
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

    ordered = [results[c.id] for c in criteria]  # type: ignore[index]
    return Verdict(
        criteria_results=ordered,
        # Computed here, never taken from the model: every criterion met and cited.
        met=bool(ordered) and all(r.met and r.evidence_ids for r in ordered),
        model_confidence=confidence,
        rationale=rationale,
        model=model,
        prompt_version=VERIFY_PROMPT_VERSION,
        gate="needs_review",
        criteria_hash="",
    )


def _model_judged(m: Milestone) -> list[Criterion]:
    return [c for c in m.criteria if c.verification is VerificationMethod.model]


def _skeptic_required(m: Milestone) -> bool:
    """With the work withheld until capture the payer pays on the verdict alone, so a model-judged auto-release
    needs a second opinion."""
    return m.agreement.deliverable_release == DeliverableRelease.on_capture and bool(_model_judged(m))


def _first_pass_releases(m: Milestone, v: Verdict) -> bool:
    a = m.agreement
    return (
        a.auto_release_enabled
        and v.met
        and not any(c.verification is VerificationMethod.human for c in m.criteria)
        and v.model_confidence >= a.auto_release_threshold
    )


def _gate(m: Milestone, v: Verdict) -> str:
    if not _first_pass_releases(m, v):
        return "needs_review"
    if _skeptic_required(m):
        s = SkepticPass.model_validate(v.skeptic) if v.skeptic is not None else None
        if s is None or s.error or s.disagrees_on:
            return "needs_review"
    return "auto_release"


def skeptic_pass(
    ai: AIRuntime,
    criteria: list[Criterion],
    first: Verdict,
    evidence: list[Evidence],
    attachments: dict[str, tuple[bytes, str]] | None = None,
) -> SkepticPass:
    """Second verifier run on the model-judged criteria, told to look for reasons they are not met. A criterion
    the skeptic finds met must be cited, as in the first pass. A failed run is recorded and fails closed."""
    from app.ai.agents import SKEPTIC_PROMPT_VERSION

    judged = [c for c in criteria if c.verification is VerificationMethod.model]
    try:
        out, aliases = ai.skeptic(judged, evidence, attachments)
    except AIError as e:
        return SkepticPass(
            results=[],
            rationale="",
            confidence=0,
            model=ai.model_name,
            prompt_version=SKEPTIC_PROMPT_VERSION,
            disagrees_on=[c.id or "" for c in judged],
            error=str(e)[:300],
        )
    by_id = {r.criterion_id: r for r in out.results}
    first_met = {r.criterion_id for r in first.criteria_results if r.met}
    results = []
    for c in judged:
        r = by_id.get(c.id or "")
        cited = [aliases[a] for a in (r.evidence_ids if r else []) if a in aliases]
        met = bool(r and r.met and cited)
        results.append(
            CriterionResult(
                criterion_id=c.id or "",
                met=met,
                decided_by="model:skeptic",
                observed=r.observed if r else None,
                evidence_ids=cited,
                quote=(r.quote or None) and r.quote[:300] if r else None,
                missing=None if met else (r.missing if r and r.missing else "The skeptic didn't find this shown."),
            )
        )
    return SkepticPass(
        results=results,
        rationale=out.rationale,
        confidence=out.confidence,
        model=ai.model_name,
        prompt_version=SKEPTIC_PROMPT_VERSION,
        disagrees_on=[r.criterion_id for r in results if not r.met and r.criterion_id in first_met],
    )


def _settle_if_possible(session: Session, gw: PayPalGateway, m: Milestone, actor: str, now: datetime) -> None:
    a = m.agreement
    if a.settlement_mode == SettlementMode.escrow_release and m.paypal_authorization_id is None:
        record_audit(session, a.id, actor, "release_waiting", milestone_id=m.id, detail={"reason": "not funded"})
        session.commit()
        notify_side(
            session,
            m,
            Role.payer,
            tranche_subject(m, "Verified, waiting for funding"),
            f"'{m.title}' met all its acceptance criteria but has no PayPal hold. Fund the tranche to release "
            f"{a.currency} {m.amount} to the payee.",
            tone="review",
        )
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
    ev: EvidenceCreate,
    *,
    source: str,
    actor: str,
    check_results: dict[str, bool] | None = None,
    pinned_ref: str | None = None,
    file_id: str | None = None,
) -> tuple[Evidence, bool]:
    """Store evidence. Returns (row, created). Re-submitting identical evidence is a no-op."""
    if m.agreement.status != AgreementStatus.active:
        raise VerificationError(f"agreement is {m.agreement.status}; evidence is accepted once it is active")
    if m.status not in ACCEPTS_EVIDENCE:
        raise VerificationError(f"tranche is {m.status}; it no longer accepts evidence")
    minimum = EvidenceVisibility(m.agreement.min_evidence_visibility)
    visibility = EvidenceVisibility(ev.payer_visibility or minimum)
    if visibility.rank < minimum.rank:
        raise VerificationError(
            f"this agreement shows the payer at least '{minimum}' of each evidence item before capture; "
            f"'{visibility}' is below that",
            422,
        )
    if ev.preview_url and not ev.preview_url.startswith("https://"):
        raise VerificationError("preview_url must be an https:// link", 422)
    pinned = pinned_ref or "sha256:" + hashlib.sha256(ev.content.encode()).hexdigest()
    external = ev.external_ref or pinned
    existing = next(
        (e for e in m.evidence if (e.source, e.external_ref, e.pinned_ref) == (source, external, pinned)), None
    )
    if existing:
        return existing, False
    row = Evidence.model_validate(
        ev,
        update={
            "milestone_id": m.id,
            "submitted_by": actor,
            "source": source,
            "external_ref": external,
            "pinned_ref": pinned,
            "check_results": check_results or {},
            "file_id": file_id,
            "payer_visibility": visibility,
            "preview_expires_at": ev.preview_expires_at or (utcnow() + PREVIEW_LINK_DAYS if ev.preview_url else None),
        },
    )
    m.evidence.append(row)
    if m.status in (
        MilestoneStatus.pending,
        MilestoneStatus.funded,
        MilestoneStatus.overdue,
        MilestoneStatus.needs_review,
    ):
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
        if m is None or m.status != MilestoneStatus.evidence_received:
            return None
        snapshot = {e.id for e in m.evidence}
        settlement.transition(
            session, m, MilestoneStatus.verifying, actor, "verification_started", evidence_count=len(snapshot)
        )
        session.commit()

        try:
            rows = [e for e in m.evidence if e.id in snapshot]
            verdict = judge(ai, m.criteria, rows, attachments_of(rows))
            if _skeptic_required(m) and _first_pass_releases(m, verdict):  # else a person decides anyway
                verdict.skeptic = skeptic_pass(ai, m.criteria, verdict, rows, attachments_of(rows))
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

        verdict.milestone_id = m.id
        verdict.criteria_hash = m.agreement.criteria_hash or ""
        verdict.evidence_ids = sorted(snapshot)
        verdict.gate = gate = _gate(m, verdict)
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
                confidence=verdict.model_confidence,
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
                met=verdict.met,
                confidence=verdict.model_confidence,
                **_skeptic_detail(verdict),
            )
            session.commit()
            _notify_review_needed(session, m, verdict)
        return verdict
    return None


def _skeptic_detail(v: Verdict) -> dict:
    if v.skeptic is None:
        return {}
    s = SkepticPass.model_validate(v.skeptic)
    return {"skeptic_disagrees_on": s.disagrees_on, "skeptic_error": s.error} if s.disagrees_on or s.error else {}


def _notify_review_needed(session: Session, m: Milestone, verdict: Verdict) -> None:
    subject = tranche_subject(m, "Review needed")
    sides = [Role.reviewer] if m.agreement.require_reviewer_approval else [Role.payer, Role.reviewer]
    for role in sides:
        # The payer side reads what disclosure allows while the work is withheld (deliverable_release=on_capture).
        shown = disclosure.verdict_read(verdict, m, role)
        unmet = [r for r in shown.criteria_results if not r.met]
        summary = (
            f"The verifier found all {len(shown.criteria_results)} criteria met with confidence "
            f"{verdict.model_confidence:.0%}; a person must approve the release."
            if verdict.met
            else f"{len(unmet)} of {len(shown.criteria_results)} criteria were not met: "
            + "; ".join(f"{r.criterion_id}: {r.missing or 'not met'}" for r in unmet[:5])
        )
        body = f"Evidence for '{m.title}' was verified. {summary}\n\nRationale: {shown.rationale}"
        if shown.skeptic and shown.skeptic.disagrees_on:
            body += (
                f"\n\nA second, skeptical check disagreed on {', '.join(shown.skeptic.disagrees_on)}: "
                f"{shown.skeptic.rationale}"
            )
        elif shown.skeptic and shown.skeptic.error:
            body += f"\n\nThe second, skeptical check couldn't run ({shown.skeptic.error}), so a person decides."
        notify_side(session, m, role, subject, body, tone="review")


def mark_overdue(session: Session, now: datetime, actor: str = "system:overdue") -> int:
    """A tranche whose due date passed with no evidence goes `overdue` and both sides hear about it. Run daily.
    Evidence and funding are still accepted; evidence arriving moves it on as usual."""
    today = now.date()
    count = 0
    for m in session.exec(
        select(Milestone).where(
            col(Milestone.status).in_((MilestoneStatus.pending, MilestoneStatus.funded)),
            col(Milestone.due_date).is_not(None),
            col(Milestone.due_date) < today,
        )
    ).all():
        if m.evidence or m.agreement.status != AgreementStatus.active:
            continue
        settlement.transition(session, m, MilestoneStatus.overdue, actor, "overdue", due_date=m.due_date.isoformat())
        session.commit()
        subject = tranche_subject(m, "Overdue")
        notify_side(
            session,
            m,
            Role.payee,
            subject,
            f"'{m.title}' was due on {m.due_date:%B %d, %Y} and no evidence has been submitted. Submit it when "
            "ready; the tranche still accepts evidence.",
            tone="alert",
        )
        notify_side(
            session,
            m,
            Role.payer,
            subject,
            f"'{m.title}' was due on {m.due_date:%B %d, %Y} and the payee hasn't submitted evidence. No money "
            "has moved.",
            tone="alert",
        )
        count += 1
    return count


def verify_in_background(
    factory: sessionmaker, ai: AIRuntime, gw: PayPalGateway, milestone_id: str, actor: str
) -> None:
    """Entry point for background tasks: runs verification in its own session."""
    with factory() as session:
        run_verification(session, ai, gw, milestone_id, actor, utcnow())


def precheck(session: Session, ai: AIRuntime, m: Milestone, items: list[EvidenceCreate], actor: str) -> Verdict:
    """Run checks and the verifier on draft evidence. Records the result; never changes state."""
    if m.agreement.status != AgreementStatus.active:
        raise VerificationError("pre-check is available once the agreement is active")
    # Transient rows: never added to the session, so nothing is stored.
    drafts = [
        Evidence.model_validate(
            ev,
            update={
                "id": f"draft-{i}",
                "milestone_id": m.id,
                "submitted_by": actor,
                "source": "precheck",
                "external_ref": f"draft-{i}",
                "pinned_ref": "draft",
                "payer_visibility": EvidenceVisibility.hash_only,
                "received_at": utcnow(),
            },
        )
        for i, ev in enumerate(items, start=1)
    ]
    stored = list(m.evidence)
    try:
        verdict = judge(ai, m.criteria, stored + drafts, attachments_of(m.evidence))
    except AIError as e:
        raise VerificationError(str(e), 502) from e
    verdict.milestone_id = m.id
    verdict.criteria_hash = m.agreement.criteria_hash or ""
    verdict.evidence_ids = [e.id for e in stored + drafts]
    verdict.is_precheck, verdict.gate = True, "precheck"
    session.add(verdict)
    record_audit(session, m.agreement_id, actor, "precheck", milestone_id=m.id, detail={"met": verdict.met})
    session.commit()
    return verdict


# --- human review ----------------------------------------------------------------------


def latest_verdict(m: Milestone) -> Verdict | None:
    return next((v for v in reversed(m.verdicts) if not v.is_precheck), None)


def review(
    session: Session,
    gw: PayPalGateway,
    m: Milestone,
    actor: str,
    approve: bool,
    note: str | None,
    now: datetime,
) -> None:
    if m.status not in (MilestoneStatus.needs_review, MilestoneStatus.disputed):
        raise VerificationError(f"tranche is {m.status}; only tranches in needs_review or disputed can be reviewed")
    verdict = latest_verdict(m)
    detail = {"note": note, "verdict_id": verdict.id if verdict else None}
    if approve:
        settlement.transition(session, m, MilestoneStatus.verified, actor, "review_approved", **detail)
        session.commit()
        _settle_if_possible(session, gw, m, actor, now)
    else:
        back = MilestoneStatus.funded if m.paypal_authorization_id else MilestoneStatus.pending
        unmet = [r for r in (verdict.criteria_results if verdict else []) if not r.met]
        settlement.transition(
            session,
            m,
            back,
            actor,
            "review_rejected",
            **detail,
            missing=[{"criterion_id": r.criterion_id, "missing": r.missing} for r in unmet],
        )
        session.commit()
        missing = (
            "\n".join(f"- {r.criterion_id}: {r.missing or 'not met'}" for r in unmet) or "- (see the reviewer's note)"
        )
        notify_side(
            session,
            m,
            Role.payee,
            tranche_subject(m, "Evidence rejected"),
            f"The evidence for '{m.title}' was not accepted."
            + (f" Note from the reviewer: {note}" if note else "")
            + f"\n\nWhat's missing:\n{missing}\n\nSubmit the missing evidence and the tranche is verified again.",
            tone="alert",
        )


def dispute(session: Session, m: Milestone, actor: str, reason: str) -> None:
    """Payee escalates a rejection. The tranche waits in `disputed` until the payer side reviews it again."""
    if m.status not in (MilestoneStatus.pending, MilestoneStatus.funded):
        raise VerificationError(f"tranche is {m.status}; only a rejected tranche can be disputed")
    last_review = session.exec(
        select(AuditEvent)
        .where(AuditEvent.milestone_id == m.id, col(AuditEvent.action).in_(("review_rejected", "review_approved")))
        .order_by(col(AuditEvent.at).desc())
        .limit(1)
    ).first()
    if last_review is None or last_review.action != "review_rejected":
        raise VerificationError("only a rejected tranche can be disputed")
    settlement.transition(session, m, MilestoneStatus.disputed, actor, "disputed", reason=reason)
    session.commit()
