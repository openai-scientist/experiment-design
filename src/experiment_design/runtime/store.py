"""SQLite canonical records with atomic, append-only journal events."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from experiment_design.contracts import (
    Approval,
    Design,
    DesignVersion,
    Patch,
    Record,
    ResearcherRequest,
    ReviewIssue,
    RunStatus,
    ScientificPackage,
)
from experiment_design.patch import design_diff, design_hash

EVENT_TYPES = (
    "run.status",
    "task.started",
    "task.finished",
    "tool.call",
    "decision",
    "design.version",
    "issue",
    "verification",
    "researcher.request",
    "approval",
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
    package: ScientificPackage
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
        """)

    def close(self) -> None:
        self.connection.close()

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
        tasks = [e.data for e in events if e.type == "task.finished"]
        return Usage(
            llm_tokens=sum(t.get("tokens_in", 0) + t.get("tokens_out", 0) for t in tasks),
            llm_calls=sum(t.get("llm_calls", 0) for t in tasks),
            jev_calls=sum(e.type == "decision" and e.data.get("by") == "jev" for e in events),
            actions=self.run(run_id).actions_done,
        )
