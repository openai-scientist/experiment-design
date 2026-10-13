"""A separate index bound to exact reviewed Markdown, never a replacement for it."""

import hashlib
from typing import Literal

from pydantic import Field

from experiment_design.agents.structured import structured_call
from experiment_design.config import RunConfig
from experiment_design.contracts import Record
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient


class IndexedExperiment(Record):
    experiment_id: str
    type: Literal["base", "comparative_benchmark", "controlled_experiment", "ablation"]
    hypothesis_ids: list[str] = Field(min_length=1)
    datasets: list[str]
    baselines: list[str]
    metrics: list[str]
    controls: list[str]
    seeds: int | None = None
    compute_budget: str | None = None
    plan_section: str


class ExperimentIndex(Record):
    schema_id: Literal["exd.experiment_index/v1"] = Field("exd.experiment_index/v1", alias="schema")
    plan_sha256: str
    experiments: list[IndexedExperiment] = Field(min_length=1)


def extract_index(
    store: RunStore, llm: LLMClient, cfg: RunConfig, plan_md: str, hypothesis_md: str
) -> ExperimentIndex:
    digest = hashlib.sha256(plan_md.encode("utf-8")).hexdigest()
    index = structured_call(
        store,
        llm,
        cfg,
        "indexer",
        {
            "plan": plan_md,
            "hypotheses": hypothesis_md,
            "plan_sha256": digest,
        },
        ExperimentIndex,
    )
    return index.model_copy(update={"plan_sha256": digest})
