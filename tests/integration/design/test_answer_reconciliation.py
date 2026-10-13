import pytest

from experiment_design.config import RunConfig
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, advance, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit
from tests.support.scenarios import (
    normal_responses,
    review_responses,
    source_context,
    source_snapshot,
    supported_design,
)


def test_agent_decision_is_presented_against_committed_context(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.design.decisions import respond

    def understood(messages, tools):
        import json

        brief = json.loads(messages[-1]["content"].split("\n")[-1])
        context = source_context()
        decision = ResearcherDecision(
            id="missing-sap",
            run_id=brief["run_id"],
            binding=brief["binding"],
            problem="Confirm SAP",
            affected_paths=["/analysis/model"],
            dependencies=["synthesis"],
        )
        return submit(
            "submit_understanding",
            {
                "context": context.model_dump(mode="json"),
                "decisions": [decision.model_dump(mode="json")],
            },
        )

    store = Store(tmp_path / "clarification.db")
    deps = Deps(
        store,
        ScriptedLLM([understood, normal_responses()[1], *review_responses()]),
        ScriptedJudge([{"next_action": "ASK_RESEARCHER"}]),
        RunConfig(),
    )
    run = start_run(source_snapshot(), deps, {})
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    decision = store.decision(run, "missing-sap")
    answered = respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Use the specified SAP",
            researcher="PI",
            response_id="answer",
        ),
    )
    assert answered.status == "ANSWERED"
    assert decision.binding.context_revision == 1
    store.close()


@pytest.mark.parametrize("legacy", [False, True, "missing", "invalid"])
def test_each_researcher_decision_keeps_its_comparison_and_verification(tmp_path, legacy):
    import json

    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.design.decisions import apply_decision, interpret, respond
    from experiment_design.workflow import run_stage

    store = Store(tmp_path / "independent-decisions.db")
    run = start_run(
        source_snapshot(), Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig()), {}
    )
    store.save_context(run, source_context())
    store.add_version(run, supported_design(), author="design", parent=None)
    for name, patch in (("one", {"ops": [], "rationale": "Confirm current intent"}), ("two", None)):
        store.save_decision(
            ResearcherDecision(
                id=name,
                run_id=run,
                binding=store.binding(run),
                status="AWAITING_RESEARCHER",
                problem="Confirm current intent",
                affected_paths=["/claim/estimand"],
            )
        )
        answered = respond(
            store,
            run,
            name,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text="Keep current estimand",
                researcher="PI",
                response_id=name + "-reply",
            ),
        )
        interpreted = interpret(answered, store.context(run), {"authorized": True, "patch": patch})
        store.save_decision(interpreted)
        from experiment_design.contracts import Patch

        applied = apply_decision(
            store,
            run,
            name,
            context=store.context(run),
            patch=Patch.model_validate(patch) if patch is not None else None,
        )
        issue = next(i for i in store.issues(run) if i.id == applied.application["issue_id"])
        issue.status = "rebutted"
        issue.rebuttal = "Already implements researcher intent"
        if legacy:
            issue.addressed_in = None
        if legacy in {"missing", "invalid"}:
            applied.application["version"] = None if legacy == "missing" else 999
            store.save_decision(applied)
        store.save_issue(issue)
    store.add_version(run, supported_design(), author="design", parent=2)
    store.update_run(run, stage_id="S4")
    issues = store.issues(run)

    def verify(messages, tools):
        focus = json.loads(messages[1]["content"].rsplit("\nVerification focus: ", 1)[1])
        expected = (
            [(None, None), (None, None)] if legacy in {"missing", "invalid"} else [(2, 1), (2, 1)]
        )
        assert [
            (c["addressed_version"], c["parent_version"]) for c in focus["comparisons"]
        ] == expected
        assert all("reconciliation" in d for d in focus["researcher_clarifications"])
        return submit(
            "submit_verdicts",
            {
                "verdicts": [
                    {
                        "issue_id": issue.id,
                        "verdict": verdict,
                        "grounds": "Independent comparison",
                        "evidence_refs": ["researcher:" + name + ":" + name + "-reply"],
                    }
                    for issue, name, verdict in zip(
                        issues, ("one", "two"), ("resolved", "uncertain"), strict=True
                    )
                ],
                "regressions": [],
            },
        )

    run_stage(
        run,
        "S4",
        Deps(
            store,
            ScriptedLLM([verify]),
            ScriptedJudge(
                [
                    {"next_action": "VERIFY"},
                    {issues[0].id: "resolved", issues[1].id: "unresolved"},
                    {"next_action": "STOP"},
                ]
            ),
            RunConfig(),
        ),
    )
    assert store.decision(run, "one").verification == {
        "status": "verified",
        "evidence_refs": ["researcher:one:one-reply"],
    }
    assert store.decision(run, "two").verification["status"] != "verified"
    assert store.issues(run)[1].status == "unresolved"
    store.close()


