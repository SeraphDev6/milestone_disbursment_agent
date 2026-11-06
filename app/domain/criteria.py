"""Criteria lint and hashing.

The lint is deterministic on purpose: it is the floor every tranche must clear before both parties
can confirm, whether criteria were typed by a person or proposed by the extraction model. The
extraction model gets the same rules in its prompt and its output runs through this same lint.

Errors block confirmation. Warnings are shown but do not block.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from decimal import Decimal

from app.domain.checks import CHECKS
from app.models import (
    AgreementBase,
    Comparator,
    Criterion,
    DeliverableRelease,
    EvidenceVisibility,
    LintIssue,
    LintLevel,
    LintReport,
    MilestoneBase,
    Role,
    SettlementMode,
    VerificationMethod,
)

MAX_CRITERIA_PER_MILESTONE = 7
MIN_STATEMENT_WORDS = 4
MIN_EVIDENCE_WORDS = 3

# Phrases that hand the definition of "done" to someone's later opinion or to another document.
DEFERRED_PHRASES = [
    "does the thing",
    "as agreed",
    "as discussed",
    "per the sow",
    "per the agreement",
    "per the contract",
    "per the proposal",
    "to the satisfaction of",
    "to client satisfaction",
    "to funder satisfaction",
    "satisfactory to",
    "work is done",
    "work completed",
    "work is complete",
    "deliverables delivered",
    "milestone complete",
    "milestone completed",
    "tbd",
    "to be determined",
]

# Words a reviewer can't count or check. Each needs to become a number, a named artifact or a date.
VAGUE_TERMS = [
    "high quality",
    "high-quality",
    "meaningful",
    "significant",
    "significantly",
    "substantial",
    "successful",
    "successfully",
    "engaging",
    "robust",
    "appropriate",
    "appropriately",
    "adequate",
    "adequately",
    "sufficient",
    "reasonable",
    "best effort",
    "as needed",
    "user-friendly",
    "effective",
    "effectively",
    "impactful",
    "improve",
    "improved",
    "enhance",
    "enhanced",
    "various",
    "etc",
    "and/or",
    "good",
    "great",
    "better",
    "timely",
    "properly",
    "fully functional",
    "polished",
    "professional",
]

_DIGIT = re.compile(r"\d")


def _find_phrase(text: str, phrases: Sequence[str]) -> str | None:
    lowered = text.lower()
    for phrase in phrases:
        if re.search(rf"(?<![\w-]){re.escape(phrase)}(?![\w-])", lowered):
            return phrase
    return None


def _issue(level: LintLevel, code: str, path: str, message: str, suggestion: str | None = None) -> LintIssue:
    return LintIssue(level=level, code=code, path=path, message=message, suggestion=suggestion)


def lint_criterion(c: Criterion, path: str, milestone: MilestoneBase) -> list[LintIssue]:
    E, W = LintLevel.error, LintLevel.warning
    issues: list[LintIssue] = []
    statement = c.statement.strip()

    if len(statement.split()) < MIN_STATEMENT_WORDS:
        issues.append(
            _issue(
                E,
                "too_short",
                f"{path}.statement",
                f"'{statement}' is too short to verify.",
                "State who delivers what, how much, and how it is shown, e.g. "
                "'At least 200 people attend the spring workshops, shown on signed sign-in sheets'.",
            )
        )

    if phrase := _find_phrase(statement, DEFERRED_PHRASES):
        issues.append(
            _issue(
                E,
                "deferred_definition",
                f"{path}.statement",
                f"'{phrase}' leaves the definition of done to someone's later judgment or another document.",
                "Write the actual condition here: the deliverable, the amount or threshold, and the evidence.",
            )
        )

    if term := _find_phrase(statement, VAGUE_TERMS):
        has_number = bool(_DIGIT.search(statement))
        issues.append(
            _issue(
                W if has_number else E,
                "vague_term",
                f"{path}.statement",
                f"'{term}' can't be counted or checked by a reviewer.",
                f"Replace '{term}' with the measure itself"
                f" ({c.measure.metric} {c.measure.comparator.replace('_', ' ')}"
                f"{' ' + str(c.measure.target) if c.measure.target is not None else ''}"
                f"{' ' + c.measure.unit if c.measure.unit else ''}).",
            )
        )

    if re.search(r";|\band also\b|\bas well as\b", statement) or statement.lower().count(" and ") >= 2:
        issues.append(
            _issue(
                W,
                "compound",
                f"{path}.statement",
                "This may combine several conditions.",
                "Split it so each condition gets its own pass/fail result and citation.",
            )
        )

    if len(c.evidence_description.split()) < MIN_EVIDENCE_WORDS:
        issues.append(
            _issue(
                E,
                "evidence_unspecific",
                f"{path}.evidence_description",
                f"'{c.evidence_description}' doesn't name a specific artifact.",
                "Name the document or file and what it must show, e.g. 'Q2 budget sheet with actuals per line'.",
            )
        )

    if c.verification is VerificationMethod.check:
        spec = CHECKS.get(c.check_id or "")
        if not c.check_id:
            issues.append(
                _issue(
                    E,
                    "check_missing",
                    f"{path}.check_id",
                    "verification is 'check' but no check_id is set.",
                    f"Pick one of: {', '.join(sorted(CHECKS))}.",
                )
            )
        elif spec is None:
            issues.append(
                _issue(
                    E,
                    "check_unknown",
                    f"{path}.check_id",
                    f"Unknown check '{c.check_id}'.",
                    f"Pick one of: {', '.join(sorted(CHECKS))}.",
                )
            )
        else:
            if not spec.evidence.intersection(c.evidence_required):
                issues.append(
                    _issue(
                        E,
                        "check_evidence_mismatch",
                        f"{path}.evidence_required",
                        f"Check '{c.check_id}' can't run on {', '.join(e for e in c.evidence_required)}.",
                        f"Require one of: {', '.join(sorted(e for e in spec.evidence))}.",
                    )
                )
            if c.measure.comparator not in spec.comparators:
                issues.append(
                    _issue(
                        E,
                        "check_comparator_mismatch",
                        f"{path}.measure.comparator",
                        f"Check '{c.check_id}' can't evaluate '{c.measure.comparator}'.",
                        f"Use one of: {', '.join(sorted(x for x in spec.comparators))}.",
                    )
                )
    elif c.check_id:
        issues.append(
            _issue(
                W,
                "check_ignored",
                f"{path}.check_id",
                f"check_id is set but verification is '{c.verification}', so the check won't run.",
                "Set verification to 'check' or remove check_id.",
            )
        )

    if c.verification is VerificationMethod.human:
        issues.append(
            _issue(
                W,
                "human_only",
                f"{path}.verification",
                "This criterion always goes to a person, so this tranche can never auto-release.",
            )
        )

    if c.due_by and milestone.due_date and c.due_by > milestone.due_date:
        issues.append(
            _issue(
                W,
                "criterion_after_milestone",
                f"{path}.due_by",
                "This criterion is due after its milestone's due date.",
            )
        )

    if (
        c.measure.comparator is Comparator.on_or_before and milestone.due_date and c.measure.target > milestone.due_date  # type: ignore[operator]  # validated as date
    ):
        issues.append(
            _issue(
                W,
                "target_after_milestone",
                f"{path}.measure.target",
                "The target date is after the milestone's due date.",
            )
        )

    return issues


def lint_agreement(agreement: AgreementBase, *, reviewer_invited: bool = False) -> LintReport:
    """Works on a request body (AgreementCreate) or a stored Agreement: both carry total_amount and milestones.
    `reviewer_invited`: a reviewer is invited but hasn't joined yet (stored agreements only)."""
    report = lint_tranches(agreement.total_amount, agreement.milestones)  # type: ignore[attr-defined]
    on_capture = getattr(agreement, "deliverable_release", None) == DeliverableRelease.on_capture
    if on_capture and getattr(agreement, "settlement_mode", None) == SettlementMode.escrow_release:
        if getattr(agreement, "dispute_window_days", 0) < 1:
            report.issues.insert(
                0,
                _issue(
                    LintLevel.error,
                    "reveal_without_window",
                    "dispute_window_days",
                    "The payer sees the work only once it's paid for, and with no dispute window the payee is "
                    "paid at the same moment: the payer has no chance to check what they paid for.",
                    "Set a dispute window of at least 1 day, or release deliverables on submit.",
                ),
            )
            report.ok = False
    elif on_capture and getattr(agreement, "settlement_mode", None) == SettlementMode.invoice_on_verify:
        report.issues.append(
            _issue(
                LintLevel.warning,
                "reveal_after_payment",
                "deliverable_release",
                "Invoice mode has no dispute window: the payer sees the work once the invoice is paid, and the "
                "payment can't be contested in the engine after that.",
                "Use escrow with a dispute window if the payer should be able to check the work before the payee "
                "is paid.",
            )
        )
    reviewers = getattr(agreement, "reviewer_emails", None) or [
        p for p in getattr(agreement, "parties", []) if p.role == Role.reviewer
    ]
    reviewer_decides = getattr(agreement, "require_reviewer_approval", False) or getattr(
        agreement, "reviewer_decides_withheld", True
    )
    if on_capture and (reviewers or reviewer_invited) and not reviewer_decides:
        report.issues.append(
            _issue(
                LintLevel.warning,
                "payer_reviews_unseen_work",
                "reviewer_decides_withheld",
                "The payer can't see the work until it's paid for, but a tranche in review can be approved by the "
                "payer, who would be judging work they haven't seen.",
                "Let the reviewer decide withheld tranches: they see all the evidence, and the payer can still reject.",
            )
        )
    if on_capture and getattr(agreement, "auto_release_enabled", False) and not reviewers and not reviewer_invited:
        report.issues.append(
            _issue(
                LintLevel.warning,
                "pays_on_model_alone",
                "deliverable_release",
                "Auto-release is on, the payer can't see the work until it's paid for, and there is no "
                "reviewer: the payer would pay on the model's verdict alone.",
                "Invite a reviewer, turn off auto-release, or release deliverables on submit.",
            )
        )
    return report


