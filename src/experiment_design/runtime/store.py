"""SQLite canonical records with atomic, append-only journal events."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from experiment_design.contracts import (
    AgentResult,
    AgentTask,
    Approval,
    ArtifactBinding,
    CalculationResult,
    Design,
    DesignVersion,
    Observation,
    Patch,
    PublicationSnapshot,
    ReadinessAssessment,
    Record,
    ResearchContext,
    ResearcherDecision,
    ResearcherRequest,
    ReviewCohort,
    ReviewIssue,
    RunStatus,
    ScientificPackage,
    SourceSnapshot,
    StageId,
    StageResult,
)
from experiment_design.patch import design_diff, design_hash

EVENT_TYPES = (
    "run.status",
    "task.started",
    "task.finished",
    "budget.stop",
    "tool.call",
    "decision",
    "design.version",
    "issue",
    "verification",
    "researcher.request",
    "approval",
    "artifact",
    "stage.result",
    "run.updated",
    "publication",
    "provider.usage",
    "provider.attempt",
    "budget.changed",
)


def now() -> str:
    return datetime.now(UTC).isoformat()


class Event(Record):
    event_id: int
    run_id: str
    timestamp: str
    type: str
    agent_id: str | None = None
    task_id: str | None = None
    design_version: int | None = None
    data: dict


class RunRecord(Record):
    run_id: str
    package: ScientificPackage | None = None
    schema_version: int = 1
    stage_id: StageId | None = None
    source_snapshot_id: str | None = None
    context_revision: int | None = None
    status: RunStatus
    config: dict
    reviewed_version: int = 0
    reviews_done: int = 0
    revisions_done: int = 0
    actions_done: int = 0
    created_at: str


class Usage(Record):
    llm_tokens: int
    llm_calls: int
    jev_calls: int
    actions: int
    llm_cached_tokens_reported: int = 0
    llm_cache_usage_missing_calls: int = 0
    llm_usage_missing_calls: int = 0
    jev_tokens_reported: int = 0
    jev_usage_missing_calls: int = 0


class TaskRecord(Record):
    task: AgentTask
    run_id: str = ""
    attempt: int = 1
    messages: list[dict] = []
    schema_hash: str = ""
    observations: list[str] = []
    submission: AgentResult | None = None
    status: Literal["running", "completed", "interrupted", "failed", "stopped"] = "running"
    usage: dict = {}


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._depth = 0
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
                run_id TEXT, id INTEGER, data TEXT NOT NULL, PRIMARY KEY(run_id, id));
            CREATE TABLE IF NOT EXISTS issues (
                run_id TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(run_id, id));
            CREATE TABLE IF NOT EXISTS requests (
                run_id TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(run_id, id));
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL, type TEXT NOT NULL, agent_id TEXT, task_id TEXT,
                design_version INTEGER, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
                run_id TEXT, kind TEXT, id TEXT, data TEXT NOT NULL,
                PRIMARY KEY(run_id, kind, id));
        """)

    def close(self) -> None:
        self.connection.close()

    def backup(self, target: Path) -> None:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as connection:
            self.connection.backup(connection)

    def record_publication(self, run_id: str, binding: ArtifactBinding, manifest: dict) -> None:
        with self.transaction():
            previous = [
                e
                for e in self.events(run_id)
                if e.type == "publication" and e.data["manifest"] == manifest
            ]
            if previous:
                return
            self.append_event(
                run_id, "publication", {"binding": binding.model_dump(), "manifest": manifest}
            )
            run = self.run(run_id)
            if (
                run.stage_id == "S7"
                and run.status == "APPROVED"
                and binding.design_hash == self.version(run_id).design_hash
            ):
                from experiment_design.qualification import can_approve

                current_snapshot = self.publication_snapshot(run_id)
                if (
                    binding.context_revision != current_snapshot.binding.context_revision
                    or current_snapshot.approval is None
                    or can_approve(current_snapshot)
                ):
                    raise ValueError("publication approval or supporting records changed")
                self.commit_stage(
                    run_id,
                    StageResult(
                        stage_id="S7",
                        status="COMPLETE",
                        binding=self.binding(run_id),
                        output_refs=[manifest["support_hash"]],
                        reason="Consistent EDP bundle published",
                    ),
                    "S7",
                )
                self.update_run(run_id, status="PUBLISHED")

    @contextmanager
    def transaction(self):
        depth = self._depth
        self.connection.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT tx{depth}")
        self._depth += 1
        try:
            yield
        except BaseException:
            if depth == 0:
                self.connection.execute("ROLLBACK")
            else:
                self.connection.execute(f"ROLLBACK TO tx{depth}")
                self.connection.execute(f"RELEASE tx{depth}")
            raise
        else:
            self.connection.execute("COMMIT" if depth == 0 else f"RELEASE tx{depth}")
        finally:
            self._depth -= 1

    def create_run(self, package: ScientificPackage, config: dict) -> str:
        run_id = "r" + uuid4().hex[:8]
        record = RunRecord(
            run_id=run_id,
            package=package,
            config=config,
            status="RUNNING",
            created_at=now(),
        )
        with self.transaction():
            self.connection.execute(
                "INSERT INTO runs VALUES (?, ?)", (run_id, record.model_dump_json())
            )
            self.append_event(run_id, "run.status", {"status": "RUNNING"})
        return run_id

    def run(self, run_id: str) -> RunRecord:
        row = self.connection.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run {run_id}")
        return RunRecord.model_validate_json(row["data"])

    def update_run(self, run_id: str, **fields) -> None:
        with self.transaction():
            old = self.run(run_id)
            record = RunRecord.model_validate(old.model_dump() | fields)
            self.connection.execute(
                "UPDATE runs SET data=? WHERE run_id=?", (record.model_dump_json(), run_id)
            )
            if old.status != record.status:
                self.append_event(run_id, "run.status", {"status": record.status})
            if record.schema_version == 2:
                self.append_event(run_id, "run.updated", record.model_dump(mode="json"))

    def append_event(
        self,
        run_id: str,
        type: str,
        data: dict,
        *,
        agent_id=None,
        task_id=None,
        design_version=None,
    ) -> int:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type}")
        with self.transaction():
            self.run(run_id)
            cursor = self.connection.execute(
                "INSERT INTO events (run_id,timestamp,type,agent_id,task_id,design_version,data)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    now(),
                    type,
                    agent_id,
                    task_id,
                    design_version,
                    json.dumps(data, ensure_ascii=False, allow_nan=False),
                ),
            )
        return cursor.lastrowid

    def events(self, run_id: str, after_id: int = 0) -> list[Event]:
        self.run(run_id)
        rows = self.connection.execute(
            "SELECT * FROM events WHERE run_id=? AND event_id>? ORDER BY event_id",
            (run_id, after_id),
        ).fetchall()
        return [Event.model_validate(dict(row) | {"data": json.loads(row["data"])}) for row in rows]

    def add_version(
        self,
        run_id: str,
        design: Design,
        *,
        author: str,
        parent: int | None,
        patch: Patch | None = None,
    ) -> DesignVersion:
        with self.transaction():
            self.run(run_id)
            previous = self.version(run_id)
            if self.run(run_id).schema_version == 2 and parent != (
                previous.version if previous else None
            ):
                raise ValueError("stale version parent")
            version = DesignVersion(
                run_id=run_id,
                version=previous.version + 1 if previous else 1,
                design=design,
                parent=parent,
                author=author,
                patch=patch,
                design_hash=design_hash(design),
                created_at=now(),
            )
            self.connection.execute(
                "INSERT INTO versions VALUES (?,?,?)",
                (run_id, version.version, version.model_dump_json()),
            )
            paths = [d["path"] for d in design_diff(previous.design, design)] if previous else ["/"]
            self.append_event(
                run_id,
                "design.version",
                {
                    "version": version.version,
                    "author": author,
                    "parent": parent,
                    "design_hash": version.design_hash,
                    "diff_paths": paths,
                    "record": version.model_dump(mode="json"),
                },
                design_version=version.version,
            )
        return version

    def version(self, run_id: str, n: int | None = None) -> DesignVersion | None:
        self.run(run_id)
        if n is None:
            row = self.connection.execute(
                "SELECT data FROM versions WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT data FROM versions WHERE run_id=? AND id=?",
                (run_id, n),
            ).fetchone()
        return DesignVersion.model_validate_json(row["data"]) if row else None

    def _save(self, table, record, prefix):
        self.run(record.run_id)
        if not record.id:
            count = self.connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id=?",
                (record.run_id,),
            ).fetchone()[0]
            record = record.model_copy(update={"id": f"{prefix}{count + 1}"})
        else:
            exists = self.connection.execute(
                f"SELECT 1 FROM {table} WHERE run_id=? AND id=?",
                (record.run_id, record.id),
            ).fetchone()
            if not exists:
                raise KeyError(f"unknown {table} id {record.id}")
        self.connection.execute(
            f"INSERT INTO {table} VALUES (?,?,?) "
            "ON CONFLICT(run_id,id) DO UPDATE SET data=excluded.data",
            (record.run_id, record.id, record.model_dump_json()),
        )
        return record

    def _list(self, table, model, run_id):
        self.run(run_id)
        rows = self.connection.execute(
            f"SELECT data FROM {table} WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [model.model_validate_json(row["data"]) for row in rows]

    def save_issue(self, issue: ReviewIssue) -> ReviewIssue:
        with self.transaction():
            issue = self._save("issues", issue, "I")
            self.append_event(
                issue.run_id,
                "issue",
                {
                    "id": issue.id,
                    "status": issue.status,
                    "severity": issue.severity,
                    "raised_by": issue.raised_by,
                    "record": issue.model_dump(mode="json"),
                },
                design_version=issue.version_raised,
            )
        return issue

    def issues(self, run_id: str) -> list[ReviewIssue]:
        return self._list("issues", ReviewIssue, run_id)

    def save_request(self, req: ResearcherRequest) -> ResearcherRequest:
        with self.transaction():
            req = self._save("requests", req, "R")
            self.append_event(req.run_id, "researcher.request", req.model_dump(mode="json"))
        return req

    def requests(self, run_id: str) -> list[ResearcherRequest]:
        return self._list("requests", ResearcherRequest, run_id)

    def add_approval(self, a: Approval) -> None:
        with self.transaction():
            self.run(a.run_id)
            self.connection.execute(
                "INSERT INTO approvals(run_id,data) VALUES (?,?)", (a.run_id, a.model_dump_json())
            )
            self.append_event(
                a.run_id, "approval", a.model_dump(mode="json"), design_version=a.version
            )

    def approvals(self, run_id: str) -> list[Approval]:
        return self._list("approvals", Approval, run_id)

    def usage(self, run_id: str) -> Usage:
        events = self.events(run_id)
        routing = [
            e.data for e in events if e.type == "provider.usage" and e.data.get("provider") == "jev"
        ]
        tasks = [
            e.data
            for e in events
            if (e.type == "provider.usage" and e.data.get("provider") != "jev")
            or (e.type == "task.finished" and not e.data.get("usage_accounted"))
        ]
        attempts = sum(
            e.type == "provider.attempt" and e.data.get("provider") == "llm" for e in events
        )
        reported_calls = sum(t.get("llm_calls", 0) for t in tasks)
        missing = max(0, attempts - reported_calls)
        return Usage(
            llm_tokens=sum(t.get("tokens_in", 0) + t.get("tokens_out", 0) for t in tasks)
            + sum(t["tokens"] or 0 for t in routing),
            llm_calls=reported_calls + missing,
            jev_calls=len(routing)
            if routing
            else sum(e.type == "decision" and e.data.get("by") == "jev" for e in events),
            actions=self.run(run_id).actions_done,
            llm_cached_tokens_reported=sum(t.get("tokens_cached", 0) for t in tasks),
            llm_cache_usage_missing_calls=sum(
                t.get("cache_usage_missing_calls", t.get("llm_calls", 0)) for t in tasks
            )
            + missing,
            llm_usage_missing_calls=missing,
            jev_tokens_reported=sum(t["tokens"] or 0 for t in routing),
            jev_usage_missing_calls=sum(t["tokens"] is None for t in routing),
        )

    def _put_artifact(self, run_id: str, kind: str, id: str, record: Record) -> Record:
        with self.transaction():
            self.run(run_id)
            self.connection.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?) ON CONFLICT(run_id,kind,id) "
                "DO UPDATE SET data=excluded.data",
                (run_id, kind, id, record.model_dump_json()),
            )
            self.append_event(
                run_id,
                "artifact",
                {"kind": kind, "id": id, "record": record.model_dump(mode="json")},
            )
        return record

    def _artifact_records(self, run_id: str, kind: str, model, cutoff: int | None = None):
        records = {}
        for event in self.events(run_id):
            if cutoff is not None and event.event_id > cutoff:
                break
            if event.type == "artifact" and event.data["kind"] == kind:
                records[event.data["id"]] = model.model_validate(event.data["record"])
        return list(records.values())

    def _artifact(self, run_id: str, kind: str, id: str, model, revision: int | None = None):
        matches = [
            e.data["record"]
            for e in self.events(run_id)
            if e.type == "artifact"
            and e.data["kind"] == kind
            and e.data["id"] == id
            and (revision is None or e.data["record"].get("revision") == revision)
        ]
        if not matches:
            raise KeyError(f"unknown {kind} {id}")
        return model.model_validate(matches[-1])

    def create_staged_run(self, snapshot: SourceSnapshot, config: dict) -> str:
        run_id = "r" + uuid4().hex[:8]
        record = RunRecord(
            run_id=run_id,
            config=config,
            status="RUNNING",
            created_at=now(),
            schema_version=2,
            stage_id="S1",
            source_snapshot_id=snapshot.id,
        )
        with self.transaction():
            self.connection.execute(
                "INSERT INTO runs VALUES (?,?)", (run_id, record.model_dump_json())
            )
            self.append_event(run_id, "run.updated", record.model_dump(mode="json"))
            self._put_artifact(run_id, "source", snapshot.id, snapshot)
        return run_id

    def source_snapshot(self, run_id: str) -> SourceSnapshot:
        return self._artifact(run_id, "source", self.run(run_id).source_snapshot_id, SourceSnapshot)

    def save_context(self, run_id: str, context: ResearchContext) -> ResearchContext:
        with self.transaction():
            run = self.run(run_id)
            previous = self.context(run_id)
            if context.source_snapshot_id != run.source_snapshot_id:
                raise ValueError("context source binding differs")
            if context.revision != (previous.revision + 1 if previous else 1):
                raise ValueError("context revision must advance exactly once")
            self._put_artifact(run_id, "context", context.id, context)
            self.update_run(
                run_id, context_revision=context.revision, package=context.package.model_dump()
            )
        return context

    def context(self, run_id: str, revision: int | None = None) -> ResearchContext | None:
        records = [
            e.data["record"]
            for e in self.events(run_id)
            if e.type == "artifact"
            and e.data["kind"] == "context"
            and (revision is None or e.data["record"]["revision"] == revision)
        ]
        return ResearchContext.model_validate(records[-1]) if records else None

    def binding(self, run_id: str) -> ArtifactBinding:
        run = self.run(run_id)
        version = self.version(run_id)
        return ArtifactBinding(
            source_snapshot_id=run.source_snapshot_id,
            context_revision=run.context_revision,
            design_version=version.version if version else None,
            design_hash=version.design_hash if version else None,
            event_id=self.events(run_id)[-1].event_id,
        )

    def check_binding(self, run_id: str, binding: ArtifactBinding) -> None:
        if not any(e.event_id == binding.event_id for e in self.events(run_id)):
            raise ValueError("unknown artifact binding checkpoint")
        current = self.binding(run_id)
        if binding.model_dump(exclude={"event_id"}) != current.model_dump(exclude={"event_id"}):
            raise ValueError("stale artifact binding")

    def commit_stage(self, run_id: str, result: StageResult, target: StageId) -> None:
        from experiment_design.stages import stage_specs

        with self.transaction():
            self.check_binding(run_id, result.binding)
            if self.run(run_id).stage_id != result.stage_id:
                raise ValueError("stage binding differs")
            stages = list(stage_specs())
            is_return = stages.index(target) < stages.index(result.stage_id)
            if is_return:
                if target not in stage_specs()[result.stage_id].permitted_return_targets:
                    raise ValueError("unpermitted return target")
                if not result.reason.strip() or not (result.issue_refs or result.output_refs):
                    raise ValueError("return requires originating issue or decision")
                known = {i.id for i in self.issues(run_id)} | {d.id for d in self.decisions(run_id)}
                if not any(ref in known for ref in result.issue_refs + result.output_refs):
                    raise ValueError("return requires an existing originating issue or decision")
            elif target != result.stage_id:
                if (
                    stages.index(target) != stages.index(result.stage_id) + 1
                    or result.status != "COMPLETE"
                ):
                    raise ValueError("unpermitted stage target")
            self.append_event(
                run_id,
                "stage.result",
                {
                    **result.model_dump(mode="json"),
                    "target": target,
                    "originating_ref": (result.issue_refs + result.output_refs or [None])[0],
                },
            )
            self.update_run(run_id, stage_id=target)

    def save_decision(self, decision: ResearcherDecision) -> ResearcherDecision:
        try:
            previous = self.decision(decision.run_id, decision.id)
        except KeyError:
            previous = None
        if previous:
            if decision.revision not in {previous.revision, previous.revision + 1}:
                raise ValueError("decision revision must be current or next")
            frozen = {
                "binding",
                "problem",
                "affected_paths",
                "dependencies",
                "options",
                "recommendation",
                "purpose",
            }
            if (
                previous.status not in {"IDENTIFIED", "EXPLORING"}
                and decision.revision == previous.revision
                and previous.model_dump(include=frozen) != decision.model_dump(include=frozen)
            ):
                raise ValueError("presented decision is immutable within its revision")
            if decision.responses[: len(previous.responses)] != previous.responses:
                raise ValueError("researcher response history is immutable")
        return self._put_artifact(decision.run_id, "researcher_decision", decision.id, decision)

    def decision(
        self, run_id: str, decision_id: str, revision: int | None = None
    ) -> ResearcherDecision:
        return self._artifact(
            run_id, "researcher_decision", decision_id, ResearcherDecision, revision
        )

    def decisions(self, run_id: str) -> list[ResearcherDecision]:
        return self._artifact_records(run_id, "researcher_decision", ResearcherDecision)

    def save_cohort(self, cohort: ReviewCohort) -> ReviewCohort:
        try:
            previous = self.cohort(cohort.run_id, cohort.id)
        except KeyError:
            previous = None
        if previous and (
            previous.binding != cohort.binding
            or previous.member_task_ids != cohort.member_task_ids
            or previous.published_event_id is not None
            and previous != cohort
        ):
            raise ValueError("review cohort is immutable")
        return self._put_artifact(cohort.run_id, "cohort", cohort.id, cohort)

    def cohort(self, run_id: str, cohort_id: str) -> ReviewCohort:
        return self._artifact(run_id, "cohort", cohort_id, ReviewCohort)

    def save_assessment(self, assessment: ReadinessAssessment) -> ReadinessAssessment:
        with self.transaction():
            self._put_artifact(assessment.run_id, "assessment", assessment.id, assessment)
            self.freeze_publication(assessment.run_id)
        return assessment

    def freeze_publication(self, run_id: str) -> PublicationSnapshot:
        snapshot = self.publication_snapshot(run_id)
        return self._put_artifact(
            run_id,
            "publication_snapshot",
            f"version:{snapshot.version.version}:event:{snapshot.binding.event_id}",
            snapshot,
        )

    def assessment(
        self, run_id: str, assessment_id: str | None = None
    ) -> ReadinessAssessment | None:
        rows = self._artifact_records(run_id, "assessment", ReadinessAssessment)
        return next(
            (r for r in reversed(rows) if assessment_id is None or r.id == assessment_id), None
        )

    def save_observation(self, run_id: str, observation: Observation) -> Observation:
        try:
            previous = self.observation(run_id, observation.id)
        except KeyError:
            return self._put_artifact(run_id, "observation", observation.id, observation)
        if previous != observation:
            raise ValueError("observation is immutable")
        return previous

    def observation(self, run_id: str, observation_id: str) -> Observation:
        return self._artifact(run_id, "observation", observation_id, Observation)

    def save_task(self, run_id: str, record: TaskRecord) -> TaskRecord:
        record = record.model_copy(update={"run_id": run_id})
        return self._put_artifact(run_id, "task", record.task.id, record)

    def task(self, task_id: str) -> TaskRecord:
        row = self.connection.execute(
            "SELECT run_id FROM artifacts WHERE kind='task' AND id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown task {task_id}")
        return self._artifact(row["run_id"], "task", task_id, TaskRecord)

    def checkpoint_task(self, record: TaskRecord) -> None:
        self.save_task(record.run_id, record)

    def accepted_observations(self, task_id: str) -> list[Observation]:
        record = self.task(task_id)
        return [
            o
            for o in self._artifact_records(record.run_id, "observation", Observation)
            if o.task_id == task_id and o.accepted
        ]

    def record_submission(self, task_id: str, result: AgentResult) -> None:
        with self.transaction():
            record = self.task(task_id)
            if record.submission is not None and record.submission != result:
                raise ValueError("task submission is immutable")
            if result.completion_status != "completed":
                raise ValueError("only a completed submission may commit")
            self.checkpoint_task(
                record.model_copy(update={"submission": result, "status": "completed"})
            )

    def publication_snapshot(
        self, run_id: str, version: int | None = None, event_id: int | None = None
    ) -> PublicationSnapshot:
        with self.transaction():
            events = self.events(run_id)
            cutoff = event_id if event_id is not None else events[-1].event_id
            if version is not None:
                frozen = [
                    s
                    for s in self._artifact_records(
                        run_id, "publication_snapshot", PublicationSnapshot, cutoff
                    )
                    if s.version.version == version
                ]
                if frozen:
                    return frozen[-1]
                later = [
                    e.event_id
                    for e in events
                    if e.type == "design.version" and e.design_version > version
                ]
                if later:
                    cutoff = min(cutoff, later[0] - 1)
            history = [e for e in events if e.event_id <= cutoff]
            versions = [
                e
                for e in history
                if e.type == "design.version" and (version is None or e.design_version == version)
            ]
            if not versions:
                raise ValueError("publication requires a design version")
            selected = self.version(run_id, versions[-1].design_version)
            runs = [e.data for e in history if e.type == "run.updated"]
            run = runs[-1] if runs else self.run(run_id).model_dump(mode="json")
            contexts = self._artifact_records(run_id, "context", ResearchContext, cutoff)
            context = contexts[-1] if contexts else None
            binding = ArtifactBinding(
                source_snapshot_id=run["source_snapshot_id"],
                context_revision=context.revision if context else None,
                design_version=selected.version,
                design_hash=selected.design_hash,
                event_id=cutoff,
            )
            issues = {}
            for e in history:
                if e.type == "issue" and "record" in e.data:
                    issues[e.data["id"]] = ReviewIssue.model_validate(e.data["record"])
            assessments = self._artifact_records(run_id, "assessment", ReadinessAssessment, cutoff)
            material_events = [
                e.event_id
                for e in history
                if e.type in {"design.version", "issue"}
                or (
                    e.type == "artifact"
                    and (
                        e.data["kind"] == "context"
                        or (
                            e.data["kind"] == "researcher_decision"
                            and e.data["record"]["purpose"] != "final_review"
                        )
                    )
                )
            ]
            last_material = max(material_events, default=0)
            assessment_events = [
                e.event_id
                for e in history
                if e.type == "artifact" and e.data["kind"] == "assessment"
            ]
            if assessment_events and last_material > assessment_events[-1]:
                assessments = []
            approvals = [
                Approval.model_validate(e.data)
                for e in history
                if e.type == "approval"
                and e.data["design_hash"] == selected.design_hash
                and e.event_id > last_material
            ]
            observations = self._artifact_records(run_id, "observation", Observation, cutoff)
            calculations = [
                CalculationResult.model_validate(item)
                for o in observations
                if o.accepted
                and o.tool_name == "compute_power"
                and o.binding.design_hash == selected.design_hash
                and o.binding.context_revision == binding.context_revision
                and o.binding.source_snapshot_id == binding.source_snapshot_id
                and not o.arguments.get("overrides")
                and not o.arguments.get("ops")
                and not o.arguments.get("design")
                for item in o.result.get("value", [])
                if "request_hash" in item
            ]
            return PublicationSnapshot(
                run=run,
                context=context,
                version=selected,
                issues=list(issues.values()),
                decisions=self._artifact_records(
                    run_id, "researcher_decision", ResearcherDecision, cutoff
                ),
                assessment=assessments[-1] if assessments else None,
                approval=approvals[-1] if approvals else None,
                binding=binding,
                calculations=calculations,
            )
