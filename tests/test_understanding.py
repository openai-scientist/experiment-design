import hashlib

import pytest

from experiment_design.agents import understanding
from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.contracts import AgentTask, TaskBudget
from experiment_design.package import PackageFormatError, snapshot_sources
from experiment_design.runtime.loop import SubmitRejected, run_agent
from experiment_design.runtime.store import Store
from tests.support.exploration import exploration_payload
from tests.support.providers import ScriptedLLM, submit, tool_call


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


@pytest.mark.parametrize("change", ["value", "downgrade", "remove"])
def test_understanding_preserves_confirmed_intent(tmp_path, change):
    from experiment_design.contracts import FieldAnnotation, ResearchContext

    store, rid, snapshot, ctx, data = setup_context(tmp_path)
    previous = ResearchContext.model_validate(data["context"])
    previous.field_annotations["margin"] = FieldAnnotation(
        status="researcher-confirmed", source_ref="researcher:D1:reply"
    )
    store.save_context(rid, previous)
    ctx.binding = store.binding(rid)
    revised = previous.model_copy(deep=True)
    revised.revision = 2
    if change == "value":
        revised.package.inference.margin = 2
    elif change == "downgrade":
        revised.field_annotations["margin"].status = "agent-proposed"
    else:
        del revised.field_annotations["margin"]
    with pytest.raises(SubmitRejected, match="confirmed"):
        understanding.submit_tool(ctx).handler(
            {"context": revised.model_dump(mode="json"), "decisions": []}
        )
    assert store.context(rid) == previous
    store.close()


def test_understanding_preserves_confirmed_context_constraint(tmp_path):
    from experiment_design.agents.tools import sources
    from experiment_design.contracts import FieldAnnotation, ResearchContext

    store, rid, snapshot, ctx, data = setup_context(tmp_path)
    previous = ResearchContext.model_validate(data["context"])
    previous.package.sources = sources(ctx)
    previous.package.constraints = ["At most 60 classes"]
    previous.field_annotations["/context/package/constraints"] = FieldAnnotation(
        status="researcher-confirmed", source_ref="researcher:resources:reply"
    )
    store.save_context(rid, previous)
    ctx.binding = store.binding(rid)
    revised = previous.model_copy(deep=True)
    revised.revision = 2
    revised.package.constraints = ["Unlimited classes"]
    with pytest.raises(SubmitRejected, match="confirmed"):
        understanding.submit_tool(ctx).handler(
            {"context": revised.model_dump(mode="json"), "decisions": []}
        )
    assert store.context(rid) == previous
    store.close()


def test_typed_source_commitment_cannot_claim_unsupported_number(tmp_path):
    store, rid, snapshot, ctx, data = setup_context(tmp_path)
    data["context"]["field_annotations"]["/analysis/alpha"] = {
        "status": "source-stated",
        "source_ref": snapshot.artifacts[0].ref,
        "source_span": "Margin 3 points",
        "value": 0.025,
    }
    with pytest.raises(SubmitRejected, match="commitment"):
        understanding.submit_tool(ctx).handler(data)
    assert store.context(rid) is None
    store.close()


def test_margin_rejection_identifies_the_recoverable_context_key(tmp_path):
    store, _, _, ctx, data = setup_context(tmp_path)
    data["context"]["field_annotations"]["inference.margin"] = data["context"][
        "field_annotations"
    ].pop("margin")
    with pytest.raises(SubmitRejected) as rejected:
        understanding.submit_tool(ctx).handler(data)
    feedback = " ".join(rejected.value.errors)
    assert "context.field_annotations['margin']" in feedback
    assert "source_ref" in feedback and "source_span" in feedback
    data["context"]["field_annotations"]["margin"] = data["context"]["field_annotations"].pop(
        "inference.margin"
    )
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["context"]["package"]["inference"]["margin"] == 3
    assert accepted["context"]["field_annotations"]["margin"]["status"] == "source-stated"
    store.close()


@pytest.mark.parametrize(
    "annotation, detail",
    [
        ("hypothesis.statement", "hypothesis"),
        ("/assumptions/A1", "JSON pointer"),
        ("/analysis/alpha", "typed commitment value"),
    ],
)
def test_understanding_rejects_context_that_cannot_bind_scientific_commitments(
    tmp_path, annotation, detail
):
    store, _, snapshot, ctx, data = setup_context(tmp_path)
    data["context"]["field_annotations"][annotation] = {
        "status": "source-stated",
        "source_ref": snapshot.artifacts[0].ref,
        "source_span": "AI feedback is non-inferior",
    }
    with pytest.raises(SubmitRejected) as rejected:
        understanding.submit_tool(ctx).handler(data)
    assert annotation in " ".join(rejected.value.errors)
    assert detail in " ".join(rejected.value.errors)
    del data["context"]["field_annotations"][annotation]
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["context"]["field_annotations"]["margin"]["status"] == "source-stated"
    store.close()


