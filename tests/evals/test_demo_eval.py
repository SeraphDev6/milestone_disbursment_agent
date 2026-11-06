"""The demo's documents through the real model, as a pydantic-evals dataset.

Two datasets: the grant agreement PDF through extraction (does the engine propose the tranches the agreement
describes, with checkable criteria the committed evidence then satisfies?), and the grantee's evidence through
the verifier and the skeptic (is the finished report met and cited, is the draft with 'TODO' caught, is the thin
note sent back with what's missing?). Each case's evaluators are plain yes/no questions, so the report reads as
a checklist of the demo's beats.

Needs ANTHROPIC_API_KEY (from .env or the environment) and spends model credits, so it is deselected by default:

    uv run pytest -m eval tests/evals -s

Scores are printed; a case fails the test only when an evaluator says the beat wouldn't work on screen.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext, MaxDuration
from pypdf import PdfReader

from app.ai.agents import AIRuntime, claude_runtime
from app.ai.extraction import ExtractionResult, extract
from app.api.demo import FILES, FILES_DIR
from app.config import Settings
from app.domain import checks
from app.integrations.uploads import parse_upload
from app.models import Criterion, Evidence, MilestoneCreate, Verdict
from app.services.verification import judge, skeptic_pass
from tests.conftest import GOOD_DRAFT

pytestmark = pytest.mark.eval

FILE = {f.key: FILES_DIR / f.name for f in FILES}
MEDIA = {f.key: f.media_type for f in FILES}


@pytest.fixture(scope="module")
def runtime() -> AIRuntime:
    settings = Settings()
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set; the demo eval needs the real model")
    return claude_runtime(key)


# --- evidence rows from the demo files, the way an upload makes them ---------------------------------------------


def evidence_from(
    key: str, evidence_id: str, dated: date | None = None
) -> tuple[Evidence, dict[str, tuple[bytes, str]]]:
    """An upload of one demo file, as the API stores it. `dated` is the form field a grantee fills in when the
    file carries no date of its own (a CSV); PDFs bring theirs."""
    path = FILE[key]
    data = path.read_bytes()
    [parsed] = parse_upload(path.name, data, None, dated)
    row = Evidence.model_validate(
        parsed.evidence,
        update={
            "id": evidence_id,
            "milestone_id": "demo",
            "submitted_by": "party:demo",
            "source": "upload",
            "external_ref": parsed.evidence.external_ref,
            "pinned_ref": "sha256:" + hashlib.sha256(parsed.evidence.content.encode()).hexdigest(),
            "payer_visibility": "hash_only",
            "received_at": "2027-09-14T12:00:00Z",
        },
    )
    return row, {evidence_id: (data, parsed.media_type)}


LAST_SESSION = date(2027, 5, 22)  # the sign-in sheet's date, as the grantee enters it on upload


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def criteria_of(milestone: dict[str, Any]) -> list[Criterion]:
    m = MilestoneCreate.model_validate(milestone)
    for i, c in enumerate(m.criteria, start=1):
        c.id = c.id or f"c{i}"
    return m.criteria


def pdf_text(path: Path) -> str:
    return " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(path.read_bytes())).pages)


# --- extraction ---------------------------------------------------------------------------------------------------


@dataclass
class TranchesMatchTheAgreement(Evaluator[Path, ExtractionResult]):
    """The schedule in section 3: two tranches, 10,000 then 20,000 USD, due May 31 and September 30, 2027."""

    def evaluate(self, ctx: EvaluatorContext[Path, ExtractionResult]) -> dict[str, bool]:
        x = ctx.output
        amounts = [m.amount for m in x.milestones]
        return {
            "two_tranches": len(x.milestones) == 2,
            "amounts_10k_then_20k": amounts == [Decimal("10000.00"), Decimal("20000.00")],
            "total_30k_usd": x.total_amount == Decimal("30000.00") and x.currency == "USD",
            "due_dates": [str(m.due_date) for m in x.milestones] == ["2027-05-31", "2027-09-30"],
        }


@dataclass
class CriteriaAreCheckable(Evaluator[Path, ExtractionResult]):
    """Every proposal survived validation and lint; nothing needs a human signature; each quotes the agreement."""

    def evaluate(self, ctx: EvaluatorContext[Path, ExtractionResult]) -> dict[str, bool]:
        x = ctx.output
        crits = [c for m in x.milestones for c in m.criteria]
        source = _squash(pdf_text(ctx.inputs))
        quoted = [_squash(p.source_quote)[:40] for p in x.provenance]
        found = sum(1 for q in quoted if q and q in source)
        return {
            "lint_ok": x.lint.ok,
            "no_problems": not x.problems,
            "no_human_sign_off": all(c.verification != "human" for c in crits),
            "known_checks": all(c.check_id in checks.CHECKS for c in crits if c.verification == "check"),
            "quotes_found": found / len(quoted) if quoted else 0.0,
            "quotes_the_agreement": bool(quoted) and found >= 0.75 * len(quoted),
        }


@dataclass
class VagueClauseIsFlagged(Evaluator[Path, ExtractionResult]):
    """'Meaningful community impact' (3.3) must not become a criterion as written; it is raised as a question."""

    def evaluate(self, ctx: EvaluatorContext[Path, ExtractionResult]) -> dict[str, bool]:
        x = ctx.output
        statements = " ".join(c.statement.lower() for m in x.milestones for c in m.criteria)
        questions = " ".join(x.open_questions).lower()
        return {
            "no_meaningful_in_criteria": "meaningful" not in statements,
            "impact_raised_as_question": "impact" in questions or "communit" in questions,
        }


@dataclass
class FixturesSatisfyExtractedChecks(Evaluator[Path, ExtractionResult]):
    """Whatever check criteria the model wrote, the committed sign-in sheet, budget and report must pass them,
    and the thin note must fail them: the demo's evidence has to work against a fresh extraction every time."""

    def evaluate(self, ctx: EvaluatorContext[Path, ExtractionResult]) -> dict[str, bool | float | EvaluationReason]:
        x = ctx.output
        if len(x.milestones) < 2:
            return {"tranche_1_checks_pass": False, "tranche_2_checks_pass": False}

        def passed(results, crits) -> EvaluationReason:
            failed = [
                f"{c.check_id} '{c.measure.metric}': {r.missing}"
                for c, r in zip(crits, results, strict=True)
                if not r.met
            ]
            return EvaluationReason(value=not failed, reason="; ".join(failed) or None)

        t1 = [c for c in x.milestones[0].criteria if c.verification == "check"]
        t2 = [c for c in x.milestones[1].criteria if c.verification == "check"]
        for i, c in enumerate([*t1, *t2], start=1):
            c.id = c.id or f"x{i}"
        sign_in, _ = evidence_from("sign_in", "e1", dated=LAST_SESSION)
        signed, _ = evidence_from("sign_in_pdf", "e5")
        budget, _ = evidence_from("budget", "e2", dated=LAST_SESSION)
        report, _ = evidence_from("report", "e3")
        thin, _ = evidence_from("thin", "e4")
        t1_results = [checks.evaluate(c, [signed, sign_in, budget]) for c in t1]
        t2_results = [checks.evaluate(c, [report]) for c in t2]
        thin_results = [checks.evaluate(c, [thin]) for c in t1]
        return {
            "tranche_1_has_checks": bool(t1),
            "tranche_1_checks_pass": passed(t1_results, t1),
            "tranche_2_checks_pass": passed(t2_results, t2),
            "thin_note_fails_tranche_1": bool(t1) and not any(r.met for r in thin_results),
            "check_criteria": float(len(t1) + len(t2)),
        }


