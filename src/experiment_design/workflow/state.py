"""Bound scientific state presented to the workflow judge."""

from experiment_design.config import RunConfig
from experiment_design.contracts import ReviewCohort
from experiment_design.persistence.store import Store


def jev_state(store: Store, run_id: str, cfg: RunConfig) -> dict:
    run = store.run(run_id)
    events = store.events(run_id)
    cohorts = store._artifact_records(run_id, "cohort", ReviewCohort)
    context, version = store.context(run_id), store.version(run_id)
    return {
        "objective": (
            "Explore, select and produce an independently reviewed Experiment Plan "
            "with explicit assumptions, limitations "
            "and downstream actions. Qualification assesses scientific plan quality, not "
            "successful downstream execution. Do not repeat work solely to close "
            "scientific uncertainties. Preserve STOP for technical failures, integrity problems "
            "or missing intent that prevents a meaningful draft."
        ),
        "binding": store.binding(run_id).model_dump(),
        "stage": run.stage_id,
        "research": {
            "question": context.package.research_question,
            "unknowns": context.unknowns,
        }
        if context
        else None,
        "draft": {
            "estimand": version.design.claim.estimand.model_dump(mode="json"),
            "model": version.design.analysis.model,
        }
        if version
        else None,
        "progress": {
            "reviewed_version": run.reviewed_version,
            "reviews_done": run.reviews_done,
            "max_reviews": cfg.max_reviews,
            "revisions_done": run.revisions_done,
            "max_revisions": cfg.max_revisions,
            "cohorts": [
                {
                    "id": c.id,
                    "binding": c.binding.model_dump(),
                    "published": c.published_event_id is not None,
                }
                for c in cohorts[-2:]
            ],
            "recent_actions": [
                {"point": e.data.get("point", e.data.get("scope")), "choice": e.data["choice"]}
                for e in events
                if e.type == "decision"
            ][-5:],
            "last_stop": next((e.data for e in reversed(events) if e.type == "budget.stop"), None),
        },
        "calculations": [
            {"scenario": c.scenario_id, "status": c.status, "reasons": c.reasons}
            for c in (
                store.publication_snapshot(run_id).calculations if store.version(run_id) else []
            )
        ],
        "issues": [
            {
                "id": i.id,
                "severity": i.severity,
                "status": i.status,
                "paths": i.paths,
                "summary": i.mechanism[:180],
                "consequence": i.consequence[:200],
                "basis": i.basis,
            }
            for i in store.issues(run_id)
            if i.status not in {"resolved", "duplicate"}
        ],
        "decisions": [
            {
                "id": d.id,
                "status": d.status,
                "verification": d.verification.get("status"),
                "problem": d.problem[:200],
            }
            for d in store.decisions(run_id)
            if d.purpose == "scientific"
            and (d.status != "APPLIED" or d.verification.get("status") != "verified")
        ],
        "budget": {
            "used": store.usage(run_id).llm_tokens,
            "limit": cfg.llm_tokens,
            "actions": run.actions_done,
            "max_actions": cfg.max_actions,
        },
    }
