"""Value objects stored inside rows (as typed JSON) and returned by the API: criteria, results, lint."""

from datetime import date
from decimal import Decimal

from pydantic import Field as PydanticField
from pydantic import model_validator
from sqlmodel import Field, SQLModel

from app.models.enums import Comparator, EvidenceType, LintLevel, VerificationMethod


class Measure(SQLModel):
    """The thing a reviewer counts or checks. Makes a criterion pass/fail instead of a judgment call."""

    metric: str = Field(
        min_length=2,
        max_length=120,
        description="What is counted or checked, e.g. 'workshop attendees', 'final report', 'unit test pass rate'.",
    )
    comparator: Comparator = Field(description="How the observed value is compared with the target.")
    # pydantic's Field: SQLModel's doesn't take union_mode, which makes "2027-09-30" a date and 200 a number.
    target: Decimal | date | str | None = PydanticField(
        default=None,
        union_mode="left_to_right",
        description="Number for at_least/at_most, date for on_or_before, value for equals, omitted for exists.",
    )
    unit: str | None = Field(default=None, max_length=40, description="Unit of a numeric target, e.g. 'people', '%'.")

    @model_validator(mode="after")
    def _target_fits_comparator(self) -> "Measure":
        c, t = self.comparator, self.target
        if c in (Comparator.at_least, Comparator.at_most) and not isinstance(t, Decimal):
            raise ValueError(f"{c} needs a numeric target")
        if c is Comparator.on_or_before and not isinstance(t, date):
            raise ValueError("on_or_before needs a date target (YYYY-MM-DD)")
        if c is Comparator.equals and t is None:
            raise ValueError("equals needs a target")
        if c is Comparator.exists and t is not None:
            raise ValueError("exists takes no target")
        return self


class Criterion(SQLModel):
    """One checkable condition of a tranche. A tranche releases only when every criterion is met."""

    id: str | None = Field(
        default=None,
        schema_extra={"pattern": r"^[a-z0-9_-]{1,32}$"},
        description="Stable id within the milestone, e.g. 'c1'. Assigned by the server when omitted.",
    )
    statement: str = Field(
        min_length=1,
        max_length=500,
        description="One condition, worded so a reviewer could answer yes or no from the evidence alone.",
    )
    measure: Measure = Field(description="What is counted or checked and the target it must reach.")
    evidence_required: list[EvidenceType] = Field(
        min_length=1, description="Kinds of evidence that can prove this criterion."
    )
    evidence_description: str = Field(
        min_length=1,
        max_length=300,
        description="The specific artifact that proves it, e.g. 'signed sign-in sheets for each session'.",
    )
    verification: VerificationMethod = Field(
        default=VerificationMethod.model, description="Who or what decides this criterion."
    )
    check_id: str | None = Field(
        default=None, description="Registry check that decides it; required when verification is 'check'."
    )
    due_by: date | None = Field(default=None, description="Date this must be met by, if earlier than the milestone.")
    weight: Decimal = Field(
        default=Decimal(1),
        gt=0,
        description="Relative weight for future partial release; unused while release is all-or-nothing.",
    )


class CriterionResult(SQLModel):
    criterion_id: str = Field(description="Id of the criterion judged.")
    met: bool = Field(description="Whether this criterion is satisfied.")
    decided_by: str = Field(description="'check:<id>', 'model' or 'human:<party id>'.")
    observed: str | None = Field(default=None, description="Value found for the measure, e.g. '214 people'.")
    evidence_ids: list[str] = Field(description="Evidence cited; empty means not met regardless of 'met'.")
    quote: str | None = Field(default=None, description="Short excerpt from the cited evidence supporting the result.")
    missing: str | None = Field(
        default=None, description="When not met: what evidence would satisfy it. Sent to the payee."
    )


class LintIssue(SQLModel):
    level: LintLevel = Field(description="error blocks confirmation; warning does not.")
    code: str = Field(description="Stable machine-readable rule id, e.g. 'vague_term'.")
    path: str = Field(description="Where the issue is, e.g. 'milestones[0].criteria[1].statement'.")
    message: str = Field(description="What is wrong.")
    suggestion: str | None = Field(default=None, description="How to fix it.")


class LintReport(SQLModel):
    ok: bool = Field(description="True when there are no errors; warnings allowed.")
    issues: list[LintIssue] = Field(default_factory=list, description="All findings, errors first.")
