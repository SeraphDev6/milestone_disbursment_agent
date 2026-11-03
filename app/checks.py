"""Registry of deterministic checks a criterion can be verified by.

A criterion with verification="check" names one of these. The registry says which evidence
types each check can run against, so the lint can reject impossible pairings at draft time.
Check implementations land with the verifier (build plan: Nov 6).
"""

from dataclasses import dataclass

from app.domain import Comparator, EvidenceType


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
