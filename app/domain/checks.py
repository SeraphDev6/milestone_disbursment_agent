"""Registry of deterministic checks a criterion can be verified by.

A criterion with verification="check" names one of these. The registry says which evidence
types each check can run against, so the lint can reject impossible pairings at draft time.
Checks never ask the model: they read evidence content (CSV for sheets) and facts adapters
recorded at intake (e.g. ci_green from the GitHub adapter). A payee can't assert those facts
through the API.
"""

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.domain.models import Comparator, Criterion, CriterionResult, EvidenceEvent, EvidenceType


@dataclass(frozen=True)
class CheckSpec:
    description: str
    evidence: frozenset[EvidenceType]
    comparators: frozenset[Comparator]


CHECKS: dict[str, CheckSpec] = {
    "ci_green": CheckSpec(
        "All CI check runs pass on the pinned commit.",
        frozenset({EvidenceType.code_change, EvidenceType.ci_run}),
        frozenset({Comparator.exists}),
    ),
    "file_present": CheckSpec(
        "A file matching the metric is attached to the evidence.",
        frozenset(
            {
                EvidenceType.document,
                EvidenceType.spreadsheet,
                EvidenceType.dataset,
                EvidenceType.photo,
                EvidenceType.code_change,
            }
        ),
        frozenset({Comparator.exists}),
    ),
    "row_count": CheckSpec(
        "Number of data rows in an attached sheet compared to the target.",
        frozenset({EvidenceType.spreadsheet, EvidenceType.dataset}),
        frozenset({Comparator.at_least, Comparator.at_most, Comparator.equals}),
    ),
    "sheet_total": CheckSpec(
        "Sum of the metric's column in an attached sheet compared to the target.",
        frozenset({EvidenceType.spreadsheet}),
        frozenset({Comparator.at_least, Comparator.at_most, Comparator.equals}),
    ),
    "dated_by": CheckSpec(
        "The evidence's own date (document date, commit date) is on or before the target.",
        frozenset({EvidenceType.document, EvidenceType.spreadsheet, EvidenceType.code_change, EvidenceType.photo}),
        frozenset({Comparator.on_or_before}),
    ),
}


# --- Evaluation ------------------------------------------------------------------------

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 3}


def _compare(observed: Decimal | date, comparator: Comparator, target: object) -> bool:
    match comparator:
        case Comparator.at_least:
            return observed >= target  # type: ignore[operator]
        case Comparator.at_most:
            return observed <= target  # type: ignore[operator]
        case Comparator.equals:
            return str(observed) == str(target) or (
                isinstance(observed, Decimal) and _to_decimal(str(target)) == observed
            )
        case Comparator.on_or_before:
            return observed <= target  # type: ignore[operator]
    return False


def _to_decimal(text: str) -> Decimal | None:
    m = _NUMBER.search(text.replace("$", ""))
    if not m:
        return None
    try:
        return Decimal(m.group().replace(",", ""))
    except InvalidOperation:
        return None


def _rows(content: str) -> list[dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(content.strip()))
        return [r for r in reader if any((v or "").strip() for v in r.values())]
    except csv.Error:
        return []


def _pick_column(rows: list[dict[str, str]], metric: str) -> str | None:
    if not rows:
        return None
    headers = [h for h in rows[0] if h]
    wanted = _words(metric)
    scored = sorted(headers, key=lambda h: len(wanted & _words(h)), reverse=True)
    if scored and wanted & _words(scored[0]):
        return scored[0]
    for fallback in ("actual", "amount", "total", "spend", "cost", "value"):
        for h in headers:
            if fallback in h.lower():
                return h
    return None


def _result(c: Criterion, met: bool, observed: str | None, ev: Sequence[EvidenceEvent], missing: str | None):
    return CriterionResult(
        criterion_id=c.id or "",
        met=met,
        decided_by=f"check:{c.check_id}",
        observed=observed,
        evidence_ids=[e.id for e in ev] if met or observed else [],
        quote=None,
        missing=None if met else missing,
    )


def evaluate(c: Criterion, evidence: Sequence[EvidenceEvent]) -> CriterionResult:
    """Decide one check-verified criterion from the evidence. Deterministic."""
    spec = CHECKS[c.check_id or ""]
    usable = [e for e in evidence if e.evidence_type in spec.evidence and e.evidence_type in c.evidence_required]
    need = f"Attach {c.evidence_description} ({', '.join(t.value for t in c.evidence_required)})."
    if not usable:
        return _result(c, False, None, [], need)

    target = c.measure.target
    unit = f" {c.measure.unit}" if c.measure.unit else ""

    if c.check_id == "ci_green":
        green = [e for e in usable if e.check_results.get("ci_green") is True]
        if green:
            return _result(c, True, "CI green on pinned commit", green[:1], None)
        return _result(c, False, None, [], "Submit a commit through the GitHub integration with all CI checks passing.")

    if c.check_id == "file_present":
        wanted = _words(c.measure.metric)
        hits = [e for e in usable if wanted and wanted <= _words(e.title + " " + e.content[:2000])]
        if hits:
            return _result(c, True, f"found '{hits[0].title}'", hits[:1], None)
        return _result(c, False, None, [], f"No attached file matches '{c.measure.metric}'. {need}")

    if c.check_id == "row_count":
        best: tuple[int, EvidenceEvent] | None = None
        for e in usable:
            n = len(_rows(e.content))
            if best is None or n > best[0]:
                best = (n, e)
        assert best is not None
        n, e = best
        met = _compare(Decimal(n), c.measure.comparator, target)
        return _result(
            c,
            met,
            f"{n} rows in '{e.title}'",
            [e],
            f"Found {n}{unit}; need {c.measure.comparator.value.replace('_', ' ')} {target}{unit}.",
        )

    if c.check_id == "sheet_total":
        for e in usable:
            rows = _rows(e.content)
            col = _pick_column(rows, c.measure.metric)
            if col is None:
                continue
            if any(None in r for r in rows):
                # More fields than headers: usually an unquoted "4,200.00". Summing it would be wrong.
                return _result(
                    c,
                    False,
                    None,
                    [],
                    f"'{e.title}' has rows with more columns than its header "
                    "(unquoted commas in amounts?). Re-export it as CSV with quoted values.",
                )
            values = [v for r in rows if (v := _to_decimal(r.get(col) or "")) is not None]
            total = sum(values, start=Decimal(0))
            met = _compare(total, c.measure.comparator, target)
            return _result(
                c,
                met,
                f"{total}{unit} (column '{col}' in '{e.title}')",
                [e],
                f"Total is {total}{unit}; need {c.measure.comparator.value.replace('_', ' ')} {target}{unit}.",
            )
        return _result(c, False, None, [], f"No sheet has a column for '{c.measure.metric}'. {need}")

    if c.check_id == "dated_by":
        dated = [e for e in usable if e.dated is not None]
        if not dated:
            return _result(c, False, None, [], f"Evidence needs its own date (e.g. report cover date). {need}")
        e = min(dated, key=lambda x: x.dated)  # type: ignore[arg-type, return-value]
        met = _compare(e.dated, Comparator.on_or_before, target)  # type: ignore[arg-type]
        return _result(c, met, f"dated {e.dated}", [e], f"Evidence is dated {e.dated}, after {target}.")

    return _result(c, False, None, [], f"Check '{c.check_id}' is not implemented.")