@dataclass
class SowPaysOnMergedPr(Evaluator[Path, ExtractionResult]):
    """The SOW: two milestones, 3,500 then 2,500 USD, each a ci_green check on a code change, nothing for a person."""

    def evaluate(self, ctx: EvaluatorContext[Path, ExtractionResult]) -> dict[str, bool]:
        x = ctx.output
        crits = [c for m in x.milestones for c in m.criteria]
        return {
            "two_milestones_6k": [m.amount for m in x.milestones] == [Decimal("3500.00"), Decimal("2500.00")]
            and x.total_amount == Decimal("6000.00"),
            "each_milestone_has_ci_green": all(any(c.check_id == "ci_green" for c in m.criteria) for m in x.milestones),
            "ci_green_on_code_change": all(
                "code_change" in c.evidence_required for c in crits if c.check_id == "ci_green"
            ),
            "lint_ok": x.lint.ok,
            "no_human_sign_off": all(c.verification != "human" for c in crits),
        }


def extraction_dataset() -> Dataset[Path, ExtractionResult]:
    return Dataset(
        name="demo-extraction",
        cases=[
            Case(
                name="riverside-grant-agreement",
                inputs=FILE["agreement"],
                metadata={"beat": "0:20-0:50 ingest and lint"},
                evaluators=[
                    TranchesMatchTheAgreement(),
                    CriteriaAreCheckable(),
                    VagueClauseIsFlagged(),
                    FixturesSatisfyExtractedChecks(),
                ],
            ),
            Case(
                name="checkout-redesign-sow",
                inputs=FILE["sow"],
                metadata={"beat": "2:00-2:30 same engine"},
                evaluators=[SowPaysOnMergedPr()],
            ),
        ],
        evaluators=[MaxDuration(seconds=180)],
    )


