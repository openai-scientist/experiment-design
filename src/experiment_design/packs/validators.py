"""Deterministic checks on declared experiment metadata, without editing the plan."""

from collections.abc import Callable, Mapping
from typing import Any

from experiment_design.contracts import Finding, WorkflowError
from experiment_design.packs import Pack
from experiment_design.publication.experiment_index import ExperimentIndex

Validator = Callable[[ExperimentIndex, Mapping[str, Any]], list[Finding]]


def _finding(rule: str, identifier: str, category: str, issue: str, suggestion: str) -> Finding:
    return Finding(
        id=f"{rule}-{identifier}",
        severity="MAJOR",
        category=category,
        anchor=f"Experiment {identifier}",
        issue=issue,
        consequence="The declared design leaves a material validity or reproducibility gap.",
        suggestion=suggestion,
    )


def seed_policy(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    return [
        _finding(
            "seed",
            e.experiment_id,
            "reproducibility",
            "Fewer than three seeds declared.",
            "Specify at least three seeds and how seed variability will be analyzed.",
        )
        for e in index.experiments
        if e.type in {"comparative_benchmark", "ablation"} and (e.seeds is None or e.seeds < 3)
    ]


def equal_compute(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    return [
        _finding(
            "compute",
            e.experiment_id,
            "control",
            "Comparable compute budget is unspecified.",
            "Declare and enforce equal compute budgets for the baselines.",
        )
        for e in index.experiments
        if len(e.baselines) >= 2 and e.compute_budget is None
    ]


def split_overlap(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    findings = []
    for position, resource in enumerate(resources.get("resources", [])):
        if resource.get("kind") != "dataset":
            continue
        splits = resource.get("splits") or []
        if "test" not in splits or len(splits) != len(set(splits)):
            findings.append(
                _finding(
                    "split",
                    str(position),
                    "leakage",
                    "Test split missing or split names overlap.",
                    "Declare distinct train/validation/test splits and verify "
                    "sample-level separation.",
                )
            )
    return findings


def benchmark_contamination(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    high_risk = any(
        resource.get("kind") == "benchmark" and resource.get("contamination_risk") == "high"
        for resource in resources.get("resources", [])
    )
    return [
        _finding(
            "contamination",
            e.experiment_id,
            "leakage",
            "High-risk benchmark lacks a control.",
            "Specify a contamination control and disclose the remaining risk.",
        )
        for e in index.experiments
        if high_risk and not any("contamination" in control.casefold() for control in e.controls)
    ]


def flaky_tests(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    return [
        _finding(
            "flaky",
            e.experiment_id,
            "control",
            "Flaky-test handling is unspecified.",
            "Declare flaky-test detection, reruns and exclusion rules before evaluation.",
        )
        for e in index.experiments
        if not any("flaky" in control.casefold() for control in e.controls)
    ]


def project_diversity(index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    return [
        _finding(
            "projects",
            str(position),
            "reproducibility",
            "Project diversity is insufficient.",
            "Identify at least two independent projects and disclose their selection.",
        )
        for position, resource in enumerate(resources.get("resources", []))
        if resource.get("kind") == "dataset"
        and (resource.get("project_count") is None or resource["project_count"] < 2)
    ]


VALIDATORS: dict[str, Validator] = {
    "seed_policy": seed_policy,
    "equal_compute": equal_compute,
    "split_overlap": split_overlap,
    "benchmark_contamination": benchmark_contamination,
    "flaky_tests": flaky_tests,
    "project_diversity": project_diversity,
}


def run(pack: Pack, index: ExperimentIndex, resources: Mapping[str, Any]) -> list[Finding]:
    findings = []
    for name in pack.validators:
        if name not in VALIDATORS:
            raise WorkflowError(f"Unknown pack validator {name!r}")
        findings.extend(VALIDATORS[name](index, resources))
    return findings
