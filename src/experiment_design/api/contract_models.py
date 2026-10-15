"""Wire models of the research engine contract, written from the vendored JSON Schema.

The contract is shared as documents (``tests/contract/vendor``), never as code. Receivers
ignore unknown fields, except secret-bearing and telemetry models, which reject them.
"""

import re
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

CONTRACT_VERSION = "1.4.0"

# Schema IDs
I2H_HYPOTHESES = "i2h.hypotheses/v1"
I2H_EVIDENCE = "i2h.evidence/v1"
EXD_EXP_PLAN = "exd.exp_plan/v1"
PLATFORM_DATASET = "platform.dataset/v1"
EXD_RESOURCES = "exd.resources/v1"
EXD_EXPERIMENT_INDEX = "exd.experiment_index/v1"
REVIEW_FINDINGS = "review.findings/v1"
REVISION_RESPONSE = "revision.response/v1"
REVISION_DIFF = "revision.diff/v1"
PIPELINE_FEEDBACK = "pipeline.feedback/v1"
PIPELINE_RECOMMENDATION = "pipeline.recommendation/v1"

# Relative engine paths (the base URL carries the prefix)
ENGINE_HEALTH = "/health"
ENGINE_CAPABILITIES = "/capabilities"
ENGINE_RUNS = "/runs"
ENGINE_RUN = "/runs/{engine_run_id}"
ENGINE_RUN_EVENTS = "/runs/{engine_run_id}/events"
ENGINE_RUN_GATE = "/runs/{engine_run_id}/gates/{gate_id}"
ENGINE_RUN_PAUSE = "/runs/{engine_run_id}/pause"
ENGINE_RUN_RESUME = "/runs/{engine_run_id}/resume"
ENGINE_RUN_CANCEL = "/runs/{engine_run_id}/cancel"
CALLBACK_EVENTS = "/events"
CALLBACK_ARTIFACTS = "/artifacts"
CALLBACK_TELEMETRY = "/telemetry"
SERVICE_KEY_HEADER = "X-Service-Key"
REQUEST_ID_HEADER = "X-Request-ID"
SELECTION_HYPOTHESIS_IDS = "hypothesis_ids"

# Error codes
SERVICE_KEY_INVALID = "SERVICE_KEY_INVALID"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
GATE_NOT_OPEN = "GATE_NOT_OPEN"
RUN_CLOSED = "RUN_CLOSED"
EVENT_GAP = "EVENT_GAP"
STAGE_CLOSED = "STAGE_CLOSED"
VALIDATION_ERROR = "VALIDATION_ERROR"
GATE_OPTION_INVALID = "GATE_OPTION_INVALID"
GATE_ACTION_INVALID = "GATE_ACTION_INVALID"
SHA256_MISMATCH = "SHA256_MISMATCH"
ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
FAILURE_ENGINE_RESTARTED = "engine_restarted"


def is_compatible(version: str) -> bool:
    """Same major version; any minor version is accepted."""
    return version.split(".")[0] == CONTRACT_VERSION.split(".")[0]


class Wire(BaseModel):
    """Receivers ignore unknown fields (CONTRACT-RULES section 4)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Strict(BaseModel):
    """Secret-bearing and telemetry models reject unknown fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Phase(StrEnum):
    drafting = "drafting"
    reviewing = "reviewing"
    revising = "revising"
    awaiting_researcher = "awaiting_researcher"
    published = "published"
    failed = "failed"
    cancelled = "cancelled"
    paused = "paused"


TERMINAL_PHASES = frozenset({Phase.published, Phase.failed, Phase.cancelled})


class GateKind(StrEnum):
    question = "question"
    artifact_review = "artifact_review"


class EventClass(StrEnum):
    lifecycle = "lifecycle"
    activity = "activity"


class InteractionMode(StrEnum):
    auto = "auto"
    light = "light"
    copilot = "copilot"
    full = "full"


class ModelRole(StrEnum):
    stage = "stage"
    review = "review"


class Severity(StrEnum):
    blocking = "blocking"
    major = "major"
    minor = "minor"


class FindingCategory(StrEnum):
    leakage = "leakage"
    baseline = "baseline"
    control = "control"
    statistics = "statistics"
    reproducibility = "reproducibility"
    evidence = "evidence"
    other = "other"


