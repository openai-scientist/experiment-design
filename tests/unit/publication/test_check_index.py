"""Deterministic mismatches cannot be certified by a plausible index."""

import hashlib

import pytest

from experiment_design.publication.experiment_index import (
    ExperimentIndex,
    check_index,
    rebind_index,
)
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
    assert findings[0].severity == ("MINOR" if defect == "heading" else "MAJOR")
    assert findings[0].anchor and findings[0].suggestion


def test_unparseable_hypothesis_ids_are_disclosed():
    findings = check_index(valid_index(), PLAN, [])
    assert len(findings) == 1 and findings[0].severity == "MINOR"


PLAN2 = """# Comparative benchmark plan

## Research question and hypotheses

### H1 — primary, shifted calibration

## Experiment E1: Smoke test

## Limitations

## Limitations
"""


@pytest.mark.parametrize(
    "section,heading",
    [
        ("Comparative benchmark plan", "# Comparative benchmark plan"),  # no heading marks
        ("## research question AND hypotheses", "## Research question and hypotheses"),
        ("H1 � primary, shifted calibration", "### H1 — primary, shifted calibration"),
        ("Experiment E1", "## Experiment E1: Smoke test"),  # unique heading containing the text
        ("Limitations", "## Limitations"),  # duplicated heading lines are one heading
    ],
)
def test_a_mismatched_section_is_matched_to_the_plan_heading_by_code(section, heading):
    index = valid_index()
    index.experiments[0].plan_section = section
    index.plan_sha256 = "stale"
    fixed = rebind_index(index, PLAN2)
    assert fixed.experiments[0].plan_section == heading
    assert fixed.plan_sha256 == hashlib.sha256(PLAN2.encode()).hexdigest()
    ids = ["H1"]
    assert [
        f for f in check_index(fixed, PLAN2, ids) if "Section" in f.issue or "hash" in f.issue
    ] == []


@pytest.mark.parametrize("section", ["Nothing like it", "Experiment", "H"])
def test_an_unmatched_or_ambiguous_section_stays_a_minor_finding(section):
    plan = PLAN2 + "\n## Experiment E2: Other\n"
    index = valid_index()
    index.experiments[0].plan_section = section
    fixed = rebind_index(index, plan)
    assert fixed.experiments[0].plan_section == section
    missing = [f for f in check_index(fixed, plan, ["H1"]) if f.issue.startswith("Section missing")]
    assert [f.severity for f in missing] == ["MINOR"]


def test_a_number_never_binds_inside_a_longer_number():
    index = valid_index()
    index.experiments[0].plan_section = "Experiment 1"
    plan = "# Plan\n## Experiment 10: Foo\n"
    assert rebind_index(index, plan).experiments[0].plan_section == "Experiment 1"


@pytest.mark.parametrize("section", ["Experiment 3: Foo", "## Experiment 3 Foo", "Experiment 3"])
def test_a_section_never_binds_to_a_heading_with_other_numbers(section):
    plan = "# Plan\n## Experiment 1: Foo\n## Experiment 10: Foo\n"
    index = valid_index()
    index.experiments[0].plan_section = section
    assert rebind_index(index, plan).experiments[0].plan_section == section


def test_a_short_section_binds_to_the_heading_that_starts_with_its_number():
    plan = "# Plan\n## Experiment E1: Matched-budget Defects4J detection\n## Experiment E2: Other\n"
    index = valid_index()
    index.experiments[0].plan_section = "Experiment E1"
    bound = rebind_index(index, plan).experiments[0].plan_section
    assert bound == "## Experiment E1: Matched-budget Defects4J detection"


def test_an_exact_numbered_heading_still_binds():
    plan = "# Plan\n## Experiment 1: Foo\n## Experiment 3: Foo\n"
    index = valid_index()
    index.experiments[0].plan_section = "experiment 3 foo"
    assert rebind_index(index, plan).experiments[0].plan_section == "## Experiment 3: Foo"
