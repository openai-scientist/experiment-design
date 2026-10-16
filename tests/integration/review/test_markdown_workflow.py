import json

import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.jev import Decision
from experiment_design.runtime.llm import LLMResponse, ToolCall
from experiment_design.workflow.engine import advance, respond
from tests.support.providers import Judge, Provider, Scripted, ToolProvider


def store_for(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_text("Exact hypothesis: compare two measurements.\n", encoding="utf-8")
    return RunStore.create(tmp_path / "runs", [source], {"run": RunConfig().model_dump()}, {})


def test_independent_reviews_fresh_check_and_resume(tmp_path):
    store = store_for(tmp_path)
    provider = Provider(
        [
            {"markdown": "Brief"},
            {"markdown": "Draft"},
            {"markdown": "Method review"},
            {"markdown": "Skeptic review"},
            {"markdown": "Final review"},
        ]
    )
    state = advance(store, provider, Judge(), RunConfig())
    assert state.review_complete
    assert state.approval == "pending"
    assert len(provider.starts) == 5
    method = provider.documents(2)
    skeptic = provider.documents(3)
    assert "Exact hypothesis: compare two measurements." in method["stage-01/hypothesis.md"]
    assert method == skeptic
    assert not any("Method review" in text for text in skeptic.values())
    assert provider.documents(4)["plan"] == "Draft"
    advance(store, provider, Judge(), RunConfig())
    assert len(provider.starts) == 5


def test_stale_and_incomplete_approval_rejected(tmp_path):
    store = store_for(tmp_path)
    with pytest.raises(WorkflowError):
        respond(store, action="APPROVE", text="yes", researcher="Researcher", revision=0)
    with pytest.raises(WorkflowError):
        respond(
            store,
            action="APPROVE",
            text="yes",
            researcher="Researcher",
            revision=store.state.attempt,
        )


MAJOR = {
    "id": "gap",
    "severity": "MAJOR",
    "issue": "No control",
    "consequence": "Confounding",
    "suggestion": "Add control",
}


def happy_outputs():
    return [
        {"markdown": text}
        for text in ["Brief", "Draft", "Method review", "Skeptic review", "Final review"]
    ]


def revised(text, **fields):
    """A revision followed by the two critics' rejoinders, in invocation order."""
    return [
        {"markdown": text} | fields,
        {"markdown": "Method rejoinder"},
        {"markdown": "Skeptic rejoinder"},
    ]


def test_material_objections_reach_revision_and_fresh_check(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan", recommended_action="Added control")
    provider = Provider(outputs)
    state = advance(store, provider, Judge(), RunConfig())
    assert state.review_complete
    revision = provider.documents(4)
    assert "Method review" in revision["methodologist_review"]
    assert "Skeptic review" in revision["skeptic_review"]
    check = provider.documents(7)
    assert check["plan"] == "Revised plan"
    assert "response" in [d["name"] for d in provider.task(7)["documents"]]
    assert store.read(4, "response.md") == "Added control"
    assert "-Draft" in check["plan_diff"] and "+Revised plan" in check["plan_diff"]
    assert "--- previous_plan" in check["plan_diff"] and "+++ revised_plan" in check["plan_diff"]
    assert "Method rejoinder" in check["methodologist_rejoinder"]
    assert "Skeptic rejoinder" in check["skeptic_rejoinder"]
    assert store.read(7, "exp_plan.md") == "Revised plan"


def test_material_final_gap_exhausted_allowance_is_incomplete(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[-1]["findings"] = [MAJOR]
    state = advance(store, Provider(outputs), Judge(), RunConfig(max_refinements=0))
    assert state.status == "INCOMPLETE"
    assert not state.review_complete
    assert not store.valid(7)
    with pytest.raises(WorkflowError):
        respond(store, action="APPROVE", text="yes", researcher="R", revision=state.attempt)


def test_missing_core_answer_bound_and_preserved(tmp_path):
    store = store_for(tmp_path)
    first = Provider(
        [
            {
                "markdown": "Need population",
                "needs_input": True,
                "question": "Which population?",
                "options": ["Adults", "Children"],
            }
        ]
    )
    state = advance(store, first, Judge(), RunConfig())
    assert state.status == "AWAITING_RESEARCHER"
    previous = state.attempt
    respond(store, action="ANSWER", text="Adults", researcher="R", revision=previous)
    event = [e["data"] for e in store.events() if e["kind"] == "researcher.response"][-1]
    assert event["question"] == "Which population?"
    assert event["source_brief_hash"]
    assert event["revision"] == previous
    provider = Provider(happy_outputs())
    advance(store, provider, Judge(), RunConfig())
    replies = provider.documents(0)["researcher_responses"]
    assert "Adults" in replies and "ANSWER" in replies


def test_clarification_preserves_plan_and_does_not_confirm_intent(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    before = store.read(7, "exp_plan.md")
    respond(
        store,
        action="CLARIFY",
        text="Why this control?",
        researcher="R",
        revision=store.state.attempt,
    )
    provider = Provider([{"markdown": "Control isolates the effect"}])
    state = advance(store, provider, Judge(), RunConfig())
    assert state.approval == "pending"
    assert state.review_complete
    assert store.read(7, "exp_plan.md") == before
    assert provider.documents(0)["question"] == "Why this control?"
    assert provider.task(0)["required"] == ["plan", "question"]
    assert any(e["kind"] == "clarification.answer" for e in store.events())


def refinement_outputs():
    """Brief, draft and critics where the methodologist leaves a fixable MAJOR: JEV must choose."""
    outputs = happy_outputs()[:4]
    outputs[2]["findings"] = [MAJOR]
    return outputs


def test_illegal_judge_choice_cannot_bypass_authority(tmp_path):
    class IllegalJudge:
        def decide(self, state, questions):
            return {"next": Decision("APPROVE", {}, 1, tokens=1)}

    store = store_for(tmp_path)
    with pytest.raises(WorkflowError, match="illegal"):
        advance(store, Provider(refinement_outputs()), IllegalJudge(), RunConfig())
    assert store.state.status == "STOPPED"
    assert not store.state.review_complete


def test_final_checker_allowance_reserved_before_revision(tmp_path):
    store = store_for(tmp_path)
    store.update(llm_token_limit=100)
    outputs = happy_outputs()[:4]
    outputs[2]["findings"] = [MAJOR]
    state = advance(store, Provider(outputs), Judge(), RunConfig(task_max_tokens=60))
    assert state.status == "INCOMPLETE"
    assert not state.review_complete


def test_stale_document_cannot_be_approved(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    store.write(4, "revised_plan.md", "Tampered")
    with pytest.raises(WorkflowError):
        respond(store, action="APPROVE", text="yes", researcher="R", revision=store.state.attempt)


def test_unknown_provider_usage_remains_blocked(tmp_path):
    class FailedProvider:
        def complete(self, messages, tools):
            raise RuntimeError("connection failed")

    store = store_for(tmp_path)
    with pytest.raises(WorkflowError, match="unknown"):
        advance(store, FailedProvider(), Judge(), RunConfig())
    assert store.state.status == "USAGE_UNKNOWN"
    store.update(llm_token_limit=400000)
    provider = Provider(happy_outputs())
    advance(store, provider, Judge(), RunConfig())
    assert not provider.inputs


def test_request_changes_revises_and_rechecks(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    respond(
        store,
        action="REQUEST_CHANGES",
        text="Add another measurement",
        researcher="R",
        revision=store.state.attempt,
    )
    provider = Provider(
        [{"markdown": t} for t in ["Method objections", "Skeptic objections"]]
        + revised("Changed plan")
        + [{"markdown": "Fresh final"}]
    )
    state = advance(store, provider, Judge(), RunConfig())
    assert state.review_complete
    assert state.refinements == 1
    assert "Add another measurement" in provider.documents(2)["researcher_responses"]
    assert store.read(7, "exp_plan.md") == "Changed plan"


def test_approval_refreshes_publication_metadata(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    respond(
        store, action="APPROVE", text="Accept plan", researcher="R", revision=store.state.attempt
    )
    assert json.loads(store.read(7, "publication.json"))["approval"] == "approved"
    assert store.read(7, "exp_plan.md") == "Draft"


def test_reframe_allowance_preserves_history_and_bounds_loop(tmp_path):
    class ReframeJudge:
        def decide(self, state, questions):
            result = {}
            for key, q in questions.items():
                choice = "REFRAME" if "REFRAME" in q.options else next(iter(q.options))
                result[key] = Decision(choice, {}, 1, tokens=1)
            return result

    store = store_for(tmp_path)
    output = [
        {"markdown": t} | ({"findings": [MAJOR]} if t == "Method1" else {})  # a fix to reframe for
        for t in [
            "Brief1",
            "Draft1",
            "Method1",
            "Skeptic1",
            "Brief2",
            "Draft2",
            "Method2",
            "Skeptic2",
            "Final2",
        ]
    ]
    state = advance(store, Provider(output), ReframeJudge(), RunConfig(max_refinements=1))
    assert state.refinements == 1
    assert state.review_complete
    assert (store.path / "attempts" / "1" / "stage-02" / "research_brief.md").is_file()


def test_fabricated_evidence_ref_rejected_at_submit(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    outputs = [
        {
            "markdown": "Unsupported claim",
            "findings": [MAJOR | {"evidence_refs": ["calculation:invented"]}],
        },
        {"markdown": "Honest limitation"},
    ]
    provider = Provider(outputs)
    result = run_specialist("methodologist", store, provider, RunConfig(), {}, [])
    assert result.markdown == "Honest limitation"
    assert "not accepted" in provider.inputs[-1][-1]["content"]


def test_markdown_with_internal_evidence_ref_is_rejected(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    ref = "source:" + "7c2487ed" * 8
    provider = Provider([{"markdown": f"Margin is 3 points [{ref}]."}, {"markdown": "Clean"}])
    result = run_specialist("design", store, provider, RunConfig(), {}, [])
    assert result.markdown == "Clean"
    assert "internal evidence refs" in provider.inputs[-1][-1]["content"]


def test_task_budget_cumulative_after_interrupted_submission(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    bad = Provider([{"markdown": "Unsupported", "findings": [MAJOR | {"evidence_refs": ["fake"]}]}])
    with pytest.raises(WorkflowError):
        run_specialist("framing", store, bad, RunConfig(task_max_turns=1), {}, [])
    provider = Provider([{"markdown": "Retry"}])
    with pytest.raises(WorkflowError, match="allowance"):
        run_specialist("framing", store, provider, RunConfig(task_max_turns=1), {}, [])
    assert not provider.inputs


@pytest.mark.parametrize("stage", [2, 3, 4, 5, 6])
def test_manifest_survives_crash_before_checkpoint_without_provider_replay(
    tmp_path, monkeypatch, stage
):
    store = store_for(tmp_path)
    provider = Provider(happy_outputs())

    class CountingJudge(Judge):
        calls = 0

        def decide(self, state, questions):
            self.calls += 1
            return super().decide(state, questions)

    judge = CountingJudge()
    real_complete = store.complete

    def crash_after_complete(number, inputs, outputs):
        real_complete(number, inputs, outputs)
        if number == stage:
            raise SystemExit("simulated crash after durable manifest")

    monkeypatch.setattr(store, "complete", crash_after_complete)
    with pytest.raises(SystemExit):
        advance(store, provider, judge, RunConfig())
    monkeypatch.setattr(store, "complete", real_complete)
    state = advance(store, provider, judge, RunConfig())
    assert len(provider.starts) == 5
    assert judge.calls == 0  # every route here is decided by code rule
    assert state.review_complete
    assert state.plan_stage == 4
    assert store.read(7, "exp_plan.md") == "Draft"


def test_retry_unknown_usage_fails_closed_without_output_allowance(tmp_path):
    from experiment_design.agents.tools import run_specialist

    class RetriedProvider(Provider):
        last_attempts = []

        def complete(self, messages, tools):
            response = super().complete(messages, tools)
            self.last_attempts = [
                {"status": "error", "tokens_in": None, "tokens_out": None},
                {
                    "status": "success",
                    "tokens_in": 2,
                    "tokens_out": 3,
                    "tokens_cached": 1,
                    "content": None,
                    "tool_calls": [t.model_dump() for t in response.tool_calls],
                },
            ]
            return response

    store = store_for(tmp_path)
    provider = RetriedProvider([{"markdown": "Brief"}])
    with pytest.raises(WorkflowError, match="unknown"):
        run_specialist("framing", store, provider, RunConfig(), {}, [])
    assert store.state.usage_unknown
    assert store.state.llm_tokens == 5
    assert len([e for e in store.events() if e["kind"] == "provider.response"]) == 2


def test_retry_success_charges_failed_attempt_its_ceiling_and_continues(tmp_path):
    from types import SimpleNamespace

    from experiment_design.agents.tools import run_specialist

    class RetriedProvider(Provider):
        cfg = SimpleNamespace(max_tokens=100)
        last_attempts = []

        def complete(self, messages, tools):
            response = super().complete(messages, tools)
            self.last_attempts = [
                {"status": "error", "tokens_in": None, "tokens_out": None, "status_code": 524},
                {"status": "success", "tokens_in": 2, "tokens_out": 3, "tokens_cached": 0},
            ]
            return response

    store = store_for(tmp_path)
    provider = RetriedProvider([{"markdown": "Brief"}])
    output = run_specialist("framing", store, provider, RunConfig(), {}, [])
    assert output.markdown == "Brief"
    assert not store.state.usage_unknown
    responses = [e["data"] for e in store.events() if e["kind"] == "provider.response"]
    assert [(r["tokens"], r.get("usage_bound")) for r in responses] == [(102, "ceiling"), (5, None)]
    assert store.state.llm_tokens == 107


def test_calculation_cannot_claim_fabricated_source_value(tmp_path):
    from experiment_design.agents.tools import run_specialist

    class ToolProvider(Provider):
        def complete(self, messages, tools):
            self.inputs.append(messages.copy())
            name, args = next(self.outputs)
            return LLMResponse(
                content=None,
                tool_calls=[ToolCall(id="tool", name=name, arguments=json.dumps(args))],
                tokens_in=2,
                tokens_out=3,
            )

    store = store_for(tmp_path)
    provider = ToolProvider(
        [
            (
                "calculate",
                {
                    "calculator_id": "independent_pooled_t",
                    "effect": {
                        "value": 20,
                        "unit": "points",
                        "provenance": "source",
                        "source_ref": "stage-01/hypothesis.md",
                        "source_span": "effect 20 points",
                    },
                },
            ),
            ("submit", {"markdown": "Calculation unsupported"}),
        ]
    )
    output = run_specialist("design", store, provider, RunConfig(), {}, [])
    assert output.markdown == "Calculation unsupported"
    assert "lacks supplied source support" in provider.inputs[-1][-1]["content"]
    assert not any(e["kind"] == "calculation" for e in store.events())


def test_stale_revision_cannot_approve_current_complete_plan(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    with pytest.raises(WorkflowError, match="Stale response revision"):
        respond(
            store, action="APPROVE", text="yes", researcher="R", revision=store.state.attempt - 1
        )
    assert store.state.approval == "pending"


def test_start_pins_configured_run_token_ceiling(tmp_path):
    from experiment_design.config import ExdConfig, LLMConfig
    from experiment_design.workflow.engine import start_run

    source = tmp_path / "hypothesis.md"
    source.write_text("Hypothesis", encoding="utf-8")
    cfg = ExdConfig(
        llm=LLMConfig(base_url="https://example.invalid", model="fake"),
        run=RunConfig(llm_tokens=123),
        runs_root=tmp_path / "runs",
    )
    store = start_run([source], cfg)
    assert store.state.llm_token_limit == 123
    assert RunStore(store.path).state.llm_token_limit == 123


def test_final_check_revision_transmits_original_final_objections(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[-1] = {"markdown": "Final objection: missing blinding", "findings": [MAJOR]}
    outputs += revised("Blinded plan", recommended_action="Added blinding")
    outputs.append(
        {
            "markdown": "Fresh inspection",
            "resolutions": [{"finding_id": "P1", "status": "resolved", "reason": "fixed"}],
        }
    )
    provider = Provider(outputs)
    state = advance(store, provider, Judge(), RunConfig())
    assert state.review_complete
    assert "Final objection: missing blinding" in provider.documents(8)["final_objections"]
    assert "Final objection: missing blinding" in provider.documents(6)["final_objections"]


def test_requested_changes_critics_receive_previously_presented_revision(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Previously revised plan")
    advance(store, Provider(outputs), Judge(), RunConfig())
    respond(
        store,
        action="REQUEST_CHANGES",
        text="Change measurement",
        researcher="R",
        revision=store.state.attempt,
    )
    provider = Provider(
        [{"markdown": t} for t in ["Method", "Skeptic"]]
        + revised("Second revision")
        + [{"markdown": "Final"}]
    )
    advance(store, provider, Judge(), RunConfig())
    assert provider.documents(0)["plan"] == "Previously revised plan"
    assert provider.documents(1)["plan"] == "Previously revised plan"


@pytest.mark.parametrize("tokens", [-2, "bad"])
def test_judge_invalid_usage_never_decreases_run_usage(tmp_path, tokens):
    class InvalidUsageJudge:
        def decide(self, state, questions):
            return {"next": Decision("REVISE", {}, 1, tokens=tokens)}

    store = store_for(tmp_path)
    with pytest.raises(WorkflowError, match="invalid token usage"):
        advance(store, Provider(refinement_outputs()), InvalidUsageJudge(), RunConfig())
    assert store.state.llm_tokens == 30  # the specialists only; the invalid JEV usage adds none
    assert store.state.jev_calls == 1
    assert not store.state.review_complete


def test_unknown_judge_usage_is_disclosed_and_call_bounded(tmp_path):
    class UnknownJudge:
        def decide(self, state, questions):
            return {
                key: Decision(next(iter(q.options)), {}, 1, tokens=None)
                for key, q in questions.items()
            }

    store = store_for(tmp_path)
    outputs = refinement_outputs() + revised("Revised plan") + [{"markdown": "Final review"}]
    state = advance(store, Provider(outputs), UnknownJudge(), RunConfig())
    assert state.review_complete
    assert state.jev_calls == 1
    assert any(
        e["kind"] == "provider.usage"
        and e["data"].get("provider") == "jev"
        and e["data"].get("tokens_in") is None
        for e in store.events()
    )


class FailOnceJudge(Judge):
    """Returns an illegal choice on one call, so the run stops after the critics."""

    def __init__(self, fail_on=1):
        self.calls = 0
        self.fail_on = fail_on

    def decide(self, state, questions):
        self.calls += 1
        if self.calls == self.fail_on:
            return {"next": Decision("APPROVE", {}, 1, tokens=1)}
        return super().decide(state, questions)


def stop_after(monkeypatch, store, stage, on_call):
    """Make the Nth durable completion of a stage raise a workflow error before it is saved."""
    real_complete = store.complete
    calls = []

    def failing(number, inputs, outputs):
        if number == stage:
            calls.append(number)
            if len(calls) == on_call:
                raise WorkflowError("simulated stop")
        real_complete(number, inputs, outputs)

    monkeypatch.setattr(store, "complete", failing)
    return real_complete


def test_resume_after_failed_step_keeps_requested_change_plan(tmp_path, monkeypatch):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Previously revised plan")
    advance(store, Provider(outputs), Judge(), RunConfig())
    respond(
        store,
        action="REQUEST_CHANGES",
        text="Change measurement",
        researcher="R",
        revision=store.state.attempt,
    )
    # A change request routes to REVISE without the judge; the revised stage fails to save.
    real_complete = stop_after(monkeypatch, store, 4, on_call=1)
    first = Provider([{"markdown": t} for t in ["Method", "Skeptic"]] + revised("Second revision"))
    with pytest.raises(WorkflowError, match="simulated"):
        advance(store, first, Judge(), RunConfig())
    assert first.documents(0)["plan"] == "Previously revised plan"
    assert "Change measurement" in first.documents(0)["researcher_responses"]
    monkeypatch.setattr(store, "complete", real_complete)
    resumed = Provider([{"markdown": "Final"}])
    advance(store, resumed, Judge(), RunConfig())
    assert store.read(7, "exp_plan.md") == "Second revision"


def test_resume_after_failed_revision_keeps_plan_and_objections(tmp_path, monkeypatch):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan")
    outputs[-1] = {"markdown": "Final objection: no blinding", "findings": [MAJOR]}
    outputs += revised("Second revision")
    real_complete = stop_after(monkeypatch, store, 4, on_call=2)
    with pytest.raises(WorkflowError, match="simulated"):
        advance(store, Provider(outputs), Judge(), RunConfig())
    refinements = store.state.refinements
    monkeypatch.setattr(store, "complete", real_complete)
    resumed = Provider(
        [
            {
                "markdown": "Fresh final",
                "resolutions": [{"finding_id": "P1", "status": "resolved", "reason": "fixed"}],
            }
        ]
    )
    state = advance(store, resumed, Judge(), RunConfig())
    # The accepted revision and both rejoinders are not replayed; only the final check runs.
    assert len(resumed.starts) == 1
    final = resumed.documents(0)
    assert final["plan"] == "Second revision"
    assert "Final objection: no blinding" in final["final_objections"]
    assert refinements == 2 and state.refinements == 2
    assert state.review_complete
    assert store.read(7, "exp_plan.md") == "Second revision"


def test_failed_clarification_keeps_review_and_resumes_explanation(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    respond(store, action="CLARIFY", text="Why this?", researcher="R", revision=store.state.attempt)
    bad = Provider([{"markdown": "x", "findings": [MAJOR | {"evidence_refs": ["fake"]}]}])
    with pytest.raises(WorkflowError):
        advance(store, bad, Judge(), RunConfig(task_max_turns=1))
    assert store.state.status == "STOPPED"
    assert store.state.review_complete
    state = advance(store, Provider([{"markdown": "Because"}]), Judge(), RunConfig())
    assert state.review_complete and state.approval == "pending"
    answers = [e["data"] for e in store.events() if e["kind"] == "clarification.answer"]
    assert [(a["question"], a["markdown"]) for a in answers] == [("Why this?", "Because")]
    assert answers[0]["plan_hash"] == state.plan_hash
    respond(store, action="APPROVE", text="ok", researcher="R", revision=state.attempt)
    assert store.state.approval == "approved"


def test_each_clarification_has_own_allowance_and_record(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    cfg = RunConfig(task_max_turns=2)
    for question, answer in [("Q1?", "A1"), ("Q2?", "A2")]:
        respond(
            store, action="CLARIFY", text=question, researcher="R", revision=store.state.attempt
        )
        advance(store, Provider([{"markdown": answer}]), Judge(), cfg)
    answers = [e["data"] for e in store.events() if e["kind"] == "clarification.answer"]
    assert [(a["question"], a["markdown"]) for a in answers] == [("Q1?", "A1"), ("Q2?", "A2")]


CALC = {
    "calculator_id": "independent_pooled_t",
    "model": "Pooled two-sample t test",
    "contrast_type": "superiority",
    "direction": "increase",
    "outcome_kind": "continuous",
    "outcome_unit": "points",
    "alpha": 0.05,
    "sidedness": "two_sided",
    "effect": {"value": 5, "unit": "points", "provenance": "assumption"},
    "sd": {"value": 10, "unit": "points", "provenance": "assumption"},
    "n_per_arm": 64,
    "target_power": 0.8,
    "common_variance": True,
    "unit_of_randomization": "participant",
    "unit_of_analysis": "participant",
}


def test_critic_never_sees_peer_calculation_from_same_round(tmp_path):
    store = store_for(tmp_path)
    judge = FailOnceJudge(fail_on=1)
    calls = [
        ("submit", {"markdown": "Brief"}),
        ("submit", {"markdown": "Draft"}),
        ("calculate", CALC),
        ("submit", {"markdown": "Method", "findings": [MAJOR]}),
        ("submit", {"markdown": "Skeptic"}),
    ]
    with pytest.raises(WorkflowError, match="illegal"):
        advance(store, ToolProvider(calls), judge, RunConfig())
    calculations = [e for e in store.events() if e["kind"] == "calculation"]
    assert calculations[0]["data"]["result"]["status"] == "computed"
    resumed = ToolProvider(
        [
            ("submit", {"markdown": "Method", "findings": [MAJOR]}),
            ("submit", {"markdown": "Skeptic"}),
        ]
        + [
            ("submit", {"markdown": t})
            for t in ["Revised", "Method rejoinder", "Skeptic rejoinder"]
        ]
        + [("submit", {"markdown": "Final"})]
    )
    advance(store, resumed, judge, RunConfig())
    assert resumed.task(0)["documents"] == resumed.task(1)["documents"]
    calculations = next(
        d for d in resumed.task(1)["documents"] if d["name"] == "prior_accepted_calculations"
    )
    assert calculations["first_line"] == "[]" and calculations["lines"] == 1


class ReframeJudge:
    def decide(self, state, questions):
        return {
            key: Decision("REFRAME" if "REFRAME" in q.options else next(iter(q.options)), {}, 1, 1)
            for key, q in questions.items()
        }


def test_reframe_objections_reach_framing_and_survive_crash(tmp_path):
    store = store_for(tmp_path)
    first = [{"markdown": t} for t in ["Brief1", "Draft1", "Method1", "Skeptic1"]]
    first[2]["findings"] = [MAJOR]  # a fix to reframe for
    # Reframed framing keeps submitting an invalid output (a question flag with no question)
    # until its two turns run out; critics need both turns to read and submit.
    first += [{"markdown": "Brief2", "needs_input": True}] * 2
    with pytest.raises(WorkflowError, match="allowance"):
        advance(store, Provider(first), ReframeJudge(), RunConfig(task_max_turns=2))
    resumed = Provider(
        [{"markdown": t} for t in ["Brief2", "Draft2", "Method2", "Skeptic2", "Final2"]]
    )
    state = advance(store, resumed, ReframeJudge(), RunConfig(max_refinements=1))
    framing = resumed.documents(0)["reframe_objections"]
    assert "Method1" in framing and "Skeptic1" in framing
    assert state.review_complete


class FinalReframeJudge:
    def decide(self, state, questions):
        options = next(iter(questions.values())).options
        reframe = "REFRAME" in options and "CHECK" not in options and "PRESENT" not in options
        choice = "REFRAME" if reframe else next(iter(options))
        return {"next": Decision(choice, {}, 1, 1)}


def test_final_check_reframe_passes_all_objections_to_framing(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[-1] = {"markdown": "Final objection", "findings": [MAJOR]}
    outputs += [{"markdown": t} for t in ["Brief2", "Draft2", "M2", "S2", "Final2"]]
    provider = Provider(outputs)
    advance(store, provider, FinalReframeJudge(), RunConfig(max_refinements=1))
    framing = provider.documents(5)["reframe_objections"]
    assert (
        "Final objection" in framing and "Method review" in framing and "Skeptic review" in framing
    )


def test_run_limits_are_required_and_config_read_as_utf8(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    config = store.path / "config.json"
    config.write_text("{}", encoding="utf-8")
    with pytest.raises(WorkflowError, match="run limits"):
        respond(
            store, action="REQUEST_CHANGES", text="x", researcher="R", revision=store.state.attempt
        )
    body = json.dumps({"note": "ā", "run": RunConfig().model_dump()}, ensure_ascii=False)
    config.write_bytes(body.encode("utf-8"))
    respond(store, action="REQUEST_CHANGES", text="x", researcher="R", revision=store.state.attempt)
    assert store.state.status == "REQUESTED_CHANGES"


@pytest.mark.parametrize("later", ["APPROVE", "REQUEST_CHANGES", "CLARIFY"])
def test_reject_is_terminal(tmp_path, later):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    respond(store, action="REJECT", text="no", researcher="R", revision=store.state.attempt)
    with pytest.raises(WorkflowError, match="rejected"):
        respond(store, action=later, text="again", researcher="R", revision=store.state.attempt)
    assert store.state.approval == "rejected"
    assert store.state.status == "REJECTED"


def test_source_span_must_be_substantial_exact_text(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    spans = [" ", "a", "Exact hypo", "Exact hypothesis", "Exact  hypothesis: compare"]
    calls = [("source", {"name": "stage-01/hypothesis.md", "span": s}) for s in spans]
    provider = ToolProvider(calls + [("submit", {"markdown": "done"})])
    run_specialist("design", store, provider, RunConfig(task_max_turns=10), {}, [])
    results = [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"]
    assert ["error" in r for r in results[:5]] == [True, True, True, False, True]


def test_workspace_document_quote_is_a_citable_location_not_evidence(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    workspace = {"plan": "Randomize classes after the baseline test is scored.\n"}
    quote = {"name": "plan", "span": "Randomize classes after the baseline"}
    provider = ToolProvider([("source", quote), ("submit", {"markdown": "Review"})])
    run_specialist("skeptic", store, provider, RunConfig(task_max_turns=3), workspace, [])
    result = [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"][0]
    assert result["kind"] == "document"
    assert result["evidence_ref"].startswith("document:")

    finding = MAJOR | {"evidence_refs": [result["evidence_ref"]]}
    cited = ToolProvider(
        [("source", quote), ("submit", {"markdown": "Review", "findings": [finding]})]
    )
    output = run_specialist("skeptic", store, cited, RunConfig(task_max_turns=3), workspace, [])
    assert output.findings[0].evidence_refs == [result["evidence_ref"]]


def test_one_call_reads_several_documents_and_quotes_several_spans(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    workspace = {
        "plan": "Randomize classes after the baseline test is scored.\nSecond line.\n",
        "response": "The designer kept the baseline test as planned.\n",
    }
    quotes = [
        {"name": "plan", "span": "Randomize classes after the baseline"},
        {"name": "response", "span": "kept the baseline test"},
        {"name": "plan", "span": "Invented sentence absent"},
    ]
    turns = [
        [("read", {"names": ["plan", "response"]})],
        [("source", {"quotes": quotes})],
        [("submit", {"markdown": "Review"})],
    ]
    provider = Scripted(turns)
    run_specialist(
        "skeptic", store, provider, RunConfig(task_max_turns=3), workspace, ["plan", "response"]
    )
    read, quoted = [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"][:2]
    assert [d["name"] for d in read["documents"]] == ["plan", "response"]
    assert all("text" not in d and d["sha256"] for d in read["documents"])
    assert [q.get("kind") for q in quoted["quotes"]] == ["document", "document", None]
    assert "exact quote" in quoted["quotes"][2]["error"]


def test_calculation_value_cannot_come_from_a_workspace_document(tmp_path):
    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    workspace = {"plan": "Assume the effect was 20 points in the pilot.\n"}
    effect = {
        "value": 20,
        "unit": "points",
        "provenance": "source",
        "source_ref": "plan",
        "source_span": "Assume the effect was 20 points",
    }
    provider = ToolProvider(
        [
            ("calculate", {"calculator_id": "independent_pooled_t", "effect": effect}),
            ("submit", {"markdown": "done"}),
        ]
    )
    run_specialist("design", store, provider, RunConfig(), workspace, [])
    result = [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"][0]
    assert "lacks supplied source support" in result["error"]


def test_calculation_source_span_must_be_substantial(tmp_path):
    from experiment_design.agents.tools import run_specialist

    source = tmp_path / "hypothesis.md"
    source.write_text("The effect was 20 points here.\n", encoding="utf-8")
    store = RunStore.create(tmp_path / "runs", [source], {}, {})

    def request(span):
        effect = {
            "value": 20,
            "unit": "points",
            "provenance": "source",
            "source_ref": "stage-01/hypothesis.md",
            "source_span": span,
        }
        return {"calculator_id": "independent_pooled_t", "effect": effect}

    provider = ToolProvider(
        [
            ("calculate", request("20 points")),
            ("calculate", request("The effect was 20 points")),
            ("submit", {"markdown": "done"}),
        ]
    )
    run_specialist("design", store, provider, RunConfig(), {}, [])
    results = [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"]
    assert "lacks supplied source support" in results[0]["error"]
    assert "error" not in results[1]


def test_provider_attempt_event_stores_digest_not_messages(tmp_path):
    import hashlib

    from experiment_design.agents.tools import run_specialist

    store = store_for(tmp_path)
    provider = Provider([{"markdown": "Brief"}])
    run_specialist("framing", store, provider, RunConfig(), {}, [])
    data = [e["data"] for e in store.events() if e["kind"] == "provider.attempt"][0]
    assert "messages" not in data
    sent = provider.inputs[0]
    canonical = json.dumps(sent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert data["messages_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert data["message_count"] == len(sent) == 2


def test_a_change_request_is_revised_even_when_the_judge_would_stop(tmp_path):
    class StopJudge:
        def decide(self, state, questions):
            return {
                key: Decision(
                    "STOP" if "STOP" in q.options else next(iter(q.options)), {}, 1.0, tokens=1
                )
                for key, q in questions.items()
            }

    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Previously revised plan")
    advance(store, Provider(outputs), Judge(), RunConfig())
    respond(
        store,
        action="REQUEST_CHANGES",
        text="Change measurement",
        researcher="R",
        revision=store.state.attempt,
    )
    provider = Provider(
        [{"markdown": t} for t in ["Method", "Skeptic"]]
        + revised("Second revision")
        + [{"markdown": "Final"}]
    )
    advance(store, provider, StopJudge(), RunConfig())
    assert store.read(4, "revised_plan.md").startswith("Second revision")


PLAN_V1 = "# Plan\n## Design\nold design\n## Analysis\nsame\n"
PLAN_V2 = "# Plan\n## Design\nnew design\n## Analysis\nsame\n"


def test_final_check_after_a_change_request_is_convergent(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[1] = {"markdown": PLAN_V1}
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised(PLAN_V1)
    outputs[-1] = {"markdown": "Final", "findings": [MAJOR | {"id": "m", "severity": "MINOR"}]}
    advance(store, Provider(outputs), Judge(), RunConfig())
    respond(
        store, action="REQUEST_CHANGES", text="Change", researcher="R", revision=store.state.attempt
    )
    late = MAJOR | {"id": "late", "anchor": "Analysis", "issue": "Late find"}
    provider = Provider(
        [{"markdown": t} for t in ["Method", "Skeptic"]]
        + revised(PLAN_V2)
        + [{"markdown": "Final", "findings": [late], "resolutions": []}]
    )
    state = advance(store, provider, Judge(), RunConfig())
    final = json.loads(store.read(5, "final_review.json"))["findings"][0]
    assert (final["severity"], final["issue"]) == ("MINOR", "[unchanged] Late find")
    assert "late: MAJOR → MINOR [unchanged]" in store.read(5, "final_review.md")
    assert state.review_complete
    documents = provider.documents(6)
    assert json.loads(documents["previous_final_findings"]) == []
    assert "+new design" in documents["plan_diff_since_last_final"]


def test_findings_about_execution_time_evidence_never_block(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR | {"due": "later", "issue": "Dataset hash unknown"}]
    state = advance(store, Provider(outputs), Judge(), RunConfig())
    saved = json.loads(store.read(4, "methodologist_review.json"))["findings"][0]
    assert saved["severity"] == "MINOR"
    assert saved["issue"] == "[for execution] Dataset hash unknown"
    assert state.review_complete


def test_previous_final_is_the_latest_archived_final_check(tmp_path):
    from types import SimpleNamespace

    from experiment_design.workflow.engine import _previous_final

    store = SimpleNamespace(path=tmp_path, events=list)
    assert _previous_final(store) is None
    for number, plan in ((1, "old"), (2, "newer")):
        (tmp_path / "attempts" / str(number) / "stage-05").mkdir(parents=True)
        (tmp_path / "attempts" / str(number) / "stage-04").mkdir()
        (tmp_path / "attempts" / str(number) / "stage-05" / "final_review.json").write_text(
            json.dumps({"findings": []}), encoding="utf-8"
        )
        (tmp_path / "attempts" / str(number) / "stage-04" / "revised_plan.md").write_text(
            plan, encoding="utf-8"
        )
    (tmp_path / "attempts" / "3" / "stage-04").mkdir(parents=True)
    assert _previous_final(store) == ([], "newer")


def test_previous_final_does_not_carry_across_a_reframe(tmp_path):
    from types import SimpleNamespace

    from experiment_design.workflow.engine import _previous_final

    for number in (1, 2):
        archive = tmp_path / "attempts" / str(number)
        (archive / "stage-05").mkdir(parents=True)
        (archive / "stage-04").mkdir()
        (archive / "stage-05" / "final_review.json").write_text(
            json.dumps({"findings": [MAJOR]}), encoding="utf-8"
        )
        (archive / "stage-04" / "revised_plan.md").write_text(f"plan {number}", encoding="utf-8")
    reframe = {"kind": "reframe.objections", "data": {"attempt": 1, "objections": {}}}
    store = SimpleNamespace(path=tmp_path, events=lambda: [reframe])
    # Attempt 1 was reframed away; attempt 2 was checked after the new framing.
    assert _previous_final(store)[1] == "plan 2"
    reframe["data"]["attempt"] = 2
    assert _previous_final(store) is None


def test_previous_final_leaves_out_the_findings_code_checks_itself(tmp_path):
    from types import SimpleNamespace

    from experiment_design.workflow.engine import _previous_final

    archive = tmp_path / "attempts" / "1"
    (archive / "stage-05").mkdir(parents=True)
    (archive / "stage-04").mkdir()
    code = MAJOR | {"id": "seed-E1", "issue": "Fewer than three seeds"}
    out_of_budget = MAJOR | {"id": "review-incomplete", "issue": "Ran out"}
    findings = [MAJOR, code, out_of_budget]
    (archive / "stage-05" / "final_review.json").write_text(
        json.dumps({"findings": findings}), encoding="utf-8"
    )
    (archive / "stage-05" / "deterministic_findings.json").write_text(
        json.dumps([code]), encoding="utf-8"
    )
    (archive / "stage-04" / "revised_plan.md").write_text("plan", encoding="utf-8")
    kept, _ = _previous_final(SimpleNamespace(path=tmp_path, events=list))
    assert [f.id for f in kept] == [MAJOR["id"]]
