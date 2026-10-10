"""One researcher decision lifecycle from clarification through authorized application."""

from experiment_design.contracts import (
    DecisionResponse,
    Patch,
    ResearchContext,
    ResearcherDecision,
    ReviewIssue,
)
from experiment_design.patch import apply_patch, design_diff
from experiment_design.runtime.store import Store


def clarification_options(
    decision: ResearcherDecision, *, informative_options: bool, blocks_required_work: bool
) -> list[str]:
    options = ["EXPLORE", "STOP"]
    if informative_options or blocks_required_work:
        options.insert(0, "ASK_RESEARCHER")
    return options


def respond(
    store: Store, run_id: str, decision_id: str, response: DecisionResponse
) -> ResearcherDecision:
    with store.transaction():
        decision = store.decision(run_id, decision_id)
        existing = next(
            (r for r in decision.responses if r.response_id == response.response_id), None
        )
        if existing:
            if existing != response:
                raise ValueError("response ID already has different content")
            return decision
        if response.revision != decision.revision:
            raise ValueError("stale decision revision")
        if not response.researcher.strip() or not response.response_id.strip():
            raise ValueError("researcher identity and response ID are required")
        allowed = (
            {"APPROVE_VERSION", "REQUEST_CHANGES", "REJECT", "HOLD"}
            if decision.purpose == "final_review"
            else {"SELECT_OPTION", "PROPOSE_ALTERNATIVE", "DEFER"}
        )
        if response.action not in allowed:
            raise ValueError("action does not match decision purpose")
        if response.action == "SELECT_OPTION" and response.option_id not in {
            o.id for o in decision.options
        }:
            raise ValueError("unknown option ID")
        if response.action == "PROPOSE_ALTERNATIVE" and not response.text.strip():
            raise ValueError("alternative text is required")
        decision.responses.append(response)
        decision.status = (
            "AWAITING_RESEARCHER" if response.action in {"DEFER", "HOLD"} else "ANSWERED"
        )
        decision.interpretation = {}
        decision.application = {}
        return store.save_decision(decision)


def interpret(
    decision: ResearcherDecision, context: ResearchContext, result: dict
) -> ResearcherDecision:
    if decision.status != "ANSWERED" or not decision.responses:
        raise ValueError("decision has no answer to interpret")
    if decision.binding.source_snapshot_id != context.source_snapshot_id:
        raise ValueError("interpretation source binding differs")
    proposed = Patch.model_validate(result["patch"]) if result.get("patch") is not None else None
    if proposed and any(
        not any(op.path == p or op.path.startswith(p + "/") for p in decision.affected_paths)
        for op in proposed.ops
    ):
        raise ValueError("interpretation exceeds allowed scope")
    updated = decision.model_copy(deep=True)
    updated.interpretation = dict(result)
    if proposed:
        updated.interpretation["patch"] = proposed.model_dump(mode="json")
    updated.interpretation["response_id"] = decision.responses[-1].response_id
    if result.get("ambiguous"):
        updated.interpretation["authorized"] = False
    return updated


def apply_decision(
    store: Store, run_id: str, decision_id: str, *, context: ResearchContext, patch: Patch | None
) -> ResearcherDecision:
    with store.transaction():
        decision = store.decision(run_id, decision_id)
        response = decision.responses[-1] if decision.responses else None
        if response and decision.application.get("response_id") == response.response_id:
            return decision
        if (
            not response
            or not decision.interpretation.get("authorized")
            or decision.interpretation.get("ambiguous")
            or decision.interpretation.get("response_id") != response.response_id
        ):
            raise ValueError("decision needs an unambiguous authorized interpretation")
        store.check_binding(run_id, decision.binding)
        if store.context(run_id) != context:
            raise ValueError("stale application context")
        expected = decision.interpretation.get("patch")
        if (patch.model_dump(mode="json") if patch else None) != expected:
            raise ValueError("application patch differs from interpretation")
        version = store.version(run_id)
        paths = []
        if version and patch:
            changed = apply_patch(
                version.design,
                patch,
                source_ids={s.id for s in context.package.sources},
                author="researcher",
                context=context,
                authorization=decision,
            )
            paths = [d["path"] for d in design_diff(version.design, changed)]
            version = store.add_version(
                run_id, changed, author="researcher", parent=version.version, patch=patch
            )
        next_context = context.model_copy(deep=True)
        context_changes = decision.interpretation.get("context")
        if context_changes:
            next_context = ResearchContext.model_validate(context_changes)
            if (
                next_context.source_snapshot_id != context.source_snapshot_id
                or next_context.revision != context.revision + 1
            ):
                raise ValueError("authorized context revision differs")
            store.save_context(run_id, next_context)
        decision.status = "APPLIED"
        decision.application = {
            "response_id": response.response_id,
            "version": version.version if version else None,
            "context_revision": next_context.revision,
            "affected_paths": paths,
        }
        decision.verification = {"status": "pending", "evidence_refs": []}
        if version:
            store.save_issue(
                ReviewIssue(
                    run_id=run_id,
                    raised_by="researcher",
                    version_raised=version.version,
                    severity="MAJOR",
                    paths=paths or decision.affected_paths or ["/"],
                    mechanism=f"Researcher decision {decision.id} requires reconciliation",
                    consequence=(
                        "Changed intent or evidence needs recomputation "
                        "and independent verification"
                    ),
                    basis="reasoning",
                )
            )
        return store.save_decision(decision)
