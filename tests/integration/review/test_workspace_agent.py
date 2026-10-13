"""Workspace agents, rejoinders, structured question options and the refinement reserve."""

import hashlib
import json

import pytest
from pydantic import ValidationError

import experiment_design.workflow.engine as engine
from experiment_design.agents.tools import run_specialist
from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput, WorkflowError
from experiment_design.persistence.store import RunStore
from experiment_design.workflow.engine import advance, respond
from tests.support.providers import Judge, Provider, Scripted

DOC = "Title line\nSecond line with Alpha beta\nthird\nfourth alpha\nfifth\n"
OTHER = "Alpha only here\nbeta elsewhere\n"
MAJOR = {
    "id": "gap",
    "severity": "MAJOR",
    "issue": "No control",
    "consequence": "Confounding",
    "suggestion": "Add control",
}


def store_for(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_text("Exact hypothesis: compare two measurements.\n", encoding="utf-8")
    return RunStore.create(tmp_path / "runs", [source], {"run": RunConfig().model_dump()}, {})


def happy_outputs():
    texts = ["Brief", "Draft", "Method review", "Skeptic review", "Final review"]
    return [{"markdown": text} for text in texts]


def revised(text, **fields):
    return [
        {"markdown": text} | fields,
        {"markdown": "Method rejoinder"},
        {"markdown": "Skeptic rejoinder"},
    ]


def tool_results(store):
    return [e["data"]["result"] for e in store.events() if e["kind"] == "tool.result"]


def specialist(store, turns, required=()):
    provider = Scripted(turns)
    workspace = {"doc": DOC, "other": OTHER}
    output = run_specialist(
        "design", store, provider, RunConfig(task_max_turns=10), workspace, list(required)
    )
    return output, provider


def test_first_message_is_an_index_without_document_content(tmp_path):
    store = store_for(tmp_path)
    _, provider = specialist(store, [[("submit", {"markdown": "ok"})]])
    first = provider.inputs[0][1]["content"]
    task = json.loads(first)
    assert task["required"] == []
    assert task["documents"] == [
        {"name": "doc", "lines": 5, "first_line": "Title line"},
        {"name": "other", "lines": 2, "first_line": "Alpha only here"},
    ]
    assert "Second line" not in first and "fourth" not in first and "elsewhere" not in first


def test_unknown_required_document_is_a_workflow_error(tmp_path):
    with pytest.raises(WorkflowError, match="missing"):
        specialist(store_for(tmp_path), [], required=["absent"])


def test_submit_is_rejected_until_required_documents_are_fully_read(tmp_path):
    store = store_for(tmp_path)
    turns = [
        [("submit", {"markdown": "x"})],
        [("read", {"name": "doc", "max_lines": 2})],
        [("submit", {"markdown": "x"})],
        [("read", {"name": "doc", "start_line": 3}), ("submit", {"markdown": "done"})],
    ]
    output, _ = specialist(store, turns, required=["doc"])
    results = tool_results(store)
    assert results[0]["accepted"] is False
    assert "doc (lines 1-5)" in results[0]["error"]
    assert "doc (lines 3-5)" in results[2]["error"] and "other" not in results[2]["error"]
    assert results[3]["end_line"] == 5
    assert results[4] == {"accepted": True}
    assert output.markdown == "done"


def test_read_and_search_results(tmp_path):
    store = store_for(tmp_path)
    turns = [
        [
            ("read", {"name": "doc", "start_line": 2, "max_lines": 2}),
            ("read", {"name": "missing"}),
            ("read", {"name": "doc", "start_line": 9}),
            ("search", {"query": "ALPHA beta"}),
            ("search", {"query": "alpha", "max_hits": 1}),
            ("search", {"query": "  "}),
            ("submit", {"markdown": "done"}),
        ]
    ]
    specialist(store, turns)
    read, unknown, out_of_range, both, limited, blank, _ = tool_results(store)
    text = "2: Second line with Alpha beta\n3: third"
    assert read == {
        "name": "doc",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 5,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    assert "Unknown document" in unknown["error"]
    assert "out of range" in out_of_range["error"]
    assert both == {
        "hits": [{"name": "doc", "line": 2, "text": "Second line with Alpha beta"}],
        "truncated": False,
    }
    assert limited["hits"] == [{"name": "doc", "line": 2, "text": "Second line with Alpha beta"}]
    assert limited["truncated"] is True
    assert "query" in blank["error"]


def test_check_reports_the_problems_a_submit_would_reject(tmp_path):
    store = store_for(tmp_path)
    bad = {
        "markdown": "x",
        "needs_input": True,
        "findings": [MAJOR | {"evidence_refs": ["calculation:invented"]}],
    }
    turns = [
        [("check", bad), ("submit", bad)],
        [("read", {"name": "doc"}), ("check", {"markdown": "fine"})],
        [("submit", {"markdown": "fine"})],
    ]
    output, _ = specialist(store, turns, required=["doc"])
    checked, rejected, _, valid, accepted = tool_results(store)
    assert checked["ok"] is False
    assert checked["problems"] == rejected["problems"]
    assert len(checked["problems"]) == 3
    assert valid == {"ok": True, "problems": []}
    assert accepted == {"accepted": True}
    assert len([e for e in store.events() if e["kind"] == "agent.accepted"]) == 1
    assert output.markdown == "fine"


def test_rejoinders_see_own_review_and_designer_response_never_the_peer(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan", recommended_action="Added control")
    provider = Provider(outputs)
    advance(store, provider, Judge(), RunConfig())
    for invocation, own, peer in [(5, "Method review", "Skeptic"), (6, "Skeptic review", "Method")]:
        read = provider.documents(invocation)
        task = provider.task(invocation)
        role = "methodologist" if invocation == 5 else "skeptic"
        assert f"Role: {role}_rejoinder" in task["task"]
        assert set(task["required"]) == {
            "stage-01/hypothesis.md",
            "plan",
            "plan_diff",
            "response",
            f"{role}_review",
        }
        assert read["plan"] == "Revised plan"
        assert read["response"] == "Added control"
        assert read[f"{role}_review"] == own
        names = {d["name"] for d in task["documents"]}
        assert not any(peer.lower() in name for name in names)
        assert not any(peer in text for text in read.values())


def test_maintained_major_from_a_rejoinder_blocks_presentation(tmp_path):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan")
    outputs[5]["findings"] = [MAJOR]
    state = advance(store, Provider(outputs), Judge(), RunConfig(max_refinements=1))
    assert state.status == "INCOMPLETE"
    assert not state.review_complete
    assert not store.valid(7)
    routes = [e["data"] for e in store.events() if e["kind"] == "route"]
    assert routes[-1]["choice"] == "STOP"


def test_resume_does_not_rerun_an_accepted_rejoinder(tmp_path, monkeypatch):
    store = store_for(tmp_path)
    outputs = happy_outputs()
    outputs[2]["findings"] = [MAJOR]
    outputs[4:4] = revised("Revised plan")
    real = engine._agent

    def crashing(store_, llm, cfg, role, *rest, **options):
        if role == "skeptic_rejoinder":
            raise SystemExit("simulated crash before the second rejoinder")
        return real(store_, llm, cfg, role, *rest, **options)

    monkeypatch.setattr(engine, "_agent", crashing)
    with pytest.raises(SystemExit):
        advance(store, Provider(outputs[:6]), Judge(), RunConfig())
    monkeypatch.setattr(engine, "_agent", real)
    resumed = Provider([{"markdown": "Skeptic rejoinder"}, {"markdown": "Final review"}])
    state = advance(store, resumed, Judge(), RunConfig())
    assert len(resumed.starts) == 2
    assert "skeptic_rejoinder" in resumed.task(0)["task"]
    assert store.read(4, "methodologist_rejoinder.md") == "Method rejoinder"
    assert state.review_complete


def test_refinement_reserve_covers_revision_two_rejoinders_and_final_check(tmp_path):
    store = store_for(tmp_path)
    cfg = RunConfig(task_max_tokens=100)
    store.update(llm_token_limit=399)
    assert not engine._can_refine(store, cfg, "REVISE")
    store.update(llm_token_limit=400)
    assert engine._can_refine(store, cfg, "REVISE")
    assert not engine._can_refine(store, cfg, "REFRAME")  # framing, design, two critics, check
    store.update(llm_token_limit=500)
    assert engine._can_refine(store, cfg, "REFRAME")
    assert not engine._can_refine(store, cfg, "REQUEST_CHANGES")
    store.update(llm_token_limit=600)
    assert engine._can_refine(store, cfg, "REQUEST_CHANGES")
    outputs = happy_outputs()[:4]
    outputs[2]["findings"] = [MAJOR]
    (tmp_path / "second").mkdir()
    fresh = store_for(tmp_path / "second")
    fresh.update(llm_token_limit=350)
    state = advance(fresh, Provider(outputs), Judge(), cfg)
    assert state.status == "INCOMPLETE"
    assert state.refinements == 0


def test_request_changes_checks_the_same_reserve(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    # Two critics, revision, two rejoinders and the final check: 6x the pinned task allowance.
    used = store.state.llm_tokens
    store.update(llm_token_limit=used + 719_999)
    with pytest.raises(WorkflowError, match="final-check allowance"):
        respond(store, action="REQUEST_CHANGES", text="x", researcher="R", revision=1)
    store.update(llm_token_limit=used + 720_000)
    respond(store, action="REQUEST_CHANGES", text="x", researcher="R", revision=1)
    assert store.state.status == "REQUESTED_CHANGES"


@pytest.mark.parametrize(
    "fields",
    [
        {"question": "Which?", "options": ["a", "b"]},
        {"question": "Which?", "options": ["a", "b", "c", "d"]},
        {},
    ],
)
def test_valid_options(fields):
    assert AgentOutput(markdown="m", **fields)


@pytest.mark.parametrize(
    "fields",
    [
        {"options": ["a", "b"]},
        {"question": " ", "options": ["a", "b"]},
        {"question": "Which?", "options": ["only"]},
        {"question": "Which?", "options": ["a", "b", "c", "d", "e"]},
        {"question": "Which?", "options": ["a", " "]},
        {"question": "Which?", "options": ["a", "A "]},
    ],
)
def test_invalid_options(fields):
    with pytest.raises(ValidationError):
        AgentOutput(markdown="m", **fields)


def asked(tmp_path, options=("Adults", "Children")):
    store = store_for(tmp_path)
    ask = {
        "markdown": "Need population",
        "needs_input": True,
        "question": "Which population?",
        "options": list(options),
    }
    state = advance(store, Provider([ask]), Judge(), RunConfig())
    assert state.status == "AWAITING_RESEARCHER"
    return store


def test_option_answer_records_exact_option_text(tmp_path):
    store = asked(tmp_path)
    assert store.state.question_options == ["Adults", "Children"]
    respond(store, action="ANSWER", text="", researcher="R", revision=1, option=2)
    record = [e["data"] for e in store.events() if e["kind"] == "researcher.response"][-1]
    assert (record["option"], record["option_text"], record["text"]) == (2, "Children", "")
    assert store.state.question_options == []
    provider = Provider(happy_outputs())
    advance(store, provider, Judge(), RunConfig())
    assert '"option_text": "Children"' in provider.documents(0)["researcher_responses"]


def test_option_with_free_text_keeps_both(tmp_path):
    store = asked(tmp_path)
    respond(store, action="ANSWER", text="Adults over 60", researcher="R", revision=1, option=1)
    record = [e["data"] for e in store.events() if e["kind"] == "researcher.response"][-1]
    assert (record["option_text"], record["text"]) == ("Adults", "Adults over 60")


@pytest.mark.parametrize("option", [0, 3, -1, True])
def test_option_out_of_range_is_rejected(tmp_path, option):
    store = asked(tmp_path)
    with pytest.raises(WorkflowError, match="Option"):
        respond(store, action="ANSWER", text="", researcher="R", revision=1, option=option)
    assert store.state.status == "AWAITING_RESEARCHER"
    assert not [e for e in store.events() if e["kind"] == "researcher.response"]


def test_option_rejected_when_no_options_are_stored(tmp_path):
    store = asked(tmp_path, options=())
    assert store.state.question_options == []
    with pytest.raises(WorkflowError, match="Option"):
        respond(store, action="ANSWER", text="", researcher="R", revision=1, option=1)
    with pytest.raises(WorkflowError, match="nonblank"):
        respond(store, action="ANSWER", text=" ", researcher="R", revision=1)


def test_option_is_only_for_answers(tmp_path):
    store = store_for(tmp_path)
    advance(store, Provider(happy_outputs()), Judge(), RunConfig())
    with pytest.raises(WorkflowError, match="Option"):
        respond(store, action="CLARIFY", text="Why?", researcher="R", revision=1, option=1)


def test_several_questions_drop_options(tmp_path):
    store = store_for(tmp_path)
    one = AgentOutput(markdown="a", question="Q1?", options=["x", "y"])
    two = AgentOutput(markdown="b", question="Q2?", options=["u", "v"])
    quiet = AgentOutput(markdown="c")
    engine._ask(store, [one, quiet])
    assert store.state.question_options == ["x", "y"]
    engine._ask(store, [one, two])
    assert store.state.question == "Q1?\nQ2?"
    assert store.state.question_options == []