def test_understanding_decision_feedback_explains_permitted_agent_status(tmp_path):
    store, _, _, ctx, data = setup_context(tmp_path)
    data["decisions"][0]["status"] = "AWAITING_RESEARCHER"
    with pytest.raises(SubmitRejected) as rejected:
        understanding.submit_tool(ctx).handler(data)
    feedback = " ".join(rejected.value.errors)
    assert "IDENTIFIED" in feedback and "EXPLORING" in feedback
    data["decisions"][0]["status"] = "IDENTIFIED"
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["decisions"][0]["status"] == "IDENTIFIED"
    store.close()


def test_recorded_answer_can_recover_authority_without_losing_the_estimand(tmp_path):
    import json

    stated_estimand = "ITT mean end-of-term score difference, AI minus teacher."
    path = tmp_path / "recorded-answer.md"
    path.write_text("Margin 3 points.\nResearcher answer: " + stated_estimand, encoding="utf-8")
    snapshot = snapshot_sources([path])
    data = payload(snapshot)
    store = Store(tmp_path / "recorded-answer.db")
    rid = store.create_staged_run(snapshot, {})
    ctx = ToolContext(store, rid, None, None, set())
    data["decisions"] = []
    data["context"]["package"]["inference"]["estimand"] = stated_estimand
    data["context"]["field_annotations"]["estimand"] = {
        "status": "researcher-confirmed",
        "source_ref": snapshot.artifacts[0].ref,
        "source_span": stated_estimand,
    }
    with pytest.raises(SubmitRejected) as rejected:
        understanding.submit_tool(ctx).handler(data)
    feedback = " ".join(rejected.value.errors)
    alternative = json.loads(feedback.split("source-stated alternative: ", 1)[1])
    data["context"]["field_annotations"]["estimand"] = alternative
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["context"]["package"]["inference"]["estimand"] == stated_estimand
    assert accepted["context"]["field_annotations"]["estimand"]["status"] == "source-stated"
    assert accepted["decisions"] == []
    assert store.context(rid) is None
    store.close()


def applied_understanding(tmp_path):
    from experiment_design.contracts import DecisionResponse, ResearchContext, ResearcherDecision

    store, rid, _, ctx, data = setup_context(tmp_path)
    previous = ResearchContext.model_validate(data["context"])
    store.save_context(rid, previous)
    decision = store.save_decision(
        ResearcherDecision(
            id="confirmed-margin",
            run_id=rid,
            binding=store.binding(rid),
            status="APPLIED",
            problem="Confirm margin",
            affected_paths=["/claim/margin"],
            responses=[
                DecisionResponse(
                    revision=1,
                    action="PROPOSE_ALTERNATIVE",
                    text="Use 3 points",
                    researcher="PI",
                    response_id="margin-reply",
                )
            ],
            application={"response_id": "margin-reply", "context_revision": 1},
            verification={"status": "pending"},
        )
    )
    ctx.binding = store.binding(rid)
    revised = previous.model_copy(deep=True)
    revised.revision = 2
    revised.unknowns = ["Estimand unspecified"]
    submission = {
        "context": revised.model_dump(mode="json"),
        "decisions": [],
        "resolved_unknowns": {decision.id: ["Contradictory margins; confirm 3 or 2"]},
    }
    return store, rid, ctx, submission


def test_understanding_requires_auditable_resolution_and_preserves_other_unknowns(tmp_path):
    store, _, ctx, data = applied_understanding(tmp_path)
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["resolved_unknowns"] == {
        "confirmed-margin": ["Contradictory margins; confirm 3 or 2"]
    }
    assert accepted["context"]["unknowns"] == ["Estimand unspecified"]
    store.close()


def test_applied_reply_can_explicitly_resolve_no_unknowns(tmp_path):
    store, rid, ctx, data = applied_understanding(tmp_path)
    data["context"]["unknowns"] = store.context(rid).unknowns
    data["resolved_unknowns"] = {"confirmed-margin": []}
    accepted = understanding.submit_tool(ctx).handler(data)
    assert accepted["context"]["unknowns"] == [
        "Contradictory margins; confirm 3 or 2",
        "Estimand unspecified",
    ]
    assert accepted["resolved_unknowns"] == {"confirmed-margin": []}
    store.close()


