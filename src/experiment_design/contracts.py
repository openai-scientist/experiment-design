"""Canonical scientific records and agent task contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Provenance = Literal["source", "assumption", "computed", "llm"]
Origin = Literal["package", "researcher", "designer_default", "missing"]
ContrastType = Literal["superiority", "non_inferiority", "superiority_by_margin"]
Severity = Literal["BLOCKER", "MAJOR", "MINOR"]
IssueStatus = Literal["open", "addressed", "rebutted", "resolved", "unresolved", "duplicate"]
RunStatus = Literal[
    "RUNNING", "NEEDS_RESEARCHER", "REVIEWABLE", "BLOCKED", "INCONCLUSIVE", "BUDGET_STOP"
]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Quantity(Record):
    value: float | None = None
    unit: str = ""
    provenance: Provenance
    source_ref: str | None = None
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
