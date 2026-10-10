"""Immutable independent review cohorts and coordinated scientific refinement."""

from uuid import uuid4

from experiment_design.agents.tools import ToolContext
from experiment_design.contracts import AgentTask, ArtifactBinding, ReviewCohort, ReviewIssue
from experiment_design.package import all_sources
from experiment_design.runtime.store import Store


def commit_revision(
    store: Store,
    run_id: str,
    *,
    parent: int,
    patch,
    issue_ids: list[str],
    decision_id: str | None = None,
):
    from experiment_design.patch import apply_patch

    with store.transaction():
        version = store.version(run_id)
        if version is None or version.version != parent:
            raise ValueError("stale patch parent")
        context = store.context(run_id)
        changed = apply_patch(
            version.design,
            patch,
            context=context,
            source_ids={s.id for s in all_sources(context, store.decisions(run_id))},
        )
        version = store.add_version(run_id, changed, author="design", parent=parent, patch=patch)
        known = {i.id: i for i in store.issues(run_id)}
        for id in issue_ids:
            if id not in known:
                raise ValueError("unknown addressed issue")
            issue = known[id]
            issue.status = "addressed"
            issue.addressed_in = version.version
            store.save_issue(issue)
        return version


def verification_options(
    issue: ReviewIssue, verifier_verdict: str, findings: list, evidence_refs: list[str]
) -> list[str]:
    options = ["unresolved", "open"]
    relevant = [
        f
        for f in findings
        if getattr(f, "severity", "MAJOR") in {"MAJOR", "BLOCKER"}
        and any(
            getattr(f, "path", "/").startswith(p) or p.startswith(getattr(f, "path", "/"))
            for p in issue.paths
        )
    ]
    if verifier_verdict == "resolved" and evidence_refs and not relevant:
        options.insert(0, "resolved")
    return options


def review_actions(store: Store, run_id: str, cfg) -> list[str]:
    run = store.run(run_id)
    issues = store.issues(run_id)
    actions = []
    if (
        run.reviewed_version < (store.version(run_id).version if store.version(run_id) else 0)
        and run.reviews_done < cfg.max_reviews
    ):
        actions.append("REVIEW")
    if any(i.status in {"addressed", "rebutted"} for i in issues):
        actions.append("VERIFY")
    if (
        any(i.status == "open" and i.severity in {"MAJOR", "BLOCKER"} for i in issues)
        and run.revisions_done < cfg.max_revisions
    ):
        actions.append("REVISE")
    if any(
        d.status in {"IDENTIFIED", "EXPLORING"} and (d.options or d.dependencies)
        for d in store.decisions(run_id)
    ):
        actions.append("ASK_RESEARCHER")
    if run.reviewed_version and not any(
        i.severity in {"MAJOR", "BLOCKER"} and i.status not in {"resolved", "duplicate"}
        for i in issues
    ):
        actions.append("QUALIFY")
    actions.append("STOP")
    return actions


def start_cohort(store: Store, run_id: str, binding: ArtifactBinding) -> ReviewCohort:
    store.check_binding(run_id, binding)
    cohort_id = "cohort:" + uuid4().hex
    cohort = ReviewCohort(
        id=cohort_id,
        run_id=run_id,
        binding=binding,
        member_task_ids={role: "task:" + uuid4().hex for role in ("methodologist", "skeptic")},
    )
    return store.save_cohort(cohort)


def review_context(store: Store, run_id: str, task: AgentTask) -> ToolContext:
    cohort = store.cohort(run_id, task.cohort_id)
    if task.id not in cohort.member_task_ids.values() or task.binding != cohort.binding:
        raise ValueError("review task cohort binding differs")
    context = store.context(run_id, cohort.binding.context_revision)
    return ToolContext(
        store,
        run_id,
        context.package,
        store.version(run_id, cohort.binding.design_version),
        set(),
        binding=cohort.binding,
        issue_cutoff=cohort.binding.event_id,
        task_id=task.id,
        cohort_id=cohort.id,
    )


def publish_cohort(store: Store, run_id: str, cohort_id: str) -> list[ReviewIssue]:
    with store.transaction():
        cohort = store.cohort(run_id, cohort_id)
        if cohort.published_event_id is not None:
            return [i for i in store.issues(run_id) if i.id in cohort.submission_refs]
        store.check_binding(run_id, cohort.binding)
        records = []
        for role, task_id in cohort.member_task_ids.items():
            try:
                record = store.task(task_id)
            except KeyError as exc:
                raise ValueError("both reviewer submissions are required") from exc
            if (
                record.submission is None
                or record.status != "completed"
                or record.task.binding != cohort.binding
                or record.task.cohort_id != cohort.id
            ):
                raise ValueError("valid bound reviewer submission is required")
            records.append((role, record))
        published = []
        for role, record in records:
            for finding in record.submission.findings_or_changes.get("findings", []):
                issue = store.save_issue(
                    ReviewIssue(
                        **finding,
                        run_id=run_id,
                        raised_by=role,
                        version_raised=cohort.binding.design_version,
                    )
                )
                published.append(issue)
        cohort.submission_refs = [i.id for i in published]
        cohort.published_event_id = store.events(run_id)[-1].event_id + 1
        store.save_cohort(cohort)
        return published
