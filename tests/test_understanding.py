import hashlib

import pytest
from fakes import ScriptedLLM, submit, tool_call

from experiment_design.agents import understanding
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.contracts import AgentTask, TaskBudget
from experiment_design.package import PackageFormatError, snapshot_sources
from experiment_design.runtime.loop import SubmitRejected, run_agent
from experiment_design.runtime.store import Store


def test_snapshot_preserves_freeform_bom_duplicates_and_missing_attachment(tmp_path):
    text = "\ufeffWhy feedback?\r\n### Any heading\r\nMargin 3 points. [Data](missing.csv)\r\n"
    main = tmp_path / "study.md"
    other = tmp_path / "second.md"
    main.write_bytes(text.encode("utf-8"))
    other.write_bytes(text.encode("utf-8"))
    snapshot = snapshot_sources([main, other])
    assert [a.text for a in snapshot.artifacts] == [text, text]
    assert snapshot.artifacts[0].sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert snapshot.artifacts[0].ref != snapshot.artifacts[1].ref
    assert "missing.csv" in " ".join(snapshot.intake_issues)
    with pytest.raises(PackageFormatError, match="read"):
        snapshot_sources([tmp_path / "absent.md"])


def payload(snapshot):
    return {
        "context": {
            "id": "context",
            "revision": 1,
            "source_snapshot_id": snapshot.id,
            "package": {
                "id": "feedback",
                "research_question": "AI feedback?",
                "hypothesis": {"statement": "AI feedback is non-inferior"},
                "inference": {
                    "contrast_type": "non_inferiority",
                    "margin": 3,
                    "estimand": "ITT class mean difference",
                },
            },
            "field_annotations": {
                "margin": {
                    "status": "source-stated",
                    "source_ref": snapshot.artifacts[0].ref,
                    "source_span": "Margin 3 points",
                },
                "estimand": {"status": "agent-proposed", "note": "Must clarify estimand"},
            },
            "unknowns": ["Contradictory margins; confirm 3 or 2", "Estimand unspecified"],
        },
        "decisions": [
            {
                "id": "D-margin",
                "run_id": "filled-by-test",
                "binding": {},
                "problem": "Which margin: 3 or 2?",
                "affected_paths": ["/claim/margin"],
            }
        ],
    }


def setup_context(tmp_path):
    path = tmp_path / "source.md"
    path.write_text(
        "AI feedback is non-inferior. Margin 3 points. Elsewhere margin 2 points.", encoding="utf-8"
    )
    snapshot = snapshot_sources([path])
    store = Store(tmp_path / "study.db")
    rid = store.create_staged_run(snapshot, {})
    ctx = ToolContext(store, rid, None, None, set())
    data = payload(snapshot)
    data["decisions"][0].update(run_id=rid, binding=store.binding(rid).model_dump())
    return store, rid, snapshot, ctx, data


def test_understanding_uses_sources_and_keeps_intent_provisional(tmp_path):
    store, rid, snapshot, ctx, data = setup_context(tmp_path)
    llm = ScriptedLLM([tool_call("read_sources", {}), submit("submit_understanding", data)])
    task = AgentTask(
        id="understand-1",
        binding=store.binding(rid),
        stage_id="S2",
        objective="Understand source",
        package_ref=snapshot.id,
        design_version=None,
        allowed_tools=list(understanding.TOOLS),
        budget=TaskBudget(max_turns=3, max_tokens=1000),
        exit_conditions=["Submit context"],
    )
    result = run_agent(
        understanding.SPEC,
        task,
        understanding.brief(ctx),
        build_tools(ctx),
        understanding.submit_tool(ctx),
        llm,
        store,
        rid,
        ctx.accepted_call_ids,
    )
    assert result.completion_status == "completed"
    context = result.findings_or_changes["context"]
    assert context["package"]["inference"]["margin"] == 3
    assert context["field_annotations"]["estimand"]["status"] == "agent-proposed"
    assert snapshot.artifacts[0].text in llm.calls[1][0][-1]["content"]
    assert result.findings_or_changes["decisions"][0]["binding"]["design_version"] is None
    assert store.version(rid) is None
    store.close()


@pytest.mark.parametrize("corruption", ["source", "confirmation", "unsupported_sd"])
def test_understanding_rejects_invented_authority_or_numbers(tmp_path, corruption):
    store, rid, snapshot, ctx, data = setup_context(tmp_path)
    if corruption == "source":
        data["context"]["field_annotations"]["margin"]["source_span"] = (
            "Margin 3 points not present"
        )
    elif corruption == "confirmation":
        data["context"]["field_annotations"]["estimand"]["status"] = "researcher-confirmed"
    else:
        data["context"]["package"]["assumptions"] = [
            {
                "id": "A1",
                "statement": "SD",
                "quantity": {
                    "value": 10,
                    "unit": "points",
                    "provenance": "source",
                    "source_ref": snapshot.artifacts[0].ref,
                    "source_span": "Margin 3 points",
                },
            }
        ]
    with pytest.raises(SubmitRejected):
        understanding.submit_tool(ctx).handler(data)
    assert store.context(rid) is None
    store.close()


def test_explicit_missing_input_cannot_be_hidden_by_a_reference(tmp_path):
    main = tmp_path / "main.md"
    missing = tmp_path / "missing.md"
    main.write_text("[Additional](missing.md)", encoding="utf-8")
    from experiment_design.package import PackageFormatError, snapshot_sources

    with pytest.raises(PackageFormatError, match="cannot read input"):
        snapshot_sources([main, missing])