def weighted_share(criteria: Sequence[Criterion], met_ids: set[str]) -> float:
    """The share of a tranche its met criteria account for, by weight (1.0 when every criterion is met). What a
    partial release would pay; today's release is still all-or-nothing, so this is reported, not paid."""
    total = sum((Decimal(str(c.weight)) for c in criteria), start=Decimal(0))
    if total <= 0:
        return 0.0
    met = sum((Decimal(str(c.weight)) for c in criteria if c.id in met_ids), start=Decimal(0))
    return float(round(met / total, 4))


def lint_tranches(total_amount: Decimal, milestones: Sequence[MilestoneBase]) -> LintReport:
    """Lint tranches on their own, e.g. extraction output before parties are known."""
    E, W = LintLevel.error, LintLevel.warning
    issues: list[LintIssue] = []

    for mi, m in enumerate(milestones):
        weights = {Decimal(str(c.weight)) for c in m.criteria}
        if len(weights) > 1:
            issues.append(
                _issue(
                    W,
                    "weights_not_yet_paid",
                    f"milestones[{mi}].criteria",
                    "Criteria carry different weights, but release is all-or-nothing: a tranche pays in full when "
                    "every criterion is met, and nothing before. The weights show the met share on the verdict.",
                    "Keep the weights if the share is useful to see; split the tranche if parts should pay separately.",
                )
            )

    milestone_sum = sum((m.amount for m in milestones), start=Decimal(0))
    if milestone_sum != total_amount:
        issues.append(
            _issue(
                E,
                "amounts_mismatch",
                "milestones",
                f"Milestones sum to {milestone_sum} but the agreement total is {total_amount}.",
                "Adjust tranche amounts or the total so they match exactly.",
            )
        )

    for mi, m in enumerate(milestones):
        mpath = f"milestones[{mi}]"
        if len(m.criteria) > MAX_CRITERIA_PER_MILESTONE:
            issues.append(
                _issue(
                    W,
                    "too_many_criteria",
                    f"{mpath}.criteria",
                    f"{len(m.criteria)} criteria in one tranche; reviewers miss things past {MAX_CRITERIA_PER_MILESTONE}.",
                    "Split the tranche into two milestones.",
                )
            )
        ids = [c.id for c in m.criteria if c.id]
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            issues.append(
                _issue(E, "duplicate_criterion_id", f"{mpath}.criteria", f"Criterion id '{dup}' is used twice.")
            )
        statements = [c.statement.strip().lower() for c in m.criteria]
        for ci, s in enumerate(statements):
            if s in statements[:ci]:
                issues.append(
                    _issue(
                        W, "duplicate_statement", f"{mpath}.criteria[{ci}].statement", "Same statement appears twice."
                    )
                )
        for ci, c in enumerate(m.criteria):
            issues.extend(lint_criterion(c, f"{mpath}.criteria[{ci}]", m))

    issues.sort(key=lambda i: (i.level is not LintLevel.error, i.path))
    return LintReport(ok=not any(i.level is LintLevel.error for i in issues), issues=issues)


