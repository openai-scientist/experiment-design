"""Canonical scientific records and agent task contracts."""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Provenance = Literal["source", "assumption", "computed", "llm"]
Origin = Literal["package", "researcher", "designer_default", "missing"]
ContrastType = Literal["superiority", "non_inferiority", "superiority_by_margin"]
Severity = Literal["BLOCKER", "MAJOR", "MINOR"]
IssueStatus = Literal["open", "addressed", "rebutted", "resolved", "unresolved", "duplicate"]
RunStatus = Literal[
    "RUNNING",
    "NEEDS_RESEARCHER",
    "REVIEWABLE",
    "BLOCKED",
    "INCONCLUSIVE",
    "BUDGET_STOP",
    "APPROVED",
    "PUBLISHED",
    "FAILED",
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Quantity(Record):
    value: float | None = None
    unit: str = ""
    provenance: Provenance
    source_ref: str | None = None
    source_span: str | None = None
    note: str = ""


class Source(Record):
    id: str
    title: str
    kind: Literal["paper", "dataset", "note", "researcher_input"]
    text: str


class Assumption(Record):
    id: str
    statement: str
    quantity: Quantity | None = None


class Hypothesis(Record):
    statement: str
    prediction: str | None = None
    failure_condition: str | None = None
    exposure: str | None = None
    method: str | None = None
    limitations: list[str] = Field(default_factory=list)


class Outcome(Record):
    name: str
    unit: str | None = None
    kind: Literal["continuous", "binary"] | None = None
    direction: Literal["increase", "decrease"] | None = None


class Inference(Record):
    contrast_type: ContrastType | None = None
    margin: float | None = None
    estimand: str | None = None


class ScientificPackage(Record):
    id: str
    research_question: str
    hypothesis: Hypothesis
    scope: str | None = None
    outcome: Outcome | None = None
    inference: Inference | None = None
    constraints: list[str] = Field(default_factory=list)
    budget: str | None = None
    assumptions: list[Assumption] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)


class ClaimField(Record):
    value: str | float | None = None
    origin: Origin = "missing"


CLAIM_FIELDS = (
    "hypothesis",
    "prediction",
    "failure_condition",
    "exposure",
    "inference_scope",
    "primary_outcome",
    "outcome_unit",
    "outcome_kind",
    "direction",
    "contrast_type",
    "margin",
    "estimand",
)


class Claim(Record):
    hypothesis: ClaimField = Field(default_factory=ClaimField)
    prediction: ClaimField = Field(default_factory=ClaimField)
    failure_condition: ClaimField = Field(default_factory=ClaimField)
    exposure: ClaimField = Field(default_factory=ClaimField)
    inference_scope: ClaimField = Field(default_factory=ClaimField)
    primary_outcome: ClaimField = Field(default_factory=ClaimField)
    outcome_unit: ClaimField = Field(default_factory=ClaimField)
    outcome_kind: ClaimField = Field(default_factory=ClaimField)
    direction: ClaimField = Field(default_factory=ClaimField)
    contrast_type: ClaimField = Field(default_factory=ClaimField)
    margin: ClaimField = Field(default_factory=ClaimField)
    estimand: ClaimField = Field(default_factory=ClaimField)


class DesignChoice(Record):
    choice: str
    why: str
    alternatives_considered: list[str] = Field(default_factory=list)
    tradeoffs: str = ""


class Condition(Record):
    name: str
    role: Literal["treatment", "control", "baseline"]
    description: str


class ClusterSpec(Record):
    unit: str
    size: Quantity
    clusters_per_arm: int | None = None
    icc: Quantity | None = None
    equal_size: bool | None = None
    independent_clusters: bool | None = None


class Sampling(Record):
    population: str | None = None
    inclusion: str | None = None
    exclusion: str | None = None
    allocation: str | None = None
    randomization: str | None = None
    unit_of_randomization: str | None = None
    unit_of_analysis: str | None = None
    repetitions: str | None = None
    n_per_arm: int | None = None
    cluster: ClusterSpec | None = None


class Metric(Record):
    name: str
    kind: Literal["continuous", "binary"]
    operationalization: str
    unit: str
    timing: str = ""


class Measurement(Record):
    primary_metric: Metric | None = None
    secondary_metrics: list[Metric] = Field(default_factory=list)
    data_collection: str | None = None
    missingness: str | None = None


