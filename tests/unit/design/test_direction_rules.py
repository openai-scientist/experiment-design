from copy import deepcopy

import pytest
from pydantic import ValidationError

from experiment_design.design.exploration import (
    ExplorationSubmission,
    recommendation_options,
    validate_exploration,
)
from tests.support.exploration import exploration_payload


def validated(body):
    submission = ExplorationSubmission.model_validate(body)
    validate_exploration(submission, allowed_evidence_refs={"S1"}, source_refs={"S1"})
    return submission


@pytest.mark.parametrize("ids", [["probe-a", "probe-a"], [], ["foreign"]])
def test_priority_groups_cover_candidates_once(ids):
    body = exploration_payload()
    body["priorities"][0]["candidate_ids"] = ids
    with pytest.raises(ValueError):
        validated(body)


def test_conditional_candidate_requires_conditions():
    body = exploration_payload("conditional_tie")
    assert set(recommendation_options(validated(body))) == {
        "recommend:probe-a",
        "recommend:probe-b",
    }
    body["candidates"][0]["conditions"] = []
    with pytest.raises(ValueError, match="conditions"):
        validated(body)


def test_empty_candidate_set_requires_deferral():
    body = exploration_payload("missing_input")
    assert set(recommendation_options(validated(body))) == {"DEFER"}
    body["focused_questions"] = []
    with pytest.raises(ValueError, match="defer|question"):
        validated(body)


def test_unavailable_first_tier_is_excluded_without_blocking_other_probes():
    assert set(
        recommendation_options(validated(exploration_payload("unavailable_first_tier")))
    ) == {"recommend:probe-b"}


@pytest.mark.parametrize("field,value", [("approved", True), ("computed_power", 0.8), ("id", " ")])
def test_candidate_rejects_authority_fields_and_blank_ids(field, value):
    body = exploration_payload()
    body["candidates"][0][field] = value
    with pytest.raises(ValidationError):
        validated(body)


def test_nested_refs_and_intent_need_exact_admissible_sources():
    body = exploration_payload()
    assert validated(deepcopy(body)).candidates[0].evidence_refs == []
    body["priorities"][0]["evidence_refs"] = ["tool:peer"]
    with pytest.raises(ValueError, match="ref"):
        validated(body)
    body["priorities"][0]["evidence_refs"] = []
    body["intent_source_refs"] = ["tool:peer"]
    with pytest.raises(ValueError, match="ref"):
        validated(body)
