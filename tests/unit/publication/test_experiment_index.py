"""Extraction must validate submissions, bind exact bytes and account for all usage."""

import hashlib

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import WorkflowError
from experiment_design.publication.experiment_index import extract_index
from tests.integration.review.test_markdown_workflow import store_for
from tests.support.providers import ToolProvider

PLAN = "## Experiment E1: Comparison\nProtocol.\n"
INDEX = {
    "plan_sha256": "model-supplied-hash-must-be-overridden",
    "experiments": [
        {
            "experiment_id": "E1",
            "type": "base",
            "hypothesis_ids": ["H1"],
            "datasets": [],
            "baselines": [],
            "metrics": ["accuracy"],
            "controls": [],
            "plan_section": "## Experiment E1: Comparison",
        }
    ],
}


def test_extract_binds_exact_plan_and_counts_tokens(tmp_path):
    store = store_for(tmp_path)
    index = extract_index(store, ToolProvider([("submit", INDEX)]), RunConfig(), PLAN, "## H1")
    assert index.plan_sha256 == hashlib.sha256(PLAN.encode()).hexdigest()
    assert index.experiments[0].hypothesis_ids == ["H1"]
    assert store.state.llm_tokens == 5


def test_invalid_submission_retried_once(tmp_path):
    store = store_for(tmp_path)
    provider = ToolProvider([("submit", {}), ("submit", INDEX)])
    assert (
        extract_index(store, provider, RunConfig(), PLAN, "## H1").experiments[0].experiment_id
        == "E1"
    )
    assert store.state.llm_tokens == 10


def test_two_invalid_submissions_stop(tmp_path):
    store = store_for(tmp_path)
    with pytest.raises(WorkflowError, match="submission"):
        extract_index(
            store, ToolProvider([("submit", {}), ("submit", {})]), RunConfig(), PLAN, "## H1"
        )
    assert store.state.llm_tokens == 10


def test_exhausted_budget_prevents_call(tmp_path):
    store = store_for(tmp_path)
    store.update(llm_tokens=store.state.llm_token_limit)
    with pytest.raises(WorkflowError, match="budget"):
        extract_index(store, ToolProvider([]), RunConfig(), PLAN, "## H1")