def assign_criterion_ids(milestones: Sequence[MilestoneBase]) -> None:
    """Give every criterion without an id the next free 'cN' within its milestone."""
    for m in milestones:
        used = {c.id for c in m.criteria if c.id}
        n = 1
        for c in m.criteria:
            if c.id:
                continue
            while f"c{n}" in used:
                n += 1
            c.id = f"c{n}"
            used.add(c.id)


def criteria_hash(
    milestones: Sequence[MilestoneBase],
    deliverable_release: DeliverableRelease = DeliverableRelease.on_submit,
    min_evidence_visibility: EvidenceVisibility = EvidenceVisibility.hash_only,
) -> str:
    """Hash of everything a party signs off on: tranche order and MilestoneBase's fields (title, amount, due date,
    criteria), plus deliverable_release and min_evidence_visibility when deliverable_release isn't the default. Works on request bodies and stored rows alike,
    since both are MilestoneBase."""
    payload: list = []
    for m in milestones:
        data = MilestoneBase.model_validate(m).model_dump(mode="json")
        data["amount"] = f"{m.amount:.2f}"  # '10000' and '10000.00' (as read back from the DB) must hash the same
        for c, raw in zip(m.criteria, data["criteria"], strict=True):
            # Weights are signed too. Shortest form, so '1', '1.0' and '1.00' hash the same and the default weight
            # keeps the hash agreements were signed with before weights were normalised.
            raw["weight"] = str(Decimal(str(c.weight)).normalize())
        payload.append(data)
    if deliverable_release != DeliverableRelease.on_submit:
        # Only when set, so agreements signed before the setting existed keep their hash.
        payload = {
            "deliverable_release": str(deliverable_release),
            "min_evidence_visibility": str(min_evidence_visibility),
            "milestones": payload,
        }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
