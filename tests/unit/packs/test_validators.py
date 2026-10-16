"""Each pack rule must catch its scientific defect and leave a valid design alone."""

from dataclasses import replace

import pytest

from experiment_design.contracts import Profile
from experiment_design.packs import load_pack
from experiment_design.packs.validators import VALIDATORS, run
from tests.unit.publication.test_check_index import valid_index


def scenario():
    index = valid_index()
    experiment = index.experiments[0]
    experiment.type = "comparative_benchmark"
    experiment.seeds = 3
    experiment.baselines = ["baseline-a", "baseline-b"]
    experiment.compute_budget = "Equal fixed GPU-hours for each baseline"
    experiment.controls = ["flaky test reruns", "contamination exclusions"]
    resources = {
        "resources": [
            {
                "resource_id": "data",
                "kind": "dataset",
                "splits": ["train", "test"],
                "project_count": 3,
            },
            {"resource_id": "bench", "kind": "benchmark", "contamination_risk": "high"},
        ]
    }
    return index, resources


@pytest.mark.parametrize(
    "name,category",
    [
        ("seed_policy", "reproducibility"),
        ("equal_compute", "control"),
        ("split_overlap", "leakage"),
        ("benchmark_contamination", "leakage"),
        ("flaky_tests", "control"),
        ("project_diversity", "reproducibility"),
    ],
)
def test_validator_rejects_its_defect_and_accepts_valid_design(name, category):
    index, resources = scenario()
    assert VALIDATORS[name](index, resources) == []
    experiment = index.experiments[0]
    if name == "seed_policy":
        experiment.seeds = 2
    elif name == "equal_compute":
        experiment.compute_budget = None
    elif name == "split_overlap":
        resources["resources"][0]["splits"] = ["train"]
    elif name == "benchmark_contamination":
        experiment.controls = ["flaky test reruns"]
    elif name == "flaky_tests":
        experiment.controls = ["contamination exclusions"]
    else:
        resources["resources"][0]["project_count"] = 1
    findings = VALIDATORS[name](index, resources)
    assert len(findings) == 1
    assert findings[0].severity == "MAJOR" and findings[0].category == category
    assert findings[0].suggestion


def test_run_uses_only_pack_enabled_validators():
    index, resources = scenario()
    index.experiments[0].seeds = None
    index.experiments[0].compute_budget = None
    pack = replace(load_pack(Profile()), validators=("seed_policy",))
    findings = run(pack, index, resources)
    assert len(findings) == 1 and findings[0].category == "reproducibility"


def test_duplicate_split_names_are_flagged():
    index, resources = scenario()
    resources["resources"][0]["splits"] = ["train", "test", "test"]
    assert VALIDATORS["split_overlap"](index, resources)[0].category == "leakage"


@pytest.mark.parametrize(
    "control",
    ["qualifying failure must reproduce in three clean reruns", "Re-run each failing test"],
)
def test_a_rerun_rule_counts_as_flaky_test_handling(control):
    # Live 2026-10-16: a plan with a three-rerun rule but no word "flaky" was revised until the
    # engine stopped.
    index, resources = scenario()
    index.experiments[0].controls = [control]
    assert VALIDATORS["flaky_tests"](index, resources) == []
