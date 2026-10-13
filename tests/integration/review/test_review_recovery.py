import json

from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ResearcherDecision
from experiment_design.design.decisions import apply_decision, interpret, respond
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, run_stage
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    plan_body,
    source_context,
    supported_design,
)


def test_initial_draft_verification_receives_recorded_researcher_intent(tmp_path):

    from tests.support.scenarios import staged

    store = Store(tmp_path / "initial-verification.db")
    run, _ = staged(store)
    context = source_context()
    store.save_context(run, context)
    decision = store.save_decision(
        ResearcherDecision(
            id="estimand",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm estimand",
            affected_paths=["/context/package/inference/estimand"],
        )
    )
    text = "ITT mean score difference AI minus teacher among class-randomized pupils."
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text=text,
            researcher="PI",
            response_id="intent-answer",
        ),
    )
    store.save_decision(
        interpret(
            answered,
            context,
            {
                "authorized": True,
                "ambiguous": False,
                "context_patch": {
                    "ops": [{"path": "/context/package/inference/estimand", "value": text}],
                    "rationale": "Recorded researcher answer",
                },
            },
        )
    )
    apply_decision(store, run, decision.id, context=context, patch=None)
    d = supported_design()
    d.claim.estimand.value = text
    store.update_run(run, stage_id="S3")
    run_stage(
        run,
        "S3",
        Deps(
            store,
            ScriptedLLM(
                [
                    submit(
                        "submit_design",
                        {"design": plan_body(d), "summary": "Clarified draft"},
                    )
                ]
            ),
            ScriptedJudge([]),
            RunConfig(),
        ),
    )
    issue = store.issues(run)[0]

    def verify(messages, tools):
        focus = json.loads(messages[1]["content"].rsplit("\nVerification focus: ", 1)[1])
        response = focus["researcher_clarifications"][0]["response"]
        comparison = focus["comparisons"][0]
        assert response["text"] == text and response["researcher"] == "PI"
        assert comparison["parent_version"] is None and comparison["addressed_version"] == 1
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": issue.id,
                        "verdict": "resolved",
                        "grounds": "Draft implements recorded ITT; no parent exists.",
                        "evidence_refs": ["researcher:estimand:intent-answer"],
                    }
                ],
                "regressions": [],
            },
        )

    deps = Deps(
        store,
        ScriptedLLM([verify]),
        ScriptedJudge(
            [
                {"next_action": "VERIFY"},
                {issue.id: "resolved"},
                {"next_action": "STOP"},
            ]
        ),
        RunConfig(),
    )
    run_stage(run, "S4", deps)
    assert store.issues(run)[0].status == "resolved"
    assert store.decision(run, decision.id).verification["status"] == "verified"
    store.close()
