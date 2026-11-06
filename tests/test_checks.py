"""The deterministic checks on the kind of files people actually upload."""

from datetime import date

from app.domain import checks
from app.models import Criterion, Evidence


def evidence(title: str, content: str, kind: str = "spreadsheet", dated: date | None = None, i: int = 1) -> Evidence:
    return Evidence.model_validate(
        {
            "id": f"e{i}",
            "milestone_id": "m",
            "submitted_by": "party:test",
            "source": "upload",
            "external_ref": title,
            "pinned_ref": "p",
            "evidence_type": kind,
            "title": title,
            "content": content,
            "dated": dated,
            "received_at": "2027-06-01T00:00:00Z",
        }
    )


def criterion(metric: str, check_id: str, comparator: str = "exists", target=None, kinds=("spreadsheet",)) -> Criterion:
    return Criterion.model_validate(
        {
            "id": "c1",
            "statement": "x",
            "measure": {"metric": metric, "comparator": comparator, "target": target},
            "evidence_required": list(kinds),
            "evidence_description": "the file",
            "verification": "check",
            "check_id": check_id,
        }
    )


SIGN_IN = evidence(
    "Spring workshop sign-in sheets.csv", "session_date,session,attendee,signed\n2027-04-10,Session 1,Ava Nguyen,yes\n"
)
BUDGET = evidence(
    "Spring workshop budget report.csv", "line_item,budgeted_usd,actual_spend_usd\nVenue,1500.00,1450.00\n", i=2
)


def test_file_present_folds_plurals_and_tolerates_a_wordy_metric():
    files = [SIGN_IN, BUDGET]
    for metric in ("sign-in sheet", "signed sign-in sheets", "signed sign-in sheet for each session"):
        r = checks.evaluate(criterion(metric, "file_present"), files)
        assert r.met and r.evidence_ids == ["e1"], metric
    for metric in ("budget report", "budget report with actual spending per line item"):
        r = checks.evaluate(criterion(metric, "file_present"), files)
        assert r.met and r.evidence_ids == ["e2"], metric
    # Still not a free pass: a metric the files don't carry fails and says so.
    r = checks.evaluate(criterion("final outcomes report", "file_present"), files)
    assert not r.met and "final outcomes report" in r.missing


def test_file_present_prefers_the_file_that_matches_most_of_the_metric():
    r = checks.evaluate(criterion("workshop budget report", "file_present"), [SIGN_IN, BUDGET])
    assert r.evidence_ids == ["e2"]
