import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse
from experiment_design.design.decisions import respond
from experiment_design.persistence.store import Store
from experiment_design.publication import export
from experiment_design.workflow import Deps, advance, start_run
from experiment_design.workflow.review import review_actions
from tests.support.builders import design
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    normal_responses,
    source_context,
    source_snapshot,
)


def test_pending_final_review_does_not_block_scientific_response_reconciliation(tmp_path):
    from experiment_design.contracts import ResearcherDecision
    from experiment_design.runtime.llm import LLMError

    def understood(messages, tools):
        brief = json.loads(messages[1]["content"].split("\n")[-1])
        decision = ResearcherDecision(
            id="margin-review",
            run_id=brief["run_id"],
            binding=brief["binding"],
            problem="Review planning margin",
            affected_paths=["/claim/margin"],
        )
        return submit(
            "submit_understanding",
            {
                "context": source_context().model_dump(mode="json"),
                "decisions": [decision.model_dump(mode="json")],
            },
        )

    responses = normal_responses()
    responses[0] = understood
    store = Store(tmp_path / "reconcile.db")
    deps = Deps(
        store,
        ScriptedLLM(responses),
        ScriptedJudge(
            [
                {"next_action": "CONTINUE"},
                {"next_action": "REVIEW"},
                {"next_action": "QUALIFY"},
            ]
        ),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    assert not any(d.purpose == "final_review" for d in store.decisions(run))
    assert store.publication_snapshot(run).assessment.review_status == "NEEDS_INPUT"
    export(store, run, tmp_path / "draft")
    decision = store.decision(run, "margin-review")
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=decision.revision,
            action="PROPOSE_ALTERNATIVE",
            text="Use margin 2 points",
            researcher="PI",
            response_id="new-margin",
        ),
    )
    deps.llm = ScriptedLLM(
        [
            submit(
                "submit_interpretation",
                {
                    "authorized": True,
                    "ambiguous": False,
                    "patch": {
                        "ops": [{"path": "/claim/margin", "value": 2}],
                        "rationale": "Researcher margin response",
                    },
                    "explanation": "Apply exact researcher choice",
                },
            ),
            LLMError("understanding provider interrupted during reconciliation"),
        ]
    )
    with pytest.raises(LLMError, match="during reconciliation"):
        advance(run, deps)
    assert store.run(run).stage_id == "S2"
    assert store.version(run).design.claim.margin.value == 2
    assert store.decision(run, decision.id).application["response_id"] == "new-margin"
    assert not any(d.purpose == "final_review" for d in store.decisions(run))
    assert store.publication_snapshot(run).assessment is None
    assert not store.approvals(run)
    store.close()


def test_review_counters_cannot_replace_a_published_independent_cohort(tmp_path):
    store = Store(tmp_path / "no-cohort.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, source_context())
    store.add_version(run, design(), author="design", parent=None)
    store.update_run(run, stage_id="S4", reviewed_version=1, reviews_done=1)
    assert "QUALIFY" not in review_actions(store, run, deps.run)
    assert "REVIEW" in review_actions(store, run, deps.run)
    store.close()


def test_jev_receives_review_progress_and_bound_computation_limitations(tmp_path):
    from experiment_design.contracts import CalculationRequest, Observation
    from experiment_design.science.power import calculate
    from experiment_design.workflow import jev_state

    store = Store(tmp_path / "progress.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    run = start_run(source_snapshot(), deps, {})
    store.save_context(run, source_context())
    store.add_version(run, design(), author="design", parent=None)
    store.update_run(run, stage_id="S4", revisions_done=3, actions_done=5)
    result = calculate(
        CalculationRequest(
            calculator_id=(design()).analysis.method_id,
            binding=store.binding(run),
            scenario_id="base",
            design=design(),
        )
    )
    store.save_observation(
        run,
        Observation(
            id="power",
            task_id="review",
            binding=store.binding(run),
            tool_name="compute_power",
            arguments={},
            result={"value": [result.model_dump(mode="json")]},
            accepted=True,
            evidence_ref="tool:power",
        ),
    )
    state = jev_state(store, run, deps.run)
    assert state["progress"]["revisions_done"] == 3
    assert state["progress"]["reviews_done"] == 0
    assert state["progress"]["max_revisions"] == 3
    assert state["progress"]["cohorts"] == []
    assert state["calculations"][0]["status"] == "not_computed"
    assert state["calculations"][0]["reasons"]
    assert "draft" in state["objective"].lower()
    store.close()
