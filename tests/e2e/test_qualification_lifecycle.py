import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, advance, approve, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call
from tests.support.scenarios import (
    normal_responses,
    plan_body,
    review_responses,
    source_snapshot,
    supported_design,
)


def test_qualification_feedback_redesigns_and_requalifies_in_one_run(tmp_path):

    store = Store(tmp_path / "feedback.db")
    draft = supported_design()
    draft.measurement.data_collection = None

    def verified(messages, tools):
        observation = json.loads(
            next(m["content"] for m in reversed(messages) if m["role"] == "tool")
        )
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": "I1",
                        "verdict": "resolved",
                        "grounds": "The actual child patch specifies data collection",
                        "evidence_refs": [observation["evidence_ref"]],
                    }
                ],
                "regressions": [],
            },
        )

    responses = normal_responses()
    responses[1] = submit(
        "submit_design", {"design": plan_body(draft), "summary": "Incomplete draft"}
    )
    responses += [
        submit(
            "submit_revision",
            {
                "patch": {
                    "ops": [
                        {
                            "path": "/measurement/data_collection",
                            "value": "Collect end-of-term test scores using the specified rubric",
                        }
                    ],
                    "addresses": ["I1"],
                    "rationale": "Fill the qualification gap",
                }
            },
        ),
        *review_responses(),
        tool_call("read_diff", {"from_version": 1, "to_version": 2}),
        verified,
    ]
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
                {"next_action": "REDESIGN"},
                {"next_action": "REVIEW"},
                {"next_action": "VERIFY"},
                {"I1": "resolved"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert store.version(run).version == 2 and store.assessment(run).plan_complete
    assert store.issues(run)[0].status == "resolved"
    returns = [
        e
        for e in store.events(run)
        if e.type == "stage.result" and e.data["stage_id"] == "S5" and e.data["target"] == "S3"
    ]
    assert len(returns) == 1 and returns[0].data["originating_ref"] == "I1"
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert not any(e.agent_id == "handoff" for e in store.events(run))
    store.close()


@pytest.mark.parametrize("missing", ["steps", "stop_conditions", "analysis_stopping"])
def test_missing_protocol_and_stopping_cannot_be_complete(tmp_path, missing):
    store = Store(tmp_path / "empty.db")
    responses = normal_responses()
    design = supported_design()
    if missing == "analysis_stopping":
        design.analysis.stopping = None
    else:
        setattr(design.protocol, missing, [])
    responses[1] = submit("submit_design", {"design": plan_body(design), "summary": "Draft"})
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}, {"next_action": "REVIEW_PLAN"}]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "REVIEWABLE"
    assert not store.assessment(run).plan_complete
    assert not store.assessment(run).plan_complete
    with pytest.raises(ValueError):
        approve(store, run, 1, "PI")
    assert not store.approvals(run)
    store.close()
