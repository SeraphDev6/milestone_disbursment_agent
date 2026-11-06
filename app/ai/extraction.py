"""Turn the extraction agent's output into validated, linted tranche drafts."""

from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlmodel import Field, SQLModel

from app.ai.agents import AIRuntime, ExtractionOutput
from app.domain.criteria import assign_criterion_ids, lint_tranches
from app.models import Criterion, LintReport, MilestoneCreate


class Provenance(SQLModel):
    path: str = Field(..., description="Where the criterion landed, e.g. 'milestones[0].criteria[1]'.")
    source_quote: str = Field(..., description="Agreement text it was drawn from.")


class ExtractionProblem(SQLModel):
    path: str = Field(description="Where the proposal would have landed, e.g. 'milestones[1].criteria[0]'.")
    message: str = Field(description="Why it was left out.")


class ExtractionResult(SQLModel):
    title: str = Field(description="The agreement's name as the model read it.")
    total_amount: Decimal | None = Field(description="The agreement total; null when the document gives none.")
    currency: str = Field(description="ISO 4217, from the document (USD when unstated).")
    milestones: list[MilestoneCreate] = Field(..., description="Proposed tranches; edit, then POST /agreements.")
    open_questions: list[str] = Field(..., description="Ambiguities a person must resolve before confirming.")
    problems: list[ExtractionProblem] = Field(..., description="Proposals that failed validation and were left out.")
    provenance: list[Provenance] = Field(description="The agreement sentence each criterion was drawn from.")
    lint: LintReport = Field(description="The same lint that gates signing, run on the proposal.")
    model: str = Field(description="Which model produced it ('mock' on a stand-in).")


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", "").replace("$", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def convert(out: ExtractionOutput, model_name: str) -> ExtractionResult:
    problems: list[ExtractionProblem] = []
    provenance: list[Provenance] = []
    milestones: list[MilestoneCreate] = []

    for mi, xm in enumerate(out.milestones):
        criteria: list[Criterion] = []
        for ci, xc in enumerate(xm.criteria):
            path = f"milestones[{mi}].criteria[{ci}]"
            try:
                criteria.append(
                    Criterion.model_validate(
                        {
                            "statement": xc.statement,
                            "measure": {
                                "metric": xc.metric,
                                "comparator": xc.comparator,
                                "target": xc.target,
                                "unit": xc.unit,
                            },
                            "evidence_required": xc.evidence_required,
                            "evidence_description": xc.evidence_description,
                            "verification": xc.verification,
                            "check_id": xc.check_id,
                        }
                    )
                )
                provenance.append(
                    Provenance(path=f"milestones[{mi}].criteria[{len(criteria) - 1}]", source_quote=xc.source_quote)
                )
            except ValidationError as e:
                problems.append(ExtractionProblem(path=path, message=f"'{xc.statement}': {e.errors()[0]['msg']}"))
        try:
            milestones.append(
                MilestoneCreate.model_validate(
                    {"title": xm.title, "amount": _decimal(xm.amount), "due_date": xm.due_date, "criteria": criteria}
                )
            )
        except ValidationError as e:
            problems.append(
                ExtractionProblem(path=f"milestones[{mi}]", message=f"'{xm.title}': {e.errors()[0]['msg']}")
            )

    assign_criterion_ids(milestones)
    total = _decimal(out.total_amount)
    return ExtractionResult(
        title=out.title,
        total_amount=total,
        currency=out.currency.upper()[:3],
        milestones=milestones,
        open_questions=out.open_questions,
        problems=problems,
        provenance=provenance,
        lint=lint_tranches(total if total is not None else Decimal(0), milestones),
        model=model_name,
    )


def extract(ai: AIRuntime, document: bytes, media_type: str, filename: str) -> ExtractionResult:
    return convert(ai.extract(document, media_type, filename), ai.model_name)
