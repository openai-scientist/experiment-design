"""One researcher decision lifecycle from clarification through authorized application."""

from experiment_design.contracts import (
    DecisionResponse,
    FieldAnnotation,
    Patch,
    ResearchContext,
    ResearcherDecision,
    ReviewIssue,
)
from experiment_design.patch import apply_patch, design_diff, package_claim, resolve
from experiment_design.runtime.store import Store


def clarification_options(
    decision: ResearcherDecision, *, informative_options: bool, blocks_required_work: bool
) -> list[str]:
    options = ["EXPLORE", "STOP"]
    if informative_options or blocks_required_work:
        options.insert(0, "ASK_RESEARCHER")
    return options


def affected_work(
    before: ResearchContext, after: ResearchContext, diff_paths: list[str]
) -> list[str]:
    if before != after or any(
        p.startswith(("/claim", "/analysis", "/sampling", "/measurement", "/scenarios"))
        for p in diff_paths
    ):
        return ["synthesis", "calculations", "review", "verification", "qualification"]
    return ["review", "qualification"] if diff_paths else []


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
        if decision.status != "AWAITING_RESEARCHER":
            raise ValueError("decision is not awaiting a researcher response")
        store.check_binding(run_id, decision.binding)
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
        if (
            response.action in {"PROPOSE_ALTERNATIVE", "REQUEST_CHANGES"}
            and not response.text.strip()
        ):
            raise ValueError("alternative text is required")
        if response.affected_paths:
            if response.action != "REQUEST_CHANGES":
                raise ValueError("affected paths are only valid for a final change request")
            for path in response.affected_paths:
                resolve(store.version(run_id).design, path)
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
    if result.get("context"):
        proposed_context = ResearchContext.model_validate(result["context"])
        if (
            proposed_context.id != context.id
            or proposed_context.source_snapshot_id != context.source_snapshot_id
            or proposed_context.revision != context.revision + 1
        ):
            raise ValueError("authorized context revision differs")
        old_claim, new_claim = (
            package_claim(context.package),
            package_claim(proposed_context.package),
        )
        changed_paths = [f"/claim/{key}" for key in old_claim if old_claim[key] != new_claim[key]]
        for key in ("research_question", "constraints", "budget", "assumptions", "sources"):
            if getattr(context.package, key) != getattr(proposed_context.package, key):
                changed_paths.append(f"/context/package/{key}")
        if any(
            not any(
                path == allowed or path.startswith(allowed + "/")
                for allowed in decision.affected_paths
            )
            for path in changed_paths
        ):
            raise ValueError("context interpretation exceeds allowed scope")
        if proposed_context.field_annotations != context.field_annotations:
            raise ValueError("context authority is assigned during application")
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
        context_changes = decision.interpretation.get("context")
        next_context = (
            ResearchContext.model_validate(context_changes)
            if context_changes
            else context.model_copy(deep=True)
        )
        if patch:
            mapping = {
                "margin": ("inference", "margin"),
                "estimand": ("inference", "estimand"),
                "contrast_type": ("inference", "contrast_type"),
                "hypothesis": ("hypothesis", "statement"),
                "prediction": ("hypothesis", "prediction"),
                "failure_condition": ("hypothesis", "failure_condition"),
                "exposure": ("hypothesis", "exposure"),
                "primary_outcome": ("outcome", "name"),
                "outcome_unit": ("outcome", "unit"),
                "outcome_kind": ("outcome", "kind"),
                "direction": ("outcome", "direction"),
            }
            for op in patch.ops:
                parts = op.path.strip("/").split("/")
                if len(parts) in {2, 3} and parts[0] == "claim" and parts[1] in mapping:
                    group, field = mapping[parts[1]]
                    record = getattr(next_context.package, group)
                    if record is not None:
                        setattr(record, field, op.value)
                    next_context.field_annotations[parts[1]] = FieldAnnotation(
                        status="researcher-confirmed",
                        source_ref=f"researcher:{decision.id}:{response.response_id}",
                        note=response.text,
                    )
        if patch or context_changes:
            next_context.revision = context.revision + 1
            for name, value in package_claim(next_context.package).items():
                if value != package_claim(context.package)[name]:
                    next_context.field_annotations[name] = FieldAnnotation(
                        status="researcher-confirmed",
                        source_ref=f"researcher:{decision.id}:{response.response_id}",
                        note=response.text,
                    )
            next_context = ResearchContext.model_validate(next_context.model_dump())
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
            issue = store.save_issue(
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
            decision.application["issue_id"] = issue.id
        return store.save_decision(decision)