class AnalysisPlan(Record):
    method_id: str | None = None
    common_variance: bool | None = None
    model: str | None = None
    sidedness: Literal["one_sided", "two_sided"] = "two_sided"
    alpha: float = 0.05
    uncertainty: str | None = None
    decision_rule: str | None = None
    multiplicity: str | None = None
    stopping: str | None = None
    sensitivity: list[str] = Field(default_factory=list)


class PowerScenario(Record):
    name: str
    effect: Quantity | None = None
    sd: Quantity | None = None
    p_control: Quantity | None = None
    p_treatment: Quantity | None = None
    target_power: float = 0.8


class Protocol(Record):
    steps: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    feasibility: str | None = None
    stop_conditions: list[str] = Field(default_factory=list)


class Risk(Record):
    threat: str
    mechanism: str
    mitigation: str | None = None


class Design(Record):
    claim: Claim
    rationale: list[DesignChoice] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    sampling: Sampling = Field(default_factory=Sampling)
    measurement: Measurement = Field(default_factory=Measurement)
    analysis: AnalysisPlan = Field(default_factory=AnalysisPlan)
    scenarios: list[PowerScenario] = Field(default_factory=list)
    protocol: Protocol = Field(default_factory=Protocol)
    validity: list[Risk] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    handoff: list[str] = Field(default_factory=list)


class PatchOp(Record):
    path: str
    value: Any


class Patch(Record):
    ops: list[PatchOp]
    rationale: str
    addresses: list[str] = Field(default_factory=list)


class DesignVersion(Record):
    run_id: str
    version: int
    design: Design
    parent: int | None
    author: str
    patch: Patch | None
    design_hash: str
    created_at: str


class Finding(Record):
    severity: Severity
    paths: list[str]
    mechanism: str
    consequence: str
    suggested_fix: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    basis: Literal["evidence", "computation", "reasoning", "unknown_assumption"]
    duplicate_of: str | None = None


class ReviewIssue(Finding):
    id: str = ""
    run_id: str
    raised_by: str
    status: IssueStatus = "open"
    version_raised: int
    addressed_in: int | None = None
    rebuttal: str | None = None
    validator_code: str | None = None


class ResearcherRequest(Record):
    id: str = ""
    run_id: str
    kind: Literal["decision", "input"]
    question: str
    path: str | None = None
    options: list[str] = Field(default_factory=list)
    status: Literal["draft", "open", "answered"] = "draft"
    answer: str | None = None
    raised_by: str
    applied_in: int | None = None


class Approval(Record):
    run_id: str
    version: int
    design_hash: str
    approver: str
    at: str


class TaskBudget(Record):
    max_turns: int
    max_tokens: int


class AgentTask(Record):
    id: str = ""
    stage_id: Literal["S1", "S2", "S3", "S4", "S5", "S6", "S7"] | None = None
    binding: "ArtifactBinding | None" = None
    cohort_id: str | None = None
    objective: str
    package_ref: str
    design_version: int | None
    focus_paths: list[str] = Field(default_factory=list)
    issue_refs: list[str] = Field(default_factory=list)
    allowed_tools: list[str]
    budget: TaskBudget
    exit_conditions: list[str]


class AgentResult(Record):
    findings_or_changes: dict | None
    rationale: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    tool_observations: list[str] = Field(default_factory=list)
    uncertainty: str = ""
    completion_status: Literal["completed", "budget_exhausted", "failed"]
    tokens_in: int = 0
    tokens_out: int = 0
    llm_calls: int = 0
    tokens_cached: int = 0
    cache_usage_missing_calls: int = 0
    stop_reason: Literal["token_limit", "turn_limit"] | None = None


StageId = Literal["S1", "S2", "S3", "S4", "S5", "S6", "S7"]


class ArtifactBinding(Record):
    source_snapshot_id: str
    context_revision: int | None = None
    design_version: int | None = None
    design_hash: str | None = None
    event_id: int


class SourceArtifact(Record):
    ref: str
    path: str
    text: str
    sha256: str
    metadata: dict = Field(default_factory=dict)


class SourceSnapshot(Record):
    id: str
    artifacts: list[SourceArtifact]
    intake_issues: list[str] = Field(default_factory=list)


class FieldAnnotation(Record):
    status: Literal["source-stated", "agent-proposed", "unknown", "researcher-confirmed"]
    source_ref: str | None = None
    source_span: str | None = None
    note: str = ""
    value: Any = None


