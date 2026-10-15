"""Refinement reserves, pinned limits, re-decidable stops and the revision/agent/view contracts."""

import json

import pytest
from pydantic import ValidationError

import experiment_design.workflow.engine as engine
from experiment_design.agents.prompts import prompt
from experiment_design.agents.tools import run_specialist
from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput
from experiment_design.persistence.store import RunStore
from experiment_design.publication import render_review
from experiment_design.runtime.jev import Decision
from experiment_design.runtime.llm import LLMResponse, ToolCall
from experiment_design.workflow.engine import advance, respond
from tests.integration.review.test_markdown_workflow import CALC
from tests.integration.review.test_workspace_agent import (
    DOC,
    MAJOR,
    happy_outputs,
    revised,
    store_for,
    tool_results,
)
from tests.support.providers import Judge, Provider, Scripted, ToolProvider

HYPOTHESES_INPUT = "stage-01/hypothesis.md"


def events(store, kind):
    return [e["data"] for e in store.events() if e["kind"] == kind]


def prior_calculation_lines(provider, invocation):
    document = next(
        d
        for d in provider.task(invocation)["documents"]
        if d["name"] == "prior_accepted_calculations"
    )
    return document["lines"]


# Pinned run limits


def test_changed_run_limits_are_journalled_and_persisted_once(tmp_path):
    store = store_for(tmp_path)
    engine.pin_limits(store, RunConfig())
    assert not events(store, "limits.changed")
    changed = RunConfig(task_max_tokens=500, max_refinements=0)
    engine.pin_limits(store, changed)
    engine.pin_limits(store, changed)
    (event,) = events(store, "limits.changed")
    assert (event["old"]["task_max_tokens"], event["new"]["task_max_tokens"]) == (120_000, 500)
    assert (event["old"]["max_refinements"], event["new"]["max_refinements"]) == (2, 0)
    assert RunStore(store.path).config()["run"]["task_max_tokens"] == 500


