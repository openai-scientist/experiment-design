"""Scientific exploration proposals and bound internal direction selections."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_serializer, model_validator

from experiment_design.contracts import ArtifactBinding, Observation, Record, SourceSnapshot

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CandidateId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]


class ExplorationPolicy(Record):
    schema_version: Literal[1] = 1
    version: Literal[1] = 1
    mode: Literal["exploration"] = "exploration"


class OutcomeConsequence(Record):
    observation: Text
    next_step: Text
    inference_limit: Text


class CandidateResources(Record):
    requirements: list[Text]
    cost_basis: Text
    unknowns: list[Text]


class ExperimentCandidate(Record):
    id: CandidateId
    purpose: Text
    question: Text
    target_uncertainty: Text
    alternative_explanations: list[Text]
    method_sketch: Text
    outcomes: list[OutcomeConsequence] = Field(min_length=1)
    assumptions: list[Text]
    evidence_refs: list[Text]
    resources: CandidateResources
    feasibility: Literal["possible", "conditional", "unknown", "unavailable"]
    feasibility_basis: Text
    conditions: list[Text]
    limitations: list[Text]

    @model_validator(mode="after")
    def feasibility_disclosure(self):
        if self.feasibility == "conditional" and not self.conditions:
            raise ValueError("conditional feasibility requires conditions")
        if self.feasibility == "unknown" and not (self.resources.unknowns or self.limitations):
            raise ValueError("unknown feasibility requires resource unknowns or limitations")
        if self.feasibility == "unavailable" and not self.evidence_refs:
            raise ValueError("unavailable feasibility requires evidence refs")
        return self


class PriorityGroup(Record):
    candidate_ids: list[CandidateId] = Field(min_length=1)
    rationale: Text
    evidence_refs: list[Text]
    conditions: list[Text]


class ExplorationSubmission(Record):
    intent_interpretation: Text
    intent_source_refs: list[Text] = Field(min_length=1)
    candidates: list[ExperimentCandidate]
    priorities: list[PriorityGroup]
    unknowns: list[Text]
    focused_questions: list[Text]
    rationale: Text
    defer_reason: Text | None = None

    @model_validator(mode="after")
    def candidate_coverage(self):
        ids = [candidate.id for candidate in self.candidates]
        ordered = [id for group in self.priorities for id in group.candidate_ids]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate IDs must be unique")
        if len(ordered) != len(set(ordered)) or set(ordered) != set(ids):
            raise ValueError("priority groups must cover each candidate exactly once")
        if not ids and not (self.defer_reason and self.focused_questions):
            raise ValueError("empty candidates require a defer reason and focused question")
        if not any(c.feasibility != "unavailable" for c in self.candidates) and not (
            self.defer_reason and self.focused_questions
        ):
            raise ValueError("no eligible candidates: provide a defer reason and focused question")
        return self


class ExplorationSelection(Record):
    task_id: Text
    binding: ArtifactBinding
    choice: Text


class ExplorationSnapshot(Record):
    schema_version: Literal[1, 2] = 1
    policy: ExplorationPolicy | None = None
    id: Text
    run_id: Text
    task_id: Text
    binding: ArtifactBinding
    source: SourceSnapshot
    observations: list[Observation]
    submission: ExplorationSubmission
    selection: ExplorationSelection
    outcome: Literal["recommendation", "needs_input"]
    created_at: Text
    origin_issue_refs: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.schema_version == 1:
            data.pop("origin_issue_refs", None)
        return data


class SelectedCandidateInput(Record):
    snapshot: ExplorationSnapshot
    candidate_id: str


def evidence_refs(submission: ExplorationSubmission) -> set[str]:
    return {
        *submission.intent_source_refs,
        *(ref for c in submission.candidates for ref in c.evidence_refs),
        *(ref for group in submission.priorities for ref in group.evidence_refs),
    }


def validate_exploration(
    submission: ExplorationSubmission, *, allowed_evidence_refs: set[str], source_refs: set[str]
) -> None:
    if unknown := set(submission.intent_source_refs) - source_refs:
        raise ValueError(f"intent refs must cite exact source refs: {sorted(unknown)}")
    if unknown := evidence_refs(submission) - allowed_evidence_refs:
        raise ValueError(f"unknown evidence refs: {sorted(unknown)}")


def recommendation_options(submission: ExplorationSubmission) -> dict[str, str]:
    candidates = {candidate.id: candidate for candidate in submission.candidates}
    options = {}
    for group in submission.priorities:
        for id in group.candidate_ids:
            candidate = candidates[id]
            if candidate.feasibility != "unavailable":
                options[f"recommend:{id}"] = (
                    f"{candidate.question} [{candidate.feasibility}]. "
                    f"Conditions: {candidate.conditions + group.conditions}. "
                    f"Limits: {candidate.limitations}. Basis: {group.rationale}"
                )
        if options:
            break
    if submission.defer_reason and submission.focused_questions:
        options["DEFER"] = submission.defer_reason
    return options
