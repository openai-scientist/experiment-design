"""Small records shared by the seven-stage Markdown workflow."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkflowError(ValueError):
    pass


class ReviewerError(WorkflowError):
    """A known-usage reviewer failure, distinct from a budget stop."""


class RunInterrupted(Exception):
    """A cancel or pause request reached the workflow; not a workflow failure."""

    def __init__(self, kind: Literal["cancel", "pause"]):
        super().__init__(kind)
        self.kind = kind


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Profile(Record):
    domain: str | None = None
    research_type: str | None = None


_ENUMERATOR = re.compile(r"^\s*(?:\(\d+\)|\d+[.)]|-)\s+")
FINDING_CATEGORIES = (
    "leakage",
    "baseline",
    "control",
    "statistics",
    "reproducibility",
    "evidence",
    "other",
    "framing",
)


class Finding(Record):
    id: str
    severity: Literal["MAJOR", "MINOR"]
    issue: str
    consequence: str
    suggestion: str
    anchor: str = ""
    category: Literal[*FINDING_CATEGORIES] = "other"
    evidence_refs: list[str] = Field(default_factory=list)


class RevisionDisposition(Record):
    finding_id: str
    outcome: Literal["fixed", "rebutted", "not_fixed"]
    note: str | None = None

    @model_validator(mode="after")
    def grounds_required(self) -> "RevisionDisposition":
        if self.outcome != "fixed" and not (self.note or "").strip():
            raise ValueError("rebutted/not_fixed require a note")
        return self


class AgentOutput(Record):
    markdown: str
    findings: list[Finding] = Field(default_factory=list)
    needs_input: bool = False
    question: str = ""
    recommended_action: str = ""
    options: list[str] = Field(default_factory=list)
    default: int = Field(default=1, ge=1, le=4)
    responses: list[RevisionDisposition] = Field(default_factory=list)

    @field_validator("markdown")
    @classmethod
    def nonblank_markdown(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("markdown must not be blank")
        return value

    @field_validator("question")
    @classmethod
    def question_not_whitespace(cls, value: str) -> str:
        if value and not value.strip():
            raise ValueError("question must be empty or contain text")
        return value

    @field_validator("options")
    @classmethod
    def unnumbered_options(cls, value: list[str]) -> list[str]:
        return [_ENUMERATOR.sub("", option, count=1) for option in value]

    @model_validator(mode="after")
    def valid_options(self) -> "AgentOutput":
        if not self.options:
            if self.question.strip():
                raise ValueError("question requires 2 to 4 options")
            return self
        if not self.question.strip():
            raise ValueError("options require a nonblank question")
        if not 2 <= len(self.options) <= 4:
            raise ValueError("options must be empty or contain 2 to 4 items")
        if any(not option.strip() for option in self.options):
            raise ValueError("options must not be blank")
        if len({option.strip().casefold() for option in self.options}) != len(self.options):
            raise ValueError("options must be distinct")
        if self.default > len(self.options):
            raise ValueError("question default must select a listed option")
        return self


class RunState(Record):
    run_id: str
    profile: Profile | None = None
    stage: int = Field(default=1, ge=1, le=7)
    status: str = "RUNNING"
    attempt: int = Field(default=1, ge=1)
    refinements: int = Field(default=0, ge=0)
    llm_tokens: int = Field(default=0, ge=0)
    llm_token_limit: int = Field(default=1_000_000, gt=0)
    jev_calls: int = Field(default=0, ge=0)
    usage_unknown: bool = False
    plan_stage: int | None = None
    plan_name: str | None = None
    plan_hash: str | None = None
    review_complete: bool = False
    approval: str = "pending"
    question: str = ""
    question_kind: str = ""
    question_options: list[str] = Field(default_factory=list)
    question_default: int = Field(default=1, ge=1, le=4)


class Quantity(Record):
    value: float | None = None
    unit: str = ""
    provenance: str
    source_ref: str | None = None
    source_span: str | None = None
    note: str = ""


class ClusterInputs(Record):
    unit: str
    size: Quantity | None = None
    icc: Quantity | None = None
    clusters_per_arm: int | None = None
    equal_size: bool | None = None
    independent_clusters: bool | None = None


class CalculationRequest(Record):
    calculator_id: str | None = None
    model: str | None = None
    contrast_type: str | None = None
    direction: str | None = None
    outcome_kind: str | None = None
    outcome_unit: str = ""
    alpha: float | None = None
    sidedness: str | None = None
    margin: Quantity | None = None
    effect: Quantity | None = None
    sd: Quantity | None = None
    n_per_arm: int | None = None
    target_power: float | None = None
    common_variance: bool | None = None
    allocation: str = "1:1"
    unit_of_randomization: str | None = None
    unit_of_analysis: str | None = None
    cluster: ClusterInputs | None = None
    p_discordant_10: Quantity | None = None
    p_discordant_01: Quantity | None = None
    n_pairs: int | None = None
    family_size: int | None = None
    p_values: list[float] | None = None
    values: list[float] | None = None
    confidence: float | None = None
    a12: Quantity | None = None
    cliffs_delta: Quantity | None = None
    resamples: int | None = None
    random_seed: int | None = None


class CalculationResult(Record):
    id: str
    request_hash: str
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
    ci_low: float | None = None
    ci_high: float | None = None
    adjusted: list[float] | None = None