class ResearchContext(Record):
    id: str
    revision: int
    source_snapshot_id: str
    package: ScientificPackage
    field_annotations: dict[str, FieldAnnotation] = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)


class StageSpec(Record):
    id: StageId
    objective: str
    input_kinds: list[str]
    completion_condition: str
    permitted_return_targets: list[StageId] = Field(default_factory=list)


class StageResult(Record):
    stage_id: StageId
    status: Literal["COMPLETE", "NEEDS_INPUT", "REVISION_REQUIRED", "STOPPED", "FAILED"]
    binding: ArtifactBinding
    output_refs: list[str] = Field(default_factory=list)
    issue_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason: str


class DecisionOption(Record):
    id: str
    label: str
    proposal: dict = Field(default_factory=dict)
    consequences: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    uncertainty: str = ""


class DecisionResponse(Record):
    revision: int
    action: Literal[
        "SELECT_OPTION",
        "PROPOSE_ALTERNATIVE",
        "DEFER",
        "APPROVE_VERSION",
        "REQUEST_CHANGES",
        "REJECT",
        "HOLD",
    ]
    option_id: str | None = None
    text: str = ""
    researcher: str
    response_id: str
    affected_paths: list[str] = Field(default_factory=list)
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ResearcherDecision(Record):
    id: str
    run_id: str
    revision: int = 1
    purpose: Literal["scientific", "final_review"] = "scientific"
    status: Literal["IDENTIFIED", "EXPLORING", "AWAITING_RESEARCHER", "ANSWERED", "APPLIED"] = (
        "IDENTIFIED"
    )
    binding: ArtifactBinding
    problem: str
    affected_paths: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    options: list[DecisionOption] = Field(default_factory=list)
    recommendation: str = ""
    responses: list[DecisionResponse] = Field(default_factory=list)
    interpretation: dict = Field(default_factory=dict)
    application: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    reconciliation: dict = Field(default_factory=dict)


class ReviewCohort(Record):
    id: str
    run_id: str
    binding: ArtifactBinding
    member_task_ids: dict[str, str]
    submission_refs: list[str] = Field(default_factory=list)
    published_event_id: int | None = None
    superseded_by: str | None = None


class Observation(Record):
    id: str
    task_id: str
    binding: ArtifactBinding
    tool_name: str
    arguments: dict
    result: dict
    accepted: bool
    evidence_ref: str


class ReadinessAssessment(Record):
    id: str
    run_id: str
    binding: ArtifactBinding
    review_status: Literal["REVIEWABLE", "NEEDS_INPUT", "NOT_READY"]
    handoff_complete: bool
    execution_ready: bool = False
    blockers: list[str] = Field(default_factory=list)
    decision_ids: list[str] = Field(default_factory=list)
    computation_refs: list[str] = Field(default_factory=list)
    handoff_risks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def preserve_existing_readiness(cls, data):
        # Earlier schema-2 complete handoffs already required execution readiness.
        if isinstance(data, dict) and "execution_ready" not in data:
            data = dict(data)
            data["execution_ready"] = (
                data.get("handoff_complete") is True
                and data.get("review_status") == "REVIEWABLE"
                and not data.get("blockers")
                and not data.get("decision_ids")
            )
        return data


class PublicationSnapshot(Record):
    run: dict
    context: ResearchContext | None
    version: DesignVersion
    issues: list[ReviewIssue]
    decisions: list[ResearcherDecision]
    assessment: ReadinessAssessment | None
    approval: Approval | None
    calculations: list["CalculationResult"] = Field(default_factory=list)
    binding: ArtifactBinding


class CalculationRequest(Record):
    binding: ArtifactBinding
    scenario_id: str
    design: Design
    overrides: dict[str, Quantity] = Field(default_factory=dict)
    preview_patch: Patch | None = None


class CalculationResult(Record):
    scenario_id: str = ""
    counterfactual: bool = False
    id: str
    request_hash: str
    binding: ArtifactBinding
    method_id: str
    implementation: str = "scipy-noncentral-t"
    applicability: list[str] = Field(default_factory=list)
    normalized_inputs: dict[str, float] = Field(default_factory=dict)
    input_provenance: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    status: Literal["computed", "not_computed"]
    power: float | None = None
    required_n_per_arm: int | None = None
    required_clusters_per_arm: int | None = None
    df: int | None = None
    mde: float | None = None
    reasons: list[str] = Field(default_factory=list)


AgentTask.model_rebuild()
PublicationSnapshot.model_rebuild()
