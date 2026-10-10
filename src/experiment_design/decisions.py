"""One researcher decision lifecycle from clarification through authorized application."""

from experiment_design.contracts import (
    DecisionResponse,
    FieldAnnotation,
    Patch,
    ResearchContext,
    ResearcherDecision,
    ReviewIssue,
)
from experiment_design.package import all_sources
from experiment_design.patch import (
    apply_context_patch,
    apply_patch,
    design_diff,
    package_claim,
    record_diff,
    resolve,
    scientific_path,
)
from experiment_design.runtime.store import Store


def context_change_paths(before: ResearchContext, after: ResearchContext) -> list[str]:
    return [
        scientific_path("/context" + d["path"])
        for d in record_diff(before, after)
        if d["path"] != "/revision" and not d["path"].startswith("/field_annotations/")
    ]


def reconcile_answer(store: Store, run_id: str, decision: ResearcherDecision) -> ResearcherDecision:
    """Keep independent recorded intent; re-present intent affected by intervening changes."""
    current = store.binding(run_id)
    if decision.binding.model_dump(exclude={"event_id"}) == current.model_dump(
        exclude={"event_id"}
    ):
        return decision
    before = store.context(run_id, decision.binding.context_revision)
    after = store.context(run_id)
    changes = context_change_paths(before, after)
    if decision.binding.design_version and current.design_version:
        changes += [
            d["path"]
            for d in design_diff(
                store.version(run_id, decision.binding.design_version).design,
                store.version(run_id).design,
            )
        ]
    dependent = bool(decision.dependencies) or any(
        path.startswith(
            (
                "/context/package/constraints",
                "/context/package/assumptions",
                "/context/package/sources",
                "/context/package/budget",
            )
        )
        or any(
            path == scope or path.startswith(scope + "/") or scope.startswith(path + "/")
            for scope in map(scientific_path, decision.affected_paths)
        )
        for path in changes
    )
    response = decision.responses[-1]
    decision.reconciliation = {
        "response_id": response.response_id,
        "response_revision": response.revision,
        "previous_binding": decision.binding.model_dump(),
        "independent": not dependent,
        "changed_paths": changes,
    }
    decision.revision += 1
    decision.binding = current
    decision.interpretation = {}
    if dependent:
        decision.status = "AWAITING_RESEARCHER"
        decision.recommendation = (
            "Review the recorded answer against the changed scientific context."
        )
    return store.save_decision(decision)


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
        decision.reconciliation = {}
        return store.save_decision(decision)


def interpret(
    decision: ResearcherDecision, context: ResearchContext, result: dict
) -> ResearcherDecision:
    if decision.status != "ANSWERED" or not decision.responses:
        raise ValueError("decision has no answer to interpret")
    if decision.binding.source_snapshot_id != context.source_snapshot_id:
        raise ValueError("interpretation source binding differs")
    result = dict(result)
    context_patch = result.pop("context_patch", None)
    if context_patch is not None:
        if result.get("context") is not None or result.get("patch") is not None:
            raise ValueError("context_patch cannot be combined with context or design patch")
        if decision.binding.design_version is not None:
            raise ValueError("context patch requires a pre-design decision; use a design patch")
        result["context"] = apply_context_patch(
            context, Patch.model_validate(context_patch)
        ).model_dump(mode="json")
    allowed_paths = [scientific_path(path) for path in decision.affected_paths]
    proposed = Patch.model_validate(result["patch"]) if result.get("patch") is not None else None
    if proposed and any(
        not any(op.path == p or op.path.startswith(p + "/") for p in allowed_paths)
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
        changed_paths = context_change_paths(context, proposed_context)
        if any(
            not any(path == allowed or path.startswith(allowed + "/") for allowed in allowed_paths)
            for path in changed_paths
        ):
            raise ValueError(
                "context interpretation exceeds allowed scope; changed_paths="
                + repr(changed_paths)
                + "; allowed_paths="
                + repr(allowed_paths)
                + ". Preserve sources, unknowns, decision_ids and all other unrequested fields."
            )
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
                source_ids={s.id for s in all_sources(context, store.decisions(run_id))},
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
                if len(parts) in {2, 3} and parts[:2] == ["claim", "inference_scope"]:
                    next_context.package.scope = op.value
                if parts[0] != "claim":
                    if op.path == "/analysis/model":
                        next_context.package.hypothesis.method = op.value
                    next_context.field_annotations[op.path] = FieldAnnotation(
                        status="researcher-confirmed",
                        source_ref=f"researcher:{decision.id}:{response.response_id}",
                        note=response.text,
                        value=resolve(version.design, op.path) if version else op.value,
                    )
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