def test_understanding_stage_merges_resolution_audit_without_conferring_verification(tmp_path):
    from experiment_design.config import RunConfig
    from experiment_design.workflow import Deps, run_stage
    from tests.support.providers import ScriptedJudge

    store, rid, _, data = applied_understanding(tmp_path)
    decision = store.decision(rid, "confirmed-margin")
    decision.reconciliation = {"independent": True, "changed_paths": ["/sampling/inclusion"]}
    store.save_decision(decision)
    store.update_run(rid, stage_id="S2")
    result = run_stage(
        rid,
        "S2",
        Deps(
            store,
            ScriptedLLM(
                [
                    submit("submit_understanding", data),
                    submit(
                        "submit_exploration",
                        exploration_payload(source_ref=store.source_snapshot(rid).artifacts[0].ref),
                    ),
                ]
            ),
            ScriptedJudge([]),
            RunConfig(),
        ),
    )
    assert result.status == "COMPLETE"
    recorded = store.decision(rid, decision.id)
    assert recorded.reconciliation == {
        "independent": True,
        "changed_paths": ["/sampling/inclusion"],
        "unknown_resolutions": [
            {
                "decision_id": "confirmed-margin",
                "response_id": "margin-reply",
                "context_revision": 2,
                "unknowns": ["Contradictory margins; confirm 3 or 2"],
            }
        ],
    }
    assert recorded.verification["status"] == "pending"
    assert store.context(rid).unknowns == ["Estimand unspecified"]
    store.close()


def test_understanding_cannot_replace_a_source_stated_commitment_with_another_span(tmp_path):
    from experiment_design.contracts import ResearchContext

    store, rid, _, ctx, data = setup_context(tmp_path)
    previous = ResearchContext.model_validate(data["context"])
    store.save_context(rid, previous)
    ctx.binding = store.binding(rid)
    revised = previous.model_copy(deep=True)
    revised.revision = 2
    revised.package.inference.margin = 2
    revised.field_annotations["margin"].source_span = "Elsewhere margin 2 points"
    with pytest.raises(SubmitRejected, match="intent cannot change"):
        understanding.submit_tool(ctx).handler({"context": revised.model_dump(mode="json")})
    assert store.context(rid).package.inference.margin == 3
    store.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "foreign",
        "unapplied",
        "reply",
        "invented",
        "retained",
        "uncovered",
        "overwrite",
        "no_reply",
        "verified",
        "final",
    ],
)
def test_understanding_rejects_invalid_unknown_resolution(tmp_path, corruption):
    store, rid, ctx, data = applied_understanding(tmp_path)
    decision = store.decision(rid, "confirmed-margin")
    if corruption == "missing":
        data.pop("resolved_unknowns")
        data["context"]["unknowns"] = store.context(rid).unknowns
    elif corruption == "foreign":
        data["resolved_unknowns"]["another-run"] = []
    elif corruption == "unapplied":
        decision.status = "ANSWERED"
        store.save_decision(decision)
    elif corruption == "reply":
        decision.application["response_id"] = "wrong-reply"
        store.save_decision(decision)
    elif corruption == "invented":
        data["resolved_unknowns"][decision.id].append("Approximate margin")
    elif corruption == "retained":
        data["context"]["unknowns"].append("Contradictory margins; confirm 3 or 2")
    elif corruption == "uncovered":
        data["context"]["unknowns"] = []
    elif corruption == "overwrite":
        replacement = decision.model_dump(mode="json")
        replacement.update(
            status="IDENTIFIED", responses=[], application={}, binding=ctx.binding.model_dump()
        )
        data["decisions"] = [replacement]
    elif corruption == "no_reply":
        decision = decision.model_copy(update={"id": "missing-reply", "responses": []})
        store.save_decision(decision)
        data["resolved_unknowns"][decision.id] = []
    elif corruption == "verified":
        decision.verification = {"status": "verified"}
        store.save_decision(decision)
    elif corruption == "final":
        decision.revision += 1
        decision.purpose = "final_review"
        store.save_decision(decision)
    with pytest.raises(SubmitRejected):
        understanding.submit_tool(ctx).handler(data)
    assert store.context(rid).revision == 1
    store.close()


@pytest.mark.parametrize("metadata", ["reconciliation", "verification"])
def test_understanding_cannot_forge_workflow_metadata_on_a_proposal(tmp_path, metadata):
    store, _, _, ctx, data = setup_context(tmp_path)
    data["decisions"][0][metadata] = (
        {"unknown_resolutions": [{"unknowns": ["Estimand unspecified"]}]}
        if metadata == "reconciliation"
        else {"status": "verified"}
    )
    with pytest.raises(SubmitRejected):
        understanding.submit_tool(ctx).handler(data)
    assert store.decisions(ctx.run_id) == []
    store.close()


