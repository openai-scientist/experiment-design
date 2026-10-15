"""A separate index bound to exact reviewed Markdown, never a replacement for it."""

import difflib
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


def _words(text: str) -> str:
    """Case, heading marks, punctuation and spacing removed."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def _closest_heading(section: str, headings: list[str]) -> str | None:
    """The one heading the section names: equal, else the only one containing it, else the
    only close one. Only an equal heading may differ in its digits. Ambiguity gives None."""
    wanted = _words(section)
    if not wanted:
        return None
    normal = {h: _words(h) for h in headings}
    exact = [h for h, n in normal.items() if n == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # "Experiment 3: Foo" must never bind to "Experiment 1: Foo".
    digits = re.findall(r"\d+", wanted)
    normal = {h: n for h, n in normal.items() if re.findall(r"\d+", n) == digits}
    contained = [h for h, n in normal.items() if wanted in n]
    if contained:
        return contained[0] if len(contained) == 1 else None
    ranked = sorted(
        ((difflib.SequenceMatcher(None, wanted, n).ratio(), h) for h, n in normal.items()),
        reverse=True,
    )
    if ranked and ranked[0][0] >= 0.75 and (len(ranked) == 1 or ranked[1][0] < ranked[0][0] - 0.1):
        return ranked[0][1]
    return None


def rebind_index(index: ExperimentIndex, plan_md: str) -> ExperimentIndex:
    """Mechanical repair by code: each plan_section becomes the plan's own heading line and
    the index is bound to the current plan, so no reviewer sees a heading-format mismatch."""
    headings = list(
        dict.fromkeys(line for line in plan_md.splitlines() if re.match(r"^#{1,6} ", line))
    )
    experiments = [
        e.model_copy(
            update={"plan_section": _closest_heading(e.plan_section, headings) or e.plan_section}
        )
        for e in index.experiments
    ]
    digest = hashlib.sha256(plan_md.encode("utf-8")).hexdigest()
    return index.model_copy(update={"experiments": experiments, "plan_sha256": digest})


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
                f"Section missing for {experiment.experiment_id}.",
                "Use an exact existing heading.",
                severity="MINOR",
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
