import json

import pytest

from experiment_design.agents.tools import run_specialist
from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput, WorkflowError
from tests.integration.review.test_markdown_workflow import store_for
from tests.support.providers import Provider


def test_question_without_choices_retries_once_then_stops(tmp_path):
    bad = {"markdown": "Brief", "needs_input": True, "question": "Which population?"}
    provider = Provider([bad, bad])
    store = store_for(tmp_path)
    with pytest.raises(WorkflowError, match="question"):
        run_specialist("framing", store, provider, RunConfig(), {}, [])
    assert store.state.llm_tokens == 10
    assert not store.state.usage_unknown


def test_question_preserves_explicit_default(tmp_path):
    provider = Provider(
        [
            {
                "markdown": "Brief",
                "needs_input": True,
                "question": "Which population?",
                "options": ["Adults", "Children", "Both"],
                "default": 3,
            }
        ]
    )
    output = run_specialist("framing", store_for(tmp_path), provider, RunConfig(), {}, [])
    assert output.default == 3


def test_missing_revision_response_is_honest_after_one_retry(tmp_path):
    body = {
        "markdown": "Revised",
        "responses": [
            {"finding_id": "F1-1", "outcome": "rebutted", "note": "Source fixes the comparison"}
        ],
    }
    provider = Provider([body, body])
    output = run_specialist(
        "revision",
        store_for(tmp_path),
        provider,
        RunConfig(),
        {"revision_findings": json.dumps([{"finding_id": "F1-1"}, {"finding_id": "F1-2"}])},
        [],
    )
    assert [(r.finding_id, r.outcome, r.note) for r in output.responses] == [
        ("F1-1", "rebutted", "Source fixes the comparison"),
        ("F1-2", "not_fixed", "no response from reviser"),
    ]


def test_revision_rebuttal_requires_grounds():
    valid = AgentOutput(
        markdown="Revised",
        responses=[
            {"finding_id": "F1-1", "outcome": "rebutted", "note": "Preserve confirmed intent"}
        ],
    )
    assert valid.responses[0].outcome == "rebutted"
    with pytest.raises(ValueError):
        AgentOutput(markdown="Revised", responses=[{"finding_id": "F1-1", "outcome": "rebutted"}])