def test_the_grant_agreement_extracts_into_the_demo_grant(runtime: AIRuntime):
    def task(path: Path) -> ExtractionResult:
        return extract(runtime, path.read_bytes(), "application/pdf", path.name)

    report = extraction_dataset().evaluate_sync(task, max_concurrency=2)
    report.print(include_input=False, include_output=False, include_durations=True)
    failed = {}
    for case in report.cases:
        x = case.output
        print(f"\n{case.name} open questions:", *x.open_questions, sep="\n- ")
        print("criteria:")
        for m in x.milestones:
            for c in m.criteria:
                print(f"- [{m.title}] {c.verification}/{c.check_id or '-'}: {c.statement}")
        failed |= {f"{case.name}:{name}": r.reason for name, r in case.assertions.items() if r.value is False}
    assert not failed, f"the demo's beats that wouldn't work: {failed}"


# --- verification -------------------------------------------------------------------------------------------------


@dataclass
class VerdictIn:
    evidence_key: str
    tranche: int  # index into the demo grant's milestones
    run_skeptic: bool = False


@dataclass
class VerdictOut:
    verdict: Verdict
    skeptic_disagrees_on: list[str]
    skeptic_error: str | None


@dataclass
class MetAndCited(Evaluator[VerdictIn, VerdictOut]):
    """A finished report: every criterion met, each citing the report, with confidence over the auto-release bar."""

    def evaluate(self, ctx: EvaluatorContext[VerdictIn, VerdictOut]) -> dict[str, bool | float]:
        v = ctx.output.verdict
        return {
            "met": v.met,
            "every_criterion_cited": all(r.evidence_ids for r in v.criteria_results),
            "confidence": v.model_confidence,
            "above_auto_release_threshold": v.model_confidence >= 0.85,
        }


@dataclass
class DraftIsCaught(Evaluator[VerdictIn, VerdictOut]):
    """The draft's section B has no post scores. Either the first pass says not met, or the skeptic disagrees."""

    def evaluate(self, ctx: EvaluatorContext[VerdictIn, VerdictOut]) -> dict[str, bool]:
        v, out = ctx.output.verdict, ctx.output
        model_results = [r for r in v.criteria_results if r.decided_by == "model"]
        first_pass_not_met = any(not r.met for r in model_results)
        return {
            "caught_before_release": first_pass_not_met or bool(out.skeptic_disagrees_on),
            "skeptic_ran": out.skeptic_error is None,
            "names_the_gap": any(
                "section b" in (r.missing or "").lower() or "post" in (r.missing or "").lower() for r in model_results
            )
            or first_pass_not_met is False,
        }


@dataclass
class ThinNoteIsSentBack(Evaluator[VerdictIn, VerdictOut]):
    """A friendly note is not a report: not met, and the grantee is told what to submit."""

    def evaluate(self, ctx: EvaluatorContext[VerdictIn, VerdictOut]) -> dict[str, bool]:
        v = ctx.output.verdict
        return {
            "not_met": not v.met,
            "nothing_cited_as_met": not any(r.met for r in v.criteria_results),
            "says_what_is_missing": all((r.missing or "").strip() for r in v.criteria_results),
        }


def verification_dataset() -> Dataset[VerdictIn, VerdictOut]:
    return Dataset(
        name="demo-verification",
        cases=[
            Case(
                name="final-report-met",
                inputs=VerdictIn("report", 1, run_skeptic=True),
                metadata={"beat": "1:10-1:40 release"},
                evaluators=[MetAndCited()],
            ),
            Case(
                name="draft-report-caught",
                inputs=VerdictIn("report_draft", 1, run_skeptic=True),
                metadata={"beat": "1:40-2:00 low confidence"},
                evaluators=[DraftIsCaught()],
            ),
            Case(
                name="thin-note-sent-back",
                inputs=VerdictIn("thin", 1),
                metadata={"beat": "1:40-2:00 low confidence"},
                evaluators=[ThinNoteIsSentBack()],
            ),
        ],
        evaluators=[MaxDuration(seconds=240)],
    )


def test_the_evidence_is_judged_as_the_script_says(runtime: AIRuntime):
    def task(inp: VerdictIn) -> VerdictOut:
        criteria = criteria_of(GOOD_DRAFT["milestones"][inp.tranche])
        row, attachments = evidence_from(inp.evidence_key, "e1")
        verdict = judge(runtime, criteria, [row], attachments)
        disagrees, error = [], None
        if inp.run_skeptic and verdict.met:
            s = skeptic_pass(runtime, criteria, verdict, [row], attachments)
            disagrees, error = s.disagrees_on, s.error
        return VerdictOut(verdict, disagrees, error)

    report = verification_dataset().evaluate_sync(task, max_concurrency=3)
    report.print(include_input=False, include_output=False, include_durations=True)
    for case in report.cases:
        print(f"\n{case.name}: {case.output.verdict.rationale}")
        for r in case.output.verdict.criteria_results:
            print(f"- {r.criterion_id} met={r.met} observed={r.observed!r} missing={r.missing!r}")
    failed = {f"{c.name}:{n}" for c in report.cases for n, r in c.assertions.items() if r.value is False}
    assert not failed, f"the demo's beats that wouldn't work: {sorted(failed)}"