def test_request_changes_reads_the_persisted_limits(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    engine.pin_limits(store, RunConfig(task_max_tokens=500))
    store.update(llm_token_limit=store.state.llm_tokens + 3000)  # 6 x 500
    respond(store, action="REQUEST_CHANGES", text="x", researcher="R", revision=1)
    assert store.state.status == "REQUESTED_CHANGES"


# Stops that limits forced stay re-decidable


class StopJudge(Judge):
    """Chooses STOP whenever a refinement is also legal."""

    def decide(self, state, questions):
        return {
            key: Decision("STOP" if "REVISE" in q.options else next(iter(q.options)), {}, 1.0, 1)
            for key, q in questions.items()
        }


@pytest.mark.parametrize("stage", [4, 5])
def test_stop_forced_by_budget_is_decided_again_after_a_ceiling_raise(tmp_path, stage):
    store = store_for(tmp_path)
    store.update(llm_token_limit=100)
    cfg = RunConfig(task_max_tokens=60)
    outputs = happy_outputs()
    outputs[2 if stage == 4 else 4]["findings"] = [MAJOR]
    state = advance(store, Provider(outputs[: 4 if stage == 4 else 5]), Judge(), cfg)
    assert state.status == "INCOMPLETE" and state.refinements == 0
    assert json.loads(store.read(stage, "route.json")) == {"choice": "STOP", "forced": True}
    store.update(llm_token_limit=10_000)
    resumed = Provider(
        revised("Revised plan")
        + [
            {
                "markdown": "Fresh final",
                "resolutions": [{"finding_id": "P1", "status": "resolved", "reason": "fixed"}],
            }
        ]
    )
    state = advance(store, resumed, Judge(), cfg)
    assert state.review_complete and state.refinements == 1
    assert len(resumed.starts) == 4  # revision, two rejoinders, fresh final check
    assert store.read(7, "exp_plan.md") == "Revised plan"


def test_stop_chosen_by_jev_among_legal_options_stays_final(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()[:4]
    outputs[2]["findings"] = [MAJOR]
    # After a first refinement STOP is the judge's call again (the first round must revise).
    store.update(refinements=1)
    state = advance(store, Provider(outputs), StopJudge(), RunConfig())
    assert state.status == "INCOMPLETE"
    assert json.loads(store.read(4, "route.json")) == {"choice": "STOP", "forced": False}
    store.update(llm_token_limit=10_000_000)
    resumed = Provider([])
    state = advance(store, resumed, StopJudge(), RunConfig())
    assert state.status == "INCOMPLETE" and state.refinements == 1
    assert not resumed.inputs


# ASK_RESEARCHER needs a real question


@pytest.mark.parametrize("question", [" ", "\n\t"])
def test_whitespace_question_is_rejected(question):
    with pytest.raises(ValidationError):
        AgentOutput(markdown="m", question=question)
    assert AgentOutput(markdown="m", question="").question == ""


def test_no_route_offers_ask_for_a_whitespace_question(tmp_path, monkeypatch):
    class Recording(Judge):
        def __init__(self):
            self.offered = []

        def decide(self, state, questions):
            self.offered += [list(q.options) for q in questions.values()]
            return super().decide(state, questions)

    real = engine._agent

    def blank_question(*args, **options):
        return real(*args, **options).model_copy(update={"question": "  "})

    monkeypatch.setattr(engine, "_agent", blank_question)
    judge = Recording()
    state = advance(store_for(tmp_path), Provider(happy_outputs()), judge, RunConfig())
    assert state.review_complete
    assert judge.offered and all("ASK_RESEARCHER" not in options for options in judge.offered)


# Revision and final-check inputs and outputs


def test_revision_and_final_check_require_hypothesis_and_brief(tmp_path):
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan")
    provider = Provider(outputs)
    advance(store_for(tmp_path), provider, Judge(), RunConfig())
    for invocation in (4, 7):
        assert {HYPOTHESES_INPUT, "research_brief"} <= set(provider.task(invocation)["required"])
        assert provider.documents(invocation)["research_brief"] == "Brief"


def test_revision_findings_are_rendered_into_the_response(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    finding = MAJOR | {"id": "obj-1", "suggestion": "Added a sham control"}
    outputs[4:4] = revised(
        "Revised plan", recommended_action="Summary of changes", findings=[finding]
    )
    provider = Provider(outputs)
    advance(store, provider, Judge(), RunConfig())
    response = store.read(4, "response.md")
    for text in ("Summary of changes", "obj-1", "MAJOR", "No control", "Added a sham control"):
        assert text in response
    assert provider.documents(5)["response"] == response


def test_revision_question_goes_to_the_researcher_before_any_rejoinder(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()[:4]
    outputs[2]["findings"] = [MAJOR]
    ask = {
        "markdown": "Revised plan",
        "needs_input": True,
        "question": "Which control?",
        "options": ["Sham", "Usual care"],
    }
    provider = Provider([*outputs, ask])
    state = advance(store, provider, Judge(), RunConfig())
    assert state.status == "AWAITING_RESEARCHER"
    assert state.question == "Which control?"
    assert len(provider.starts) == 5  # no rejoinders, no final check
    assert advance(store, Provider([]), Judge(), RunConfig()).status == "AWAITING_RESEARCHER"
    respond(store, action="ANSWER", text="Sham", researcher="R", revision=state.attempt)
    assert store.state.status == "RUNNING"
    provider = Provider([*revised("Revised plan"), {"markdown": "Final review"}])
    assert advance(store, provider, Judge(), RunConfig()).status == "PUBLISHED"
    assert len(provider.starts) == 4  # the revision resumes with the answer
    assert provider.documents(0)["plan"] == "Draft"
    assert "Sham" in provider.documents(0)["researcher_responses"]


class AskJudge:
    """Asks the researcher whenever that route is legal, otherwise takes the first option."""

    def decide(self, state, questions):
        return {
            key: Decision(
                "ASK_RESEARCHER" if "ASK_RESEARCHER" in q.options else next(iter(q.options)),
                {},
                1.0,
                tokens=1,
            )
            for key, q in questions.items()
        }


def test_answer_to_a_critic_question_resumes_at_revision(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()[:4]
    outputs[2] |= {
        "findings": [MAJOR],
        "question": "Which control?",
        "options": ["Sham", "Usual care"],
    }
    state = advance(store, Provider(outputs), AskJudge(), RunConfig())
    assert (state.status, state.stage) == ("AWAITING_RESEARCHER", 4)
    respond(store, action="ANSWER", text="Sham", researcher="R", revision=state.attempt)
    provider = Provider([*revised("Revised plan"), {"markdown": "Final review"}])
    state = advance(store, provider, Judge(), RunConfig())
    assert len(provider.starts) == 4  # revision, two rejoinders, final check
    revision = provider.documents(0)
    assert revision["plan"] == "Draft"
    assert "Method review" in revision["methodologist_review"]
    assert "Sham" in revision["researcher_responses"]
    assert state.status == "PUBLISHED"
    assert store.read(7, "exp_plan.md") == "Revised plan"


def test_answer_to_a_final_check_question_revises_with_its_objections(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[4] |= {"question": "Which margin?", "options": ["Three points", "Five points"]}
    state = advance(store, Provider(outputs), AskJudge(), RunConfig())
    assert (state.status, state.stage) == ("AWAITING_RESEARCHER", 5)
    respond(store, action="ANSWER", text="Three points", researcher="R", revision=state.attempt)
    provider = Provider([*revised("Revised plan"), {"markdown": "Final review"}])
    state = advance(store, provider, Judge(), RunConfig())
    assert len(provider.starts) == 4
    revision = provider.documents(0)
    assert revision["plan"] == "Draft"
    assert "Final review" in revision["final_objections"]
    assert "Three points" in revision["researcher_responses"]
    assert state.status == "PUBLISHED"


# Agent tool loop


class Chatty:
    """Answers once without any tool call, then submits."""

    def __init__(self):
        self.inputs = []

    def complete(self, messages, tools):
        self.inputs.append(messages.copy())
        calls = []
        if len(self.inputs) > 1:
            args = json.dumps({"markdown": "done"})
            calls = [ToolCall(id="s", name="submit", arguments=args)]
        return LLMResponse(content="I will think", tool_calls=calls, tokens_in=2, tokens_out=3)


def test_reply_without_tool_calls_gets_a_plain_message_and_a_nudge(tmp_path):
    provider = Chatty()
    output = run_specialist("design", store_for(tmp_path), provider, RunConfig(), {}, [])
    assert output.markdown == "done"
    assistant, nudge = provider.inputs[1][-2:]
    assert assistant == {"role": "assistant", "content": "I will think"}
    assert nudge["role"] == "user"
    assert all(word in nudge["content"] for word in ("read", "search", "submit"))


def test_non_object_tool_arguments_are_an_error_result(tmp_path):
    store = store_for(tmp_path)
    turns = [[("read", ["doc"]), ("search", "alpha"), ("submit", {"markdown": "done"})]]
    run_specialist("design", store, Scripted(turns), RunConfig(), {"doc": DOC}, [])
    read, search, _ = tool_results(store)
    assert "JSON object" in read["error"] and "JSON object" in search["error"]


def test_source_tool_asks_for_a_verbatim_quote_not_line_numbers(tmp_path):
    class Capturing(Chatty):
        def complete(self, messages, tools):
            self.tools = tools
            return super().complete(messages, tools)

    provider = Capturing()
    run_specialist("design", store_for(tmp_path), provider, RunConfig(), {}, [])
    source = next(t["function"] for t in provider.tools if t["function"]["name"] == "source")
    span = source["parameters"]["properties"]["span"]["description"]
    assert "verbatim" in span and "12" in span and "line numbers" in span
    assert "verbatim" in source["description"] and "line numbers" in source["description"]
    assert "line numbers" in " ".join(prompt("design").split())


def test_read_event_keeps_a_digest_while_the_model_gets_the_text(tmp_path):
    store = store_for(tmp_path)
    provider = Scripted([[("read", {"name": "doc"})], [("submit", {"markdown": "done"})]])
    run_specialist("design", store, provider, RunConfig(), {"doc": DOC}, [])
    (logged,) = [r for r in tool_results(store) if "total_lines" in r]
    assert set(logged) == {"name", "start_line", "end_line", "total_lines", "sha256"}
    sent = json.loads(provider.inputs[1][-1]["content"])
    assert "2: Second line with Alpha beta" in sent["text"]


# Option display


@pytest.mark.parametrize(
    "given",
    [
        ["1. Direct AI", "2. Indirect"],
        ["1) Direct AI", "2) Indirect"],
        ["(1) Direct AI", "(2) Indirect"],
        ["- Direct AI", "- Indirect"],
    ],
)
def test_option_enumerators_are_stripped(given):
    assert AgentOutput(markdown="m", question="Which?", options=given).options == [
        "Direct AI",
        "Indirect",
    ]


def test_option_text_that_only_looks_numeric_is_kept():
    options = ["1.5 mg dose", "-2 degrees"]
    assert AgentOutput(markdown="m", question="Which?", options=options).options == options


def test_numbered_options_are_stored_and_shown_unnumbered(tmp_path):
    store = store_for(tmp_path)
    ask = {
        "markdown": "Need population",
        "needs_input": True,
        "question": "Which population?",
        "options": ["1. Adults", "2. Children"],
    }
    advance(store, Provider([ask]), Judge(), RunConfig())
    assert store.state.question_options == ["Adults", "Children"]
    assert "without numbering" in prompt("framing")


# Researcher view and authority


def test_clarify_keeps_a_recorded_approval(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    respond(store, action="APPROVE", text="ok", researcher="R", revision=store.state.attempt)
    respond(store, action="CLARIFY", text="Why?", researcher="R", revision=store.state.attempt)
    assert store.state.approval == "approved"
    state = advance(store, Provider([{"markdown": "Because"}]), Judge(), RunConfig())
    assert state.approval == "approved"
    assert json.loads(store.read(7, "publication.json"))["approval"] == "approved"


def test_review_header_discloses_gaps_and_notes_of_an_incomplete_draft(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[-1] = {"markdown": "Final review: no blinding", "findings": [MAJOR]}
    state = advance(store, Provider(outputs), Judge(), RunConfig(max_refinements=0))
    assert state.status == "INCOMPLETE"
    text = render_review(store)
    assert text.endswith("\n\nDraft")
    header = text[: -len("Draft")]
    for expected in (
        "incomplete draft",
        "acceptance: pending",
        "Known gaps",
        "Fresh complete review is absent",
        "Review notes",
        "Final review: no blinding",
    ):
        assert expected in header


def test_review_header_of_a_complete_plan_lists_no_gaps(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    assert "Known gaps" not in render_review(store)


# Calculation isolation between rejoinders (the critic round is covered in the workflow tests)


def test_rejoinder_never_sees_the_peers_accepted_calculation(tmp_path):
    store = store_for(tmp_path)
    submit = lambda **fields: ("submit", fields)  # noqa: E731
    provider = ToolProvider(
        [
            submit(markdown="Brief"),
            submit(markdown="Draft"),
            ("calculate", CALC),
            submit(markdown="Method", findings=[MAJOR]),
            submit(markdown="Skeptic"),
            submit(markdown="Revised plan"),
            submit(markdown="Method rejoinder"),
            submit(markdown="Skeptic rejoinder"),
            submit(markdown="Final"),
        ]
    )
    state = advance(store, provider, Judge(), RunConfig())
    assert state.review_complete
    assert len(events(store, "calculation")) == 1
    assert provider.task(5)["task"].startswith("Role: methodologist_rejoinder")
    assert provider.task(6)["task"].startswith("Role: skeptic_rejoinder")
    assert prior_calculation_lines(provider, 6) == 1  # "[]": neither methodologist calculation
    assert prior_calculation_lines(provider, 5) > 1  # its own earlier calculation stays visible
    assert prior_calculation_lines(provider, 7) > 1  # the fresh final check sees it


def test_reviser_sees_only_persistent_finding_ids(tmp_path):
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR | {"id": "F1"}]
    outputs[4:4] = revised("Revised plan")
    provider = Provider(outputs)
    advance(store_for(tmp_path), provider, Judge(), RunConfig())
    records = json.loads(provider.documents(4)["revision_findings"])
    assert [r["finding_id"] for r in records] == ["F1-1"]
    assert all("id" not in r for r in records)


def test_final_merge_drops_model_findings_that_repeat_a_deterministic_one():
    from experiment_design.contracts import Finding

    def make(fid, issue):
        return Finding(id=fid, severity="MAJOR", issue=issue, consequence="c", suggestion="s")

    det = [make("F2-5", "No  code check"), make("F2-6", "Other gap")]
    model = [make("F2-3", "no code CHECK"), make("F2-6", "different text"), make("F2-9", "Own")]
    merged = engine._merge_deterministic(model, det)
    assert [f.id for f in merged] == ["F2-9", "F2-5", "F2-6"]
    assert merged[2].issue == "Other gap"
