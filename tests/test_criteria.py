from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.criteria import assign_criterion_ids, criteria_hash, lint_agreement, lint_tranches
from app.models import AgreementCreate, Measure


def codes(draft: dict, level: str | None = None) -> set[str]:
    report = lint_agreement(AgreementCreate.model_validate(draft))
    return {i.code for i in report.issues if level is None or i.level.value == level}


def first_criterion(draft: dict) -> dict:
    return draft["milestones"][0]["criteria"][0]


def test_good_draft_is_clean(draft):
    report = lint_agreement(AgreementCreate.model_validate(draft))
    assert report.ok, report.issues


@pytest.mark.parametrize(
    "statement",
    ["Does the thing", "Work completed as agreed by both parties", "Deliverables per the SOW are delivered"],
)
def test_does_the_thing_is_rejected(draft, statement):
    first_criterion(draft)["statement"] = statement
    errors = codes(draft, "error")
    assert errors & {"too_short", "deferred_definition"}


def test_vague_without_number_is_error(draft):
    first_criterion(draft)["statement"] = "Grantee runs high-quality and engaging workshops for families"
    assert "vague_term" in codes(draft, "error")


def test_vague_with_number_is_warning(draft):
    first_criterion(draft)["statement"] = "Improve reading scores by 10 points for enrolled students"
    assert "vague_term" in codes(draft, "warning")
    assert "vague_term" not in codes(draft, "error")


def test_unspecific_evidence_is_error(draft):
    first_criterion(draft)["evidence_description"] = "proof"
    assert "evidence_unspecific" in codes(draft, "error")


def test_check_must_exist_and_fit_evidence(draft):
    c = first_criterion(draft)
    c["check_id"] = "nope"
    assert "check_unknown" in codes(draft, "error")
    c["check_id"] = "ci_green"
    assert {"check_evidence_mismatch", "check_comparator_mismatch"} <= codes(draft, "error")
    c["check_id"] = None
    assert "check_missing" in codes(draft, "error")


def test_amounts_must_sum_to_total(draft):
    draft["total_amount"] = "31000.00"
    assert "amounts_mismatch" in codes(draft, "error")


def test_compound_and_human_are_warnings(draft):
    c = first_criterion(draft)
    c["statement"] = "At least 200 people attend; sessions are recorded and posted and shared"
    c["verification"] = "human"
    c["check_id"] = None
    warnings = codes(draft, "warning")
    assert {"compound", "human_only"} <= warnings


def test_measure_target_must_fit_comparator():
    with pytest.raises(ValidationError):
        Measure(metric="attendees", comparator="at_least")
    with pytest.raises(ValidationError):
        Measure(metric="report date", comparator="on_or_before", target="soon")
    with pytest.raises(ValidationError):
        Measure(metric="final report", comparator="exists", target=1)
    assert Measure(metric="report date", comparator="on_or_before", target="2027-09-30").target.year == 2027


def test_criterion_without_measure_is_invalid(draft):
    del first_criterion(draft)["measure"]
    with pytest.raises(ValidationError):
        AgreementCreate.model_validate(draft)


def test_ids_assigned_and_hash_stable(draft):
    a = AgreementCreate.model_validate(draft)
    assign_criterion_ids(a.milestones)
    assert [c.id for c in a.milestones[0].criteria] == ["c1", "c2"]
    h1 = criteria_hash(a.milestones)
    a.milestones[0].criteria[0].measure.target = Decimal(201)
    assert criteria_hash(a.milestones) != h1


# --- weights (#46, the reporting half) ---------------------------------------------------


def test_weights_are_signed_in_the_hash_in_their_shortest_form():
    import copy

    from app.models import MilestoneCreate
    from tests.conftest import GOOD_DRAFT

    base = MilestoneCreate.model_validate(GOOD_DRAFT["milestones"][0])
    same = copy.deepcopy(GOOD_DRAFT["milestones"][0])
    same["criteria"][0]["weight"] = "1.00"
    other = copy.deepcopy(GOOD_DRAFT["milestones"][0])
    other["criteria"][0]["weight"] = 3
    assert criteria_hash([base]) == criteria_hash([MilestoneCreate.model_validate(same)])
    assert criteria_hash([base]) != criteria_hash([MilestoneCreate.model_validate(other)])


def test_weighted_share_and_the_lint_warning_for_differing_weights():
    from app.domain.criteria import weighted_share
    from app.models import MilestoneCreate
    from tests.conftest import GOOD_DRAFT

    m = MilestoneCreate.model_validate(GOOD_DRAFT["milestones"][0])
    for i, c in enumerate(m.criteria, start=1):
        c.id = f"c{i}"
    m.criteria[0].weight = 3  # attendance counts three times the budget
    assert weighted_share(m.criteria, {"c1"}) == 0.75
    assert weighted_share(m.criteria, {"c2"}) == 0.25
    assert weighted_share(m.criteria, {"c1", "c2"}) == 1.0 and weighted_share(m.criteria, set()) == 0.0
    report = lint_tranches(m.amount, [m])
    assert [i.code for i in report.issues if i.level == "warning"] == ["weights_not_yet_paid"]
    m.criteria[0].weight = 1
    assert "weights_not_yet_paid" not in {i.code for i in lint_tranches(m.amount, [m]).issues}


def test_verdicts_report_the_met_share_by_weight(client):
    import copy

    from tests.conftest import GOOD_DRAFT
    from tests.helpers import create_active, h
    from tests.test_verification import SIGN_IN, submit

    draft = copy.deepcopy(GOOD_DRAFT)
    draft["milestones"][0]["criteria"][0]["weight"] = 3
    aid, (m1, _), keys = create_active(client, draft)
    submit(client, m1, keys, "spreadsheet", "s.csv", SIGN_IN)  # attendance met, budget not
    v = client.get(f"/milestones/{m1}/verdict", headers=h(keys["payer"])).json()
    assert v["met"] is False and v["weighted_share"] == 0.75
    packet = client.get(f"/agreements/{aid}/compliance-packet", headers=h(keys["payer"])).json()
    assert packet["milestones"][0]["verdicts"][-1]["weighted_share"] == 0.75
