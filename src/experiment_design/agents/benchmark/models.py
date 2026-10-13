"""``exd.resources/v1``: the resources a plan may rely on, with their verification status."""

from typing import Literal

from pydantic import Field

from experiment_design.contracts import Record


class Resource(Record):
    resource_id: str
    kind: Literal["dataset", "benchmark", "baseline", "metric"]
    name: str
    origin: Literal["researcher", "evidence_card", "registry", "hf_hub"]
    source_url: str | None = None
    revision: str | None = None
    license: str | None = None
    splits: list[str] = Field(default_factory=list)
    size_bytes: int | None = None
    sha256: str | None = None
    retrieved_at: str | None = None
    contamination_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    # "cited": a baseline with a source URL that was not fetched (independent review I2).
    status: Literal["verified", "cited", "unknown", "not_found"] = "unknown"
    reasons: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    rank: int | None = None
    rationale: str = ""
    hf_repo_id: str | None = None


class ResourcesDoc(Record):
    schema_id: Literal["exd.resources/v1"] = Field("exd.resources/v1", alias="schema")
    resources: list[Resource]
    minimums: dict[str, int]
    shortfalls: list[str]