class FindingKind(StrEnum):
    fix = "fix"
    needs_evidence = "needs_evidence"


# --- capabilities and health -------------------------------------------------------------


class ResearchProfile(Wire):
    domain: str | None = None
    research_type: str | None = None


class OutputDecl(Wire):
    schema_id: str = Field(alias="schema")
    primary: bool = False


class StepInfo(Wire):
    step_key: str
    label: str
    order: int = Field(ge=1)


class Capabilities(Wire):
    engine_key: str
    engine_version: str
    contract_version: str
    stage_keys: list[str]
    input_schemas: list[str] = Field(default_factory=list)
    feedback_schemas: list[str] = Field(default_factory=list)
    output_schemas: list[OutputDecl]
    steps: list[StepInfo]
    question_gates: dict[str, list[str]] = Field(default_factory=dict)
    activity_types: list[str] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    optional_integrations: list[str] = Field(default_factory=list)
    providers: list[Literal["openai_compatible", "bedrock"]] = Field(default_factory=list)
    auto_revision: bool
    reuses_prior_run: bool = False
    profiles: list[ResearchProfile]

    @model_validator(mode="after")
    def platform_capability_rules(self) -> "Capabilities":
        # The Platform's model enforces this; JSON Schema cannot express it.
        if not any(p.domain is None and p.research_type is None for p in self.profiles):
            raise ValueError("profiles must include base")
        if sum(output.primary for output in self.output_schemas) != 1:
            raise ValueError("output_schemas must contain exactly one primary")
        if any(not a.startswith(self.engine_key + ".") for a in self.activity_types):
            raise ValueError("activity_types must use engine_key prefix")
        if set(self.integrations) & set(self.optional_integrations):
            raise ValueError("integrations and optional_integrations must not overlap")
        return self


class Health(Wire):
    status: Literal["ok", "degraded", "down"]
    engine_version: str
    contract_version: str
    active_runs: int = Field(ge=0)
    checks: dict[str, bool] | None = None
    last_error: str | None = None


# --- run creation ----------------------------------------------------------------------


class ModelConfig(Strict):
    provider: Literal["openai_compatible", "bedrock"]
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: SecretStr | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider, self.model)


class Models(Strict):
    stage: ModelConfig
    review: ModelConfig

    @model_validator(mode="after")
    def review_differs_from_stage(self) -> "Models":
        if self.stage.identity == self.review.identity:
            raise ValueError("review model must differ from stage model")
        return self


class ArtifactRef(Wire):
    artifact_id: str
    schema_id: str = Field(alias="schema")
    version: int = Field(ge=1)
    sha256: str
    download_url: str
    primary: bool = False


class Decisions(Wire):
    actor: Literal["user", "system"]
    decided_by: str | None = None
    note: str | None = None
    selection: dict[str, list[str]] = Field(default_factory=dict)


class PipelineFeedback(Wire):
    source_stage_key: str
    artifact_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class Inputs(Wire):
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    decisions: Decisions | None = None
    feedback: list[PipelineFeedback] = Field(default_factory=list)
    history: list[ArtifactRef] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    prior_engine_run_id: str | None = None


class ReviewPolicy(Wire):
    max_auto_revisions: int = Field(default=2, ge=0)


class RunCreateRequest(Strict):
    contract_version: str
    platform_run_id: str
    stage_run_id: str
    stage_key: str
    iteration: int = Field(default=1, ge=1)
    interaction_mode: InteractionMode
    models: Models
    integrations: dict[str, SecretStr] = Field(default_factory=dict)
    budget_usd: Decimal
    review_policy: ReviewPolicy = Field(default_factory=ReviewPolicy)
    profile: ResearchProfile = Field(default_factory=ResearchProfile)
    inputs: Inputs = Field(default_factory=Inputs)
    callback_url: str


class RunCreateResponse(Wire):
    engine_run_id: str
    phase: Phase
    created: bool


# --- state, gates, events ----------------------------------------------------------------


class FailureInfo(Wire):
    code: str
    message: str


class GateOption(Wire):
    option_id: str
    label: str


