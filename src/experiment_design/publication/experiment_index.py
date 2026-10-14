"""A separate index bound to exact reviewed Markdown, never a replacement for it."""

import hashlib
import re
from typing import Literal

from pydantic import Field

from experiment_design.agents.structured import structured_call
from experiment_design.config import RunConfig
from experiment_design.contracts import Finding, Record
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


def check_index(index: ExperimentIndex, plan_md: str, hypothesis_ids: list[str]) -> list[Finding]:
    findings: list[Finding] = []

    def report(issue: str, suggestion: str, *, severity: Literal["MAJOR", "MINOR"] = "MAJOR"):
        findings.append(
            Finding(
                id=f"index-{len(findings) + 1}",
                severity=severity,
                category="reproducibility",
                anchor="Experiment index",
                issue=issue,
                consequence="The index cannot establish faithful coverage of the reviewed plan.",
                suggestion=suggestion,
            )
        )

    digest = hashlib.sha256(plan_md.encode("utf-8")).hexdigest()
    if index.plan_sha256 != digest:
        report(
            "Index hash differs from the exact plan.", "Re-extract the index from the current plan."
        )
    headings = {line for line in plan_md.splitlines() if re.match(r"^#{1,6} ", line)}
    indexed: set[str] = set()
    covered: set[str] = set()
    for experiment in index.experiments:
        if experiment.plan_section not in headings:
            report(
                f"Section missing for {experiment.experiment_id}.", "Use an exact existing heading."
            )
        if experiment.experiment_id in indexed:
            report(
                f"Duplicate experiment ID {experiment.experiment_id}.", "Use unique experiment IDs."
            )
        indexed.add(experiment.experiment_id)
        covered.update(experiment.hypothesis_ids)
    for experiment_id in dict.fromkeys(
        re.findall(r"^## Experiment (E\d+)\b", plan_md, re.MULTILINE)
    ):
        if experiment_id not in indexed:
            report(
                f"Plan experiment {experiment_id} is absent from the index.",
                "Index this experiment.",
            )
    for hypothesis_id in dict.fromkeys(hypothesis_ids):
        if hypothesis_id not in covered:
            report(
                f"Hypothesis {hypothesis_id} has no experiment.", "Cover every supplied hypothesis."
            )
    if not hypothesis_ids:
        report(
            "Hypothesis IDs could not be identified.",
            "Check hypothesis headings before relying on coverage.",
            severity="MINOR",
        )
    return findings
