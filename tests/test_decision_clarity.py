"""Initial synthesis sees existing decisions without acquiring researcher authority."""

import ast
import hashlib

from fakes import ScriptedJudge, ScriptedLLM, submit
from test_stage_pipeline import source_context, source_snapshot, supported_design

from experiment_design.agents import design as designer
from experiment_design.agents.tools import ToolContext
from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionResponse, ResearcherDecision, StageResult
from experiment_design.decisions import apply_decision, interpret, respond
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, run_stage, start_run


def focus_from(brief):
    return ast.literal_eval(brief.rsplit("\nFocus: ", 1)[1])


def unconfirmed_intake():
    snapshot, context = source_snapshot(), source_context()
    source = snapshot.artifacts[0]
    source.text = source.text.replace(context.package.inference.estimand + "\n", "")
    source.sha256 = hashlib.sha256(source.text.encode()).hexdigest()
    context.package.inference.estimand = None
    context.field_annotations.pop("estimand")
    context.unknowns = ["Estimand requires researcher clarification"]
    context.decision_ids = ["estimand"]
    return snapshot, context


def enter_understanding(store, deps, snapshot):
    run = start_run(snapshot, deps, {})
    store.commit_stage(run, run_stage(run, "S1", deps), "S2")
    return run


def test_initial_synthesis_receives_s2_pending_question_without_resolving_it(tmp_path):
    snapshot, context = unconfirmed_intake()
    store = Store(tmp_path / "pending.db")
    try:

        def understood(messages, tools):
            import json

            inputs = json.loads(messages[1]["content"].rsplit("\n", 1)[1])
            return submit(
                "submit_understanding",
                {
                    "context": context.model_dump(mode="json"),
                    "decisions": [
                        {
                            "id": "estimand",
                            "run_id": inputs["run_id"],
                            "binding": inputs["binding"],
                            "problem": "Specify target population and assigned-treatment contrast",
                            "affected_paths": ["/context/package/inference/estimand"],
                            "recommendation": "Clarify the estimand; do not infer intent",
                        }
                    ],
                },
            )

        def synthesize(messages, tools):
            focus = focus_from(messages[1]["content"])
            ledger = focus.get("researcher_decisions", [])
            assert len(ledger) == 1, "Initial synthesis did not receive the existing S2 decision"
            assert ledger[0] == store.decision(run, "estimand").model_dump(mode="json")
            assert ledger[0]["status"] == "IDENTIFIED" and ledger[0]["responses"] == []
            assert ledger[0]["affected_paths"] == ["/context/package/inference/estimand"]
            return submit(
                "submit_design",
                {
                    "design": supported_design().model_dump(mode="json"),
                    "rationale": "Tentative draft",
                },
            )

        deps = Deps(
            store,
            ScriptedLLM([understood, synthesize]),
            ScriptedJudge([{"next_action": "CONTINUE"}]),
            RunConfig(),
        )
        run = enter_understanding(store, deps, snapshot)
        run_stage(run, "S2", deps)
        decision_before = store.decision(run, "estimand")
        result = run_stage(run, "S3", deps)
        assert result.status == "COMPLETE"
        assert store.decisions(run) == [decision_before]
        assert store.version(run).design.claim.estimand.origin == "designer_default"
        assert store.context(run).unknowns == ["Estimand requires researcher clarification"]
        assert not store.approvals(run)
    finally:
        store.close()