def test_workflow_recorded_unknown_resolution_cannot_be_resurrected(tmp_path):
    from experiment_design.config import RunConfig
    from experiment_design.workflow import Deps, run_stage
    from tests.support.providers import ScriptedJudge

    store, rid, ctx, data = applied_understanding(tmp_path)
    store.update_run(rid, stage_id="S2")
    run_stage(
        rid,
        "S2",
        Deps(
            store,
            ScriptedLLM(
                [
                    submit("submit_understanding", data),
                    submit(
                        "submit_exploration",
                        exploration_payload(source_ref=store.source_snapshot(rid).artifacts[0].ref),
                    ),
                ]
            ),
            ScriptedJudge([]),
            RunConfig(),
        ),
    )
    ctx.binding = store.binding(rid)
    revised = store.context(rid).model_copy(deep=True)
    revised.revision += 1
    revised.unknowns.append("Contradictory margins; confirm 3 or 2")
    with pytest.raises(SubmitRejected, match="reintroduced"):
        understanding.submit_tool(ctx).handler(
            {
                "context": revised.model_dump(mode="json"),
                "resolved_unknowns": {"confirmed-margin": []},
            }
        )
    assert store.context(rid).unknowns == ["Estimand unspecified"]
    store.close()


@pytest.mark.parametrize(
    "corruption",
    [
        "unapplied",
        "final",
        "no_reply",
        "application_reply",
        "application_revision",
        "application_missing_context",
        "source_binding",
        "application_revision_bool",
        "container",
        "entry",
        "missing_unknowns",
        "unknown_type",
        "decision",
        "response",
        "revision_type",
        "revision_bool",
        "missing_context",
        "not_after_application",
        "no_removal",
        "not_previously_unknown",
    ],
)
def test_untrusted_or_malformed_recorded_audit_cannot_suppress_an_unknown(tmp_path, corruption):
    from experiment_design.contracts import DecisionResponse, ResearchContext, ResearcherDecision

    store, rid, _, ctx, data = setup_context(tmp_path)
    previous = ResearchContext.model_validate(data["context"])
    store.save_context(rid, previous)
    resolved_context = previous.model_copy(deep=True)
    resolved_context.revision = 2
    resolved_context.unknowns = ["Estimand unspecified"]
    audit = {
        "decision_id": "historical",
        "response_id": "reply",
        "context_revision": 2,
        "unknowns": ["Contradictory margins; confirm 3 or 2"],
    }
    decision = ResearcherDecision(
        id="historical",
        run_id=rid,
        binding=store.binding(rid),
        status="APPLIED",
        problem="Recorded clarification",
        responses=[
            DecisionResponse(
                revision=1,
                action="PROPOSE_ALTERNATIVE",
                text="Use 3 points",
                researcher="PI",
                response_id="reply",
            )
        ],
        application={"response_id": "reply", "context_revision": 1},
        verification={"status": "verified"},
        reconciliation={"unknown_resolutions": [audit]},
    )
    if corruption == "unapplied":
        decision.status = "IDENTIFIED"
    elif corruption == "final":
        decision.purpose = "final_review"
    elif corruption == "no_reply":
        decision.responses = []
    elif corruption == "application_reply":
        decision.application["response_id"] = "another-reply"
    elif corruption == "application_revision":
        decision.application["context_revision"] = 1.0
    elif corruption == "application_missing_context":
        decision.application["context_revision"] = 0
    elif corruption == "application_revision_bool":
        decision.application["context_revision"] = True
    elif corruption == "source_binding":
        decision.binding.source_snapshot_id = "unrelated-snapshot"
    elif corruption == "container":
        decision.reconciliation["unknown_resolutions"] = {"forged": audit}
    elif corruption == "entry":
        decision.reconciliation["unknown_resolutions"] = [None]
    elif corruption == "missing_unknowns":
        audit.pop("unknowns")
    elif corruption == "unknown_type":
        audit["unknowns"] = ["Contradictory margins; confirm 3 or 2", ["malformed"]]
    elif corruption == "decision":
        audit["decision_id"] = "unrelated-decision"
    elif corruption == "response":
        audit["response_id"] = "unrelated-reply"
    elif corruption == "revision_type":
        audit["context_revision"] = "2"
    elif corruption == "revision_bool":
        audit["context_revision"] = True
    elif corruption == "missing_context":
        audit["context_revision"] = 999
    elif corruption == "not_after_application":
        decision.application["context_revision"] = 2
    elif corruption == "no_removal":
        resolved_context.unknowns = previous.unknowns
    else:
        previous.unknowns = ["Estimand unspecified"]
        previous.revision = 2
        resolved_context = previous
        audit["unknowns"] = ["District SD missing"]
    store.save_context(rid, resolved_context)
    store.save_decision(decision)
    ctx.binding = store.binding(rid)
    revised = resolved_context.model_copy(deep=True)
    revised.revision = 3
    revised.unknowns = ["Contradictory margins; confirm 3 or 2", "Estimand unspecified"]
    if corruption == "not_previously_unknown":
        revised.unknowns.append("District SD missing")
    accepted = understanding.submit_tool(ctx).handler({"context": revised.model_dump(mode="json")})
    assert accepted["context"]["unknowns"] == revised.unknowns
    assert store.context(rid).revision == 2
    store.close()
