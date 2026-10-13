"""Hand-authored proposal cases; no production ranking or validators as oracles."""

from copy import deepcopy

from experiment_design.contracts import AgentTask, StageResult, TaskBudget


def framed_run(store, snapshot=None):
    """A grounded S2 fixture; scientific behavior uses the production stage handler."""
    from tests.support.scenarios import source_context, source_snapshot

    snapshot = snapshot or source_snapshot()
    rid = store.create_staged_run(snapshot, {})
    store.commit_stage(
        rid,
        StageResult(
            stage_id="S1", status="COMPLETE", binding=store.binding(rid), reason="Sources captured"
        ),
        "S2",
    )
    entry = store.events(rid)[-2].event_id
    context = source_context()
    context.package.hypothesis.method = None
    context.field_annotations.pop("/analysis/model")
    context.source_snapshot_id = snapshot.id
    store.save_context(rid, context)
    frame = StageResult(
        stage_id="S2",
        status="COMPLETE",
        binding=store.binding(rid),
        output_refs=["context:1"],
        reason=f"entry:{entry}",
    )
    store._put_artifact(rid, "framing", f"{entry}:1", frame)
    return rid


def advance_framing(rid, deps):
    """Drive just the real S2 handler for focused exploration/recovery tests."""
    from experiment_design.runtime.jev import JudgeBudgetStop
    from experiment_design.workflow import run_stage
    from experiment_design.workflow.stages.common import BudgetStop

    with deps.store.run_execution(rid):
        try:
            result = run_stage(rid, "S2", deps)
        except (BudgetStop, JudgeBudgetStop):
            deps.store.update_run(rid, status="BUDGET_STOP")
            return "BUDGET_STOP"
    return "NEEDS_RESEARCHER" if result.status == "NEEDS_INPUT" else "RUNNING"


def historical_exploration(store, snapshot):
    """Load a historical candidate packet for read/export tests; no old controller."""
    from uuid import uuid4

    from experiment_design.design.exploration import ExplorationPolicy
    from experiment_design.persistence.store import RunRecord, now

    rid = "r" + uuid4().hex[:8]
    policy = ExplorationPolicy()
    snapshot = snapshot.model_copy(update={"schema_version": 1, "policy": policy, "run_id": rid})
    run = RunRecord(
        run_id=rid,
        schema_version=2,
        source_snapshot_id=snapshot.source.id,
        status="EXPLORED",
        config={},
        created_at=now(),
    )
    with store.transaction():
        store.connection.execute("INSERT INTO runs VALUES (?,?)", (rid, run.model_dump_json()))
        store.append_event(rid, "run.updated", run.model_dump(mode="json"))
        store._put_artifact(rid, "source", snapshot.source.id, snapshot.source)
        store._put_artifact(rid, "exploration_policy", rid, policy)
        store._put_artifact(rid, "exploration_snapshot", snapshot.id, snapshot)
    return rid


def exploration_payload(case: str = "useful_probe", *, source_ref: str = "S1") -> dict:
    candidate = {
        "id": "probe-a",
        "purpose": "Check measurement feasibility",
        "question": "Can the proposed measure detect the intended signal?",
        "target_uncertainty": "Measurement reliability",
        "alternative_explanations": ["An apparent effect is measurement drift"],
        "method_sketch": "Repeat blinded measurements on a small calibration sample",
        "outcomes": [
            {
                "observation": "Measurements disagree across repeats",
                "next_step": "Improve the measurement before an efficacy experiment",
                "inference_limit": "This does not test efficacy",
            }
        ],
        "assumptions": ["Repeat measurement is possible"],
        "evidence_refs": [],
        "resources": {
            "requirements": ["Calibration sample"],
            "cost_basis": "Small repeated-measurement probe; actual cost unknown",
            "unknowns": ["Sample access"],
        },
        "feasibility": "unknown",
        "feasibility_basis": "Sample access has not been established",
        "conditions": [],
        "limitations": ["Cannot estimate a treatment effect"],
    }
    body = {
        "intent_interpretation": "Investigate the proposed signal before committing a full plan",
        "intent_source_refs": [source_ref],
        "candidates": [candidate],
        "priorities": [
            {
                "candidate_ids": ["probe-a"],
                "rationale": "Resolve the measurement uncertainty cheaply first",
                "evidence_refs": [],
                "conditions": ["Confirm sample access"],
            }
        ],
        "unknowns": ["Sample availability"],
        "focused_questions": [],
        "rationale": "A provisional methodological proposal, not empirical support",
        "defer_reason": None,
    }
    if case in {"conditional_tie", "unavailable_first_tier"}:
        other = deepcopy(candidate)
        other["id"] = "probe-b"
        other["question"] = "Could an explicit confound explain the signal?"
        other["target_uncertainty"] = "Confounding"
        body["candidates"].append(other)
        if case == "conditional_tie":
            candidate["feasibility"] = "conditional"
            candidate["conditions"] = ["Sample access is confirmed"]
            body["priorities"][0]["candidate_ids"].append("probe-b")
        else:
            candidate["feasibility"] = "unavailable"
            candidate["feasibility_basis"] = "Supplied constraint disallows repeated measurements"
            candidate["evidence_refs"] = [source_ref]
            body["priorities"].append(
                {
                    "candidate_ids": ["probe-b"],
                    "rationale": "Available alternative",
                    "evidence_refs": [],
                    "conditions": [],
                }
            )
    elif case == "missing_input":
        body["candidates"] = []
        body["priorities"] = []
        body["defer_reason"] = "The intended signal is not specified"
        body["focused_questions"] = ["What signal should the probe investigate?"]
    elif case != "useful_probe":
        raise ValueError(case)
    return body


def exploration_task(store, rid: str) -> AgentTask:
    entry = next(e.event_id for e in store.events(rid) if e.type == "stage.result")
    return AgentTask(
        id=f"explore:{rid}:{entry}:1:0",
        stage_id="S2",
        binding=store.binding(rid),
        objective="Explore candidates",
        package_ref=store.run(rid).source_snapshot_id,
        design_version=None,
        allowed_tools=["read_sources", "lookup_evidence"],
        budget=TaskBudget(max_turns=5, max_tokens=1000),
        exit_conditions=["Submit a bounded exploration"],
    )