def test_initial_synthesis_receives_applied_reply_with_pending_verification(tmp_path):
    snapshot, context = unconfirmed_intake()
    store = Store(tmp_path / "applied.db")
    try:
        run = store.create_staged_run(snapshot, {})
        store.commit_stage(
            run,
            StageResult(
                stage_id="S1",
                status="COMPLETE",
                binding=store.binding(run),
                output_refs=[snapshot.id],
                reason="Fixture source captured",
            ),
            "S2",
        )
        store.save_context(run, context)
        decision = store.save_decision(
            ResearcherDecision(
                id="estimand",
                run_id=run,
                binding=store.binding(run),
                status="AWAITING_RESEARCHER",
                problem="Specify the intended estimand",
                affected_paths=["/context/package/inference/estimand"],
            )
        )
        response = DecisionResponse(
            revision=1,
            action="PROPOSE_ALTERNATIVE",
            text="ITT difference in class means",
            researcher="Fixture PI",
            response_id="fixture-answer",
        )
        answered = respond(store, run, decision.id, response)
        store.save_decision(
            interpret(
                answered,
                context,
                {
                    "authorized": True,
                    "ambiguous": False,
                    "context_patch": {
                        "ops": [
                            {"path": "/context/package/inference/estimand", "value": response.text}
                        ],
                        "rationale": "Implement exact fixture answer",
                    },
                },
            )
        )
        applied = apply_decision(store, run, decision.id, context=context, patch=None)

        def synthesize(messages, tools):
            ledger = focus_from(messages[1]["content"]).get("researcher_decisions", [])
            assert ledger == [applied.model_dump(mode="json")], (
                "Recorded intent is missing from synthesis"
            )
            assert ledger[0]["responses"][0]["researcher"] == "Fixture PI"
            assert ledger[0]["responses"][0]["response_id"] == "fixture-answer"
            assert ledger[0]["responses"][0]["at"] == response.model_dump(mode="json")["at"]
            assert ledger[0]["verification"]["status"] == "pending"
            return submit(
                "submit_design",
                {
                    "design": supported_design().model_dump(mode="json"),
                    "rationale": "Recorded intent",
                },
            )

        deps = Deps(store, ScriptedLLM([synthesize]), ScriptedJudge([]), RunConfig())
        # Use a real stage transition; this fixture does not answer a historical run.
        store.commit_stage(
            run,
            StageResult(
                stage_id="S2",
                status="COMPLETE",
                binding=store.binding(run),
                reason="Fixture intake",
            ),
            "S3",
        )
        run_stage(run, "S3", deps)
        assert store.version(run).design.claim.estimand.value == response.text
        assert store.version(run).design.claim.estimand.origin == "researcher"
        annotation = store.context(run).field_annotations["estimand"]
        assert annotation.source_ref == "researcher:estimand:fixture-answer"
        assert store.decision(run, "estimand").responses == [response]
        assert store.decision(run, "estimand").verification["status"] == "pending"
        assert not store.approvals(run)
    finally:
        store.close()


def test_initial_decision_visibility_respects_binding_cutoff_and_purpose(tmp_path):
    snapshot, context = unconfirmed_intake()
    store = Store(tmp_path / "bound.db")
    try:
        run = store.create_staged_run(snapshot, {})
        store.save_context(run, context)
        decision = store.save_decision(
            ResearcherDecision(
                id="estimand", run_id=run, binding=store.binding(run), problem="Original question"
            )
        )
        store.save_decision(
            ResearcherDecision(
                id="final",
                run_id=run,
                binding=store.binding(run),
                purpose="final_review",
                problem="Review version",
            )
        )
        bound = ToolContext(store, run, context.package, None, set())
        store.save_decision(
            decision.model_copy(update={"revision": 2, "problem": "Later question"})
        )
        store.save_decision(
            ResearcherDecision(
                id="later", run_id=run, binding=store.binding(run), problem="Future question"
            )
        )
        focus = focus_from(designer.brief(bound, mode="initial"))
        assert focus.get("researcher_decisions", []) == [decision.model_dump(mode="json")]
        revised = ToolContext(
            store,
            run,
            context.package,
            store.add_version(run, supported_design(), author="design", parent=None),
            set(),
        )
        supplied = [{"id": "applied-reconciliation", "status": "APPLIED"}]
        revision_focus = focus_from(
            designer.brief(revised, mode="revision", researcher_decisions=supplied)
        )
        assert revision_focus["researcher_decisions"] == supplied
    finally:
        store.close()
