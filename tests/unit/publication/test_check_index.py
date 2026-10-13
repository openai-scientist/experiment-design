"""Deterministic mismatches cannot be certified by a plausible index."""

import hashlib

import pytest

from experiment_design.publication.experiment_index import ExperimentIndex, check_index
from tests.unit.publication.test_experiment_index import INDEX, PLAN


def valid_index():
    return ExperimentIndex.model_validate(INDEX).model_copy(
        update={"plan_sha256": hashlib.sha256(PLAN.encode()).hexdigest()}
    )


def test_matching_index_has_no_findings():
    assert check_index(valid_index(), PLAN, ["H1"]) == []


@pytest.mark.parametrize("defect", ["hash", "heading", "unindexed", "hypothesis", "duplicate"])
def test_each_mismatch_is_a_material_reproducibility_finding(defect):
    index, plan, ids = valid_index(), PLAN, ["H1"]
    if defect == "hash":
        index.plan_sha256 = "wrong"
    elif defect == "heading":
        index.experiments[0].plan_section = "## Missing heading"
    elif defect == "unindexed":
        plan += "\n## Experiment E2: Other\n"
        index.plan_sha256 = hashlib.sha256(plan.encode()).hexdigest()
    elif defect == "hypothesis":
        ids.append("H2")
    elif defect == "duplicate":
        index.experiments.append(index.experiments[0].model_copy())
    findings = check_index(index, plan, ids)
    assert len(findings) == 1
    assert findings[0].category == "reproducibility"
    assert findings[0].severity == "MAJOR"
    assert findings[0].anchor and findings[0].suggestion


def test_unparseable_hypothesis_ids_are_disclosed():
    findings = check_index(valid_index(), PLAN, [])
    assert len(findings) == 1 and findings[0].severity == "MINOR"
