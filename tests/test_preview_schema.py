"""Preview schemas stay small without weakening scientific input validation."""

import json
from hashlib import sha256

import pytest
from test_stage_pipeline import source_context, source_snapshot, supported_design

from experiment_design.agents import design
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.contracts import DesignChoice
from experiment_design.runtime.loop import SubmitRejected
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps, _agent


@pytest.fixture
def initial(tmp_path):
    store = Store(tmp_path / "preview.db")
    rid = store.create_staged_run(source_snapshot(), {})
    store.save_context(rid, source_context())
    yield ToolContext(store, rid, store.context(rid).package, None, set(), task_id="synthesis")
    store.close()


def test_initial_preview_schema_budget_retains_full_submission_contract(initial):
    tools = build_tools(initial)
    submission = design.submit_tool(initial).parameters
    # One full submission schema supplies the construction contract for both previews.
    assert "Design" in submission["$defs"]
    assert "claim" in submission["$defs"]["Design"]["properties"]
    offered = [tools[n].parameters for n in design.TOOLS] + [submission]
    assert sum(len(json.dumps(s, separators=(",", ":"))) for s in offered) < 14_000
    for name in ("compute_power", "run_validators"):
        preview = tools[name].parameters["properties"]["design"]
        assert preview["type"] == "object"
        assert "submit_design" in preview["description"]
    assert tools["compute_power"].parameters["$defs"]["Quantity"]["properties"]["provenance"]


def test_summary_schema_distinguishes_canonical_choices_and_accepts_old_summary_key(initial):
    reason = DesignChoice(
        choice="Class randomization",
        why="Assignment follows the supplied class-based intervention",
        alternatives_considered=["Individual assignment risks shared classroom exposure"],
        tradeoffs="Fewer independent units; precision needs design-specific assessment",
    )
    draft = supported_design().model_dump(mode="json")
    draft["rationale"] = [reason.model_dump(mode="json")]
    submission = design.submit_tool(initial)
    properties = submission.parameters["properties"]
    assert properties["summary"]["type"] == "string"
    assert "rationale" not in properties
    for name in ("summary", "rationale"):
        with pytest.raises(SubmitRejected):
            submission.handler({"design": draft, name: [reason.model_dump(mode="json")]})
        corrected = submission.handler({"design": draft, name: "Source-consistent synthesis"})
        assert corrected["design"]["rationale"] == [reason.model_dump(mode="json")]
        assert corrected["rationale"] == "Source-consistent synthesis"
    assert initial.store.version(initial.run_id) is None


def test_compact_preview_still_validates_design_and_source_authority(initial):
    tools = build_tools(initial)
    draft = supported_design().model_dump(mode="json")
    draft["analysis"]["method_id"] = "mixed_model"
    draft["analysis"]["model"] = "Mixed model with class random intercept"
    for name in ("run_validators", "compute_power"):
        with pytest.raises(ValueError, match="extra_forbidden"):
            tools[name].handler({"design": draft | {"invented_field": "invalid"}})
        forged = json.loads(json.dumps(draft))
        forged["scenarios"][0]["sd"]["value"] = 999
        with pytest.raises(ValueError, match="source span does not support"):
            tools[name].handler({"design": forged})
    results = tools["compute_power"].handler({"design": draft})
    assert results[0]["status"] == "not_computed"
    assert results[0]["power"] is None
    assert results[0]["implementation"] == "unavailable"
    assert tools["run_validators"].handler({"design": draft})


@pytest.mark.parametrize("contract", ["legacy", "compact", "current", "unexpected_drift"])
def test_interrupted_initial_task_keeps_its_original_preview_schema(initial, contract):
    from fakes import ScriptedJudge, ScriptedLLM, submit

    from experiment_design.config import RunConfig
    from experiment_design.contracts import AgentTask, TaskBudget
    from experiment_design.runtime.store import TaskRecord

    tools = build_tools(initial)
    # Recreate the actual pre-optimization contract, independent of any fallback code.
    from experiment_design.contracts import Design

    schema = Design.model_json_schema()
    definitions = schema.pop("$defs")
    if contract == "legacy":
        for name in ("run_validators", "compute_power"):
            tools[name].parameters["properties"]["design"] = schema
            tools[name].parameters["$defs"] = definitions
    submission = design.submit_tool(initial)
    if contract in {"legacy", "compact"}:
        properties = submission.parameters["properties"]
        properties.pop("summary", None)
        properties["rationale"] = {"title": "Rationale", "type": "string"}
        submission.parameters["required"] = ["design", "rationale"]
    offered = [tools[n] for n in design.TOOLS] + [submission]
    schemas = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in offered
    ]
    task = AgentTask(
        id="interrupted-synthesis",
        stage_id="S3",
        binding=initial.binding,
        objective=design.SPEC.system_prompt,
        package_ref=initial.run_id,
        design_version=None,
        allowed_tools=list(design.TOOLS),
        budget=TaskBudget(max_turns=15, max_tokens=150000),
        exit_conditions=["Valid structured submission"],
    )
    prefix = [
        {"role": "system", "content": design.SPEC.system_prompt},
        {"role": "user", "content": "Original immutable brief"},
    ]
    record = TaskRecord(
        task=task,
        status="interrupted",
        messages=prefix,
        schema_hash=sha256(json.dumps(schemas, sort_keys=True).encode()).hexdigest(),
    )
    if contract == "unexpected_drift":
        record.schema_hash = "unknown-contract"
    initial.store.save_task(initial.run_id, record)
    llm = ScriptedLLM(
        [
            submit(
                "submit_design",
                {
                    "design": supported_design().model_dump(mode="json"),
                    "rationale": "Resume original draft",
                },
            )
        ]
    )
    deps = Deps(initial.store, llm, ScriptedJudge([]), RunConfig())
    if contract == "unexpected_drift":
        with pytest.raises(ValueError, match="task tool schemas changed on resume"):
            _agent(deps, initial.run_id, design, ctx=initial, task_id=task.id, mode="initial")
        assert not llm.calls
        return
    result = _agent(deps, initial.run_id, design, ctx=initial, task_id=task.id, mode="initial")
    assert result.completion_status == "completed"
    assert llm.calls[0][1] == schemas
    assert llm.calls[0][0][:2] == prefix
    assert initial.store.task(task.id).schema_hash == record.schema_hash