class Gate(Wire):
    gate_id: str
    kind: GateKind
    step_key: str | None
    prompt: str
    options: list[GateOption] = Field(default_factory=list)
    default_option_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    opened_at: datetime

    @model_validator(mode="after")
    def options_match_kind(self) -> "Gate":
        # The Platform's model enforces this; JSON Schema cannot express it.
        if self.kind is GateKind.question:
            ids = [option.option_id for option in self.options]
            if not 2 <= len(ids) <= 4 or self.default_option_id not in ids:
                raise ValueError("question requires 2-4 options and a default in those options")
        elif self.options or not self.artifact_ids:
            raise ValueError("artifact_review requires artifact_ids and no options")
        return self


class RunState(Wire):
    engine_run_id: str
    stage_run_id: str
    phase: Phase
    step_key: str | None
    waiting_input: bool
    open_gate: Gate | None
    failure: FailureInfo | None = None
    last_source_seq: int = Field(default=0, ge=0)
    cost_usd: Decimal = Decimal(0)

    @model_validator(mode="after")
    def failure_matches_phase(self) -> "RunState":
        # The Platform's model enforces this; JSON Schema cannot express it.
        if (self.phase is Phase.failed) != (self.failure is not None):
            raise ValueError("failure is required exactly when phase is failed")
        return self


class Anchor(Wire):
    locator: str = Field(min_length=1)
    artifact_id: str | None = None


class Finding(Wire):
    finding_id: str
    anchor: Anchor
    severity: Severity
    category: FindingCategory = FindingCategory.other
    kind: FindingKind = FindingKind.fix
    issue: str = Field(min_length=1)
    suggestion: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    author: Literal["reviewer", "researcher"]


class GateAnswer(Wire):
    action: Literal["answer", "approve", "comment"]
    actor: Literal["user", "system"]
    decided_by: str | None = None
    option_id: str | None = None
    text: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class PhaseChanged(Wire):
    from_: Phase | None = Field(alias="from")


class StepChanged(Wire):
    from_: str | None = Field(alias="from")


class GateClosed(Wire):
    gate_id: str
    action: Literal["answer", "approve", "comment"]
    actor: Literal["user", "system"]


class ReviewCompleted(Wire):
    round: int = Field(ge=1)
    passed: bool
    artifact_id: str | None
    error: str | None = None


class ArtifactDelivered(Wire):
    artifact_id: str
    schema_id: str = Field(alias="schema")
    version: int = Field(ge=1)
    primary: bool


class RunPublished(Wire):
    artifact_id: str


class EmptyPayload(Wire):
    pass


class TokenUsage(Wire):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class RunCost(Wire):
    cost_usd: Decimal | None = None
    usage: dict[str, TokenUsage] = Field(default_factory=dict)


class EventEnvelope(Wire):
    source_seq: int = Field(ge=1)
    type: str
    event_class: EventClass = Field(alias="class")
    occurred_at: datetime
    phase: Phase | None = None
    step_key: str | None = None
    actor: str | None = None
    summary: str | None = None
    refs: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def conditions_of_the_event_type(self) -> "EventEnvelope":
        """The conditions the schema attaches to each type (allOf), checked on the way out."""
        # The Platform's model enforces these; JSON Schema cannot express them.
        if self.event_class is EventClass.activity:
            if self.type in EVENT_PAYLOADS or not ACTIVITY_TYPE.fullmatch(self.type):
                raise ValueError("activity type requires an engine prefix")
            if not all(v and v.strip() for v in (self.step_key, self.actor, self.summary)):
                raise ValueError("an activity needs step_key, actor and summary")
            return self
        if self.type not in EVENT_PAYLOADS:
            raise ValueError("unknown lifecycle type")
        payload_model = EVENT_PAYLOADS.get(self.type)
        if payload_model is not None:
            payload_model.model_validate(self.payload)
        if self.type in PHASE_REQUIRED and self.phase is None:
            raise ValueError(f"{self.type} needs a phase")
        if self.type in FIXED_PHASE and self.phase is not FIXED_PHASE[self.type]:
            raise ValueError(f"{self.type} needs phase {FIXED_PHASE[self.type].value}")
        if self.type == "run.step_changed" and not self.step_key:
            raise ValueError("run.step_changed needs a step_key")
        return self