def test_partial_decision_answers_resume_only_independent_work(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, decision = decision_state(tmp_path)
    store.update_run(run, stage_id="S4", status="NEEDS_RESEARCHER")
    waiting = store.save_decision(
        ResearcherDecision(
            id="other",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm inclusion",
            affected_paths=["/sampling/inclusion"],
        )
    )
    respond(
        store,
        run,
        decision.id,
        DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="Margin two points",
            researcher="PI",
            response_id="margin-answer",
        ),
    )
    interpretation = submit(
        "submit_interpretation",
        {
            "authorized": True,
            "ambiguous": False,
            "explanation": "Only margin was answered",
            "patch": {
                "ops": [{"path": "/claim/margin", "value": 2}],
                "rationale": "Researcher response",
            },
        },
    )
    deps = Deps(store, ScriptedLLM([interpretation]), ScriptedJudge([]), RunConfig())
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    assert store.version(run).design.claim.margin.value == 2
    assert store.decision(run, decision.id).status == "APPLIED"
    assert store.decision(run, waiting.id).status == "AWAITING_RESEARCHER"
    assert len(deps.llm.calls) == 1
    assert store.run(run).stage_id == "S2"
    store.close()


def test_two_independent_recorded_answers_apply_without_stale_failure(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, first = decision_state(tmp_path)
    store.update_run(run, stage_id="S4", status="NEEDS_RESEARCHER")
    second = store.save_decision(
        ResearcherDecision(
            id="inclusion",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm inclusion",
            affected_paths=["/sampling/inclusion"],
        )
    )
    for decision, text in ((first, "Margin two points"), (second, "Include all classes")):
        respond(
            store,
            run,
            decision.id,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text=text,
                researcher="PI",
                response_id=decision.id + "-reply",
            ),
        )
    store.save_decision(
        ResearcherDecision(
            id="remaining",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Confirm resources",
            affected_paths=["/protocol/resources"],
        )
    )
    responses = [
        submit(
            "submit_interpretation",
            {
                "authorized": True,
                "ambiguous": False,
                "explanation": "Researcher answer",
                "patch": {
                    "ops": [{"path": path, "value": value}],
                    "rationale": "Researcher response",
                },
            },
        )
        for path, value in (("/claim/margin", 2), ("/sampling/inclusion", "All classes"))
    ]
    deps = Deps(store, ScriptedLLM(responses), ScriptedJudge([]), RunConfig())
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    other = store.decision(run, second.id)
    assert other.status == "APPLIED"
    assert other.revision == 2
    assert other.responses[-1].text == "Include all classes"
    assert store.decision(run, first.id).status == "APPLIED"
    assert store.version(run).design.sampling.inclusion == "All classes"
    assert len(deps.llm.calls) == 2
    store.close()


def test_overlapping_recorded_answer_is_represented_with_raw_reply(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearcherDecision
    from experiment_design.design.decisions import respond
    from tests.support.scenarios import decision_state

    store, run, context, first = decision_state(tmp_path)
    store.update_run(run, stage_id="S4")
    second = store.save_decision(
        ResearcherDecision(
            id="also-margin",
            run_id=run,
            binding=store.binding(run),
            status="AWAITING_RESEARCHER",
            problem="Which margin?",
            affected_paths=["/claim/margin"],
        )
    )
    for decision, text in ((first, "Two points"), (second, "Keep three points")):
        respond(
            store,
            run,
            decision.id,
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text=text,
                researcher="PI",
                response_id=decision.id,
            ),
        )
    deps = Deps(
        store,
        ScriptedLLM(
            [
                submit(
                    "submit_interpretation",
                    {
                        "authorized": True,
                        "ambiguous": False,
                        "explanation": "Two points",
                        "patch": {
                            "ops": [{"path": "/claim/margin", "value": 2}],
                            "rationale": "PI",
                        },
                    },
                )
            ]
        ),
        ScriptedJudge([]),
        RunConfig(),
    )
    assert advance(run, deps) == "NEEDS_RESEARCHER"
    waiting = store.decision(run, second.id)
    assert waiting.status == "AWAITING_RESEARCHER" and waiting.revision == 2
    assert waiting.responses[-1].text == "Keep three points"
    assert len(deps.llm.calls) == 1
    assert (
        respond(
            store,
            run,
            waiting.id,
            DecisionResponse(
                revision=2,
                action="PROPOSE_ALTERNATIVE",
                text="Keep three points",
                researcher="PI",
                response_id="confirmed",
            ),
        ).status
        == "ANSWERED"
    )
    store.close()
