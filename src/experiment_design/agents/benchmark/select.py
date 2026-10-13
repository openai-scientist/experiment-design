"""Rank candidate resources. The LLM can only choose among them, never add verified ones."""

from typing import Literal

from pydantic import Field

from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.benchmark.survey import resource_id
from experiment_design.agents.structured import structured_call
from experiment_design.config import RunConfig
from experiment_design.contracts import Record
from experiment_design.packs import Pack
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.llm import LLMClient


class Choice(Record):
    resource_id: str
    rank: int = Field(ge=1)
    rationale: str = ""
    hypothesis_ids: list[str] = Field(default_factory=list)
    contamination_risk: Literal["low", "medium", "high", "unknown"] = "unknown"


class Addition(Record):
    name: str
    kind: Literal["dataset", "benchmark", "baseline", "metric"]


class Selection(Record):
    choices: list[Choice]
    additions: list[Addition] = Field(default_factory=list)


def select(
    store: RunStore,
    llm: LLMClient,
    cfg: RunConfig,
    pack: Pack,
    candidates: list[Resource],
    brief_md: str,
    comments: str = "",
    hypothesis_md: str = "",
) -> list[Resource]:
    selection = structured_call(
        store,
        llm,
        cfg,
        "selector",
        {
            "brief": brief_md,
            "hypotheses": hypothesis_md,
            "comments": comments,
            "minimums": {"benchmark": pack.min_benchmarks, "baseline": pack.min_baselines},
            "candidates": [
                {
                    "resource_id": c.resource_id,
                    "kind": c.kind,
                    "name": c.name,
                    "origin": c.origin,
                    "source_url": c.source_url,
                }
                for c in candidates
            ],
        },
        Selection,
    )
    by_id = {c.resource_id: c for c in candidates}
    chosen: dict[str, Resource] = {}
    for choice in sorted(selection.choices, key=lambda c: c.rank):
        found = by_id.get(choice.resource_id)
        if found is None:
            store.event("benchmark.unknown_resource", {"resource_id": choice.resource_id})
            continue
        chosen.setdefault(
            found.resource_id,
            found.model_copy(
                update={
                    "rank": choice.rank,
                    "rationale": choice.rationale,
                    "hypothesis_ids": choice.hypothesis_ids,
                    "contamination_risk": choice.contamination_risk,
                }
            ),
        )
    for candidate in candidates:  # a researcher's own dataset is never dropped
        if candidate.origin == "researcher":
            chosen.setdefault(candidate.resource_id, candidate)
    if comments.strip():
        for addition in selection.additions:
            rid = resource_id(addition.kind, addition.name)
            if rid not in by_id and rid not in chosen and addition.name.strip():
                chosen[rid] = Resource(
                    resource_id=rid,
                    kind=addition.kind,
                    name=addition.name,
                    origin="researcher",
                    status="unknown",
                    reasons=["requested by the researcher; not yet verified"],
                )
    return list(chosen.values())