EVENT_PAYLOADS: dict[str, type[BaseModel]] = {
    "run.phase_changed": PhaseChanged,
    "run.step_changed": StepChanged,
    "run.gate_opened": Gate,
    "run.gate_closed": GateClosed,
    "review.completed": ReviewCompleted,
    "artifact.delivered": ArtifactDelivered,
    "run.published": RunPublished,
    "run.failed": FailureInfo,
    "run.cancelled": EmptyPayload,
    "run.cost": RunCost,
}
ACTIVITY_TYPE = re.compile(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_.]*")
PHASE_REQUIRED = frozenset({"run.phase_changed", "run.gate_opened"})
FIXED_PHASE = {
    "run.published": Phase.published,
    "run.failed": Phase.failed,
    "run.cancelled": Phase.cancelled,
}


class EventBatch(Wire):
    events: list[EventEnvelope]


class EventPage(Wire):
    events: list[EventEnvelope]


class ArtifactMeta(Wire):
    schema_id: str = Field(alias="schema")
    version: int = Field(ge=1)
    filename: str
    media_type: str
    sha256: str
    primary: bool = False
    review_round: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetDescriptor(Wire):
    dataset_version_id: str
    version_number: int = Field(ge=1)
    original_filename: str
    sha256: str
    row_count: int | None = None
    source_type: str
    download_url: str


class ReviewFindings(Wire):
    round: int = Field(ge=1)
    reviewed_artifact_id: str
    reviewer_model: str
    findings: list[Finding]
    passed: bool

    @model_validator(mode="after")
    def passed_matches_findings(self) -> "ReviewFindings":
        # The Platform's model enforces this; JSON Schema cannot express it.
        if self.passed != (not any(f.severity == "blocking" for f in self.findings)):
            raise ValueError("passed must match absence of blocking findings")
        return self


class RevisionItem(Wire):
    finding_id: str
    outcome: Literal["fixed", "rebutted", "not_fixed"]
    note: str | None = None

    @model_validator(mode="after")
    def note_unless_fixed(self) -> "RevisionItem":
        # The Platform's model enforces this; JSON Schema cannot express it.
        if self.outcome != "fixed" and not (self.note and self.note.strip()):
            raise ValueError("rebutted and not_fixed require a note")
        return self


class RevisionResponse(Wire):
    round: int = Field(ge=1)
    artifact_before: str
    artifact_after: str
    items: list[RevisionItem]


class RevisionDiff(Wire):
    round: int = Field(ge=1)
    artifact_before: str
    artifact_after: str
    format: Literal["unified"] = "unified"
    patch: str


class PipelineRecommendation(Wire):
    action: Literal["proceed", "refine", "pivot"]
    reason: str
    outcome: Literal["positive", "negative", "inconclusive"] | None = None
    target_stage_key: str | None = None
    selection: dict[str, list[str]] = Field(default_factory=dict)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    profile: ResearchProfile | None = None


# --- telemetry ---------------------------------------------------------------------------


class LlmCall(Strict):
    role: ModelRole
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int = Field(ge=0)
    outcome: Literal["ok", "error", "timeout"]


class TelemetryRecord(Strict):
    occurred_at: datetime
    level: Literal["DEBUG", "INFO", "WARN", "ERROR"]
    message: str = Field(max_length=500)
    platform_run_id: str
    stage_run_id: str
    engine_run_id: str
    phase: Phase | None = None
    step_key: str | None = None
    llm: LlmCall | None = None


class TelemetryBatch(Strict):
    records: list[TelemetryRecord]


# Wire models by schema file name, for the vendored-contract tests.
MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        Anchor, ArtifactDelivered, ArtifactMeta, ArtifactRef, Capabilities, DatasetDescriptor,
        Decisions, EmptyPayload, EventBatch, EventEnvelope, EventPage, FailureInfo, Finding, Gate,
        GateAnswer, GateClosed, GateOption, Health, Inputs, LlmCall, ModelConfig, Models,
        OutputDecl, PhaseChanged, PipelineFeedback, PipelineRecommendation, ResearchProfile,
        ReviewCompleted, ReviewFindings, ReviewPolicy, RevisionDiff, RevisionItem,
        RevisionResponse, RunCost, RunCreateRequest, RunCreateResponse, RunPublished, RunState,
        StepChanged, StepInfo, TelemetryBatch, TelemetryRecord, TokenUsage,
    )
}  # fmt: skip
