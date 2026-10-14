"""Atomic filesystem checkpoints and checksum-bound stage manifests."""

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import ValidationError

from experiment_design.contracts import RunState, WorkflowError
from experiment_design.vocabulary import ACTIVITY_TYPES


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_REPLACE_TRIES = 5


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_TRIES):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                # Windows refuses to replace a file another process is reading.
                if attempt == _REPLACE_TRIES - 1:
                    raise WorkflowError(
                        f"Cannot update {path.name}: another process holds it open; "
                        "close any reader (for example exd status) and retry"
                    ) from exc
                time.sleep(0.05 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)


def _json(path: Path, data: dict) -> None:
    _atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


# A store change: ('event', kind, data) after the journal write, or ('state', old, new)
# after the checkpoint write.
StoreChange = tuple[Literal["event"], str, dict] | tuple[Literal["state"], RunState, RunState]


class RunStore:
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir).resolve()
        self._executing = False
        self._listeners: list[Callable[[StoreChange], None]] = []
        self._control_lock = threading.Lock()
        self._coalescing = 0
        self._pending_state: tuple[RunState, RunState] | None = None
        if self.path.is_file():
            raise WorkflowError(
                "Legacy SQLite runs are unsupported; supply a filesystem run directory"
            )
        try:
            self._load_state()
            if (self.path / "archive-pending.json").exists():
                with self.execution():
                    pass
        except (OSError, ValueError, ValidationError) as exc:
            raise WorkflowError(f"Invalid filesystem run {self.path}: {exc}") from exc

    def _load_state(self) -> None:
        self.state = RunState.model_validate_json((self.path / "checkpoint.json").read_bytes())

    @classmethod
    def create(
        cls, root: Path, inputs: list[Path], config_snapshot: dict, prompts_snapshot: dict
    ) -> "RunStore":
        if not inputs or any(not Path(path).is_file() for path in inputs):
            raise WorkflowError("Supply at least one existing Markdown input file")
        # Read before creating a run, so an input failure cannot look like completed intake.
        originals = [(Path(path).resolve(), Path(path).read_bytes()) for path in inputs]
        for original, data in originals:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowError(f"Input must be UTF-8 text: {original}") from exc
        run_id = f"exd-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
        path = Path(root).resolve() / run_id
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise WorkflowError(f"Run directory collision: {path}") from exc
        _json(path / "checkpoint.json", RunState(run_id=run_id).model_dump(mode="json"))
        _json(path / "config.json", config_snapshot)
        _json(path / "prompts.json", prompts_snapshot)
        sources = []
        for index, (original, data) in enumerate(originals):
            relative = (
                "stage-01/hypothesis.md" if index == 0 else f"sources/{index:03d}-{original.name}"
            )
            _atomic(path / relative, data)
            sources.append(
                {"path": relative, "original": str(original), "sha256": _hash(path / relative)}
            )
        _json(path / "sources.json", {"sources": sources})
        store = cls(path)
        store.complete(1, [], ["hypothesis.md"])
        return store

    def subscribe(self, listener: Callable[[StoreChange], None]) -> None:
        """Listener errors propagate: a failed projection must not look like success."""
        self._listeners.append(listener)

    def _notify(self, change: StoreChange) -> None:
        if change[0] == "state" and self._coalescing:
            first = self._pending_state[0] if self._pending_state else change[1]
            self._pending_state = (first, change[2])
            return
        for listener in list(self._listeners):
            listener(change)

    @contextmanager
    def coalesced(self):
        """Report the state changes of one logical step as a single change."""
        self._coalescing += 1
        try:
            yield
        finally:
            self._coalescing -= 1
            if not self._coalescing and self._pending_state:
                (previous, current), self._pending_state = self._pending_state, None
                self._notify(("state", previous, current))

    def update(self, **fields) -> RunState:
        state = RunState.model_validate({**self.state.model_dump(), **fields})
        _json(self.path / "checkpoint.json", state.model_dump(mode="json"))
        previous, self.state = self.state, state
        self._notify(("state", previous, state))
        return state

    def config(self) -> dict:
        return json.loads((self.path / "config.json").read_text(encoding="utf-8"))

    def bound_findings(self, stage: int, name: str, round: int) -> list[dict]:
        """Assign persistent IDs to records bound to exact review bytes and their round."""
        text = self.read(stage, name)
        source = f"{round}:{stage}/{name}:" + hashlib.sha256(text.encode()).hexdigest()
        known = {
            e["data"]["binding"]: e["data"]["finding_id"]
            for e in self.events()
            if e["kind"] == "finding.mapped"
        }
        result = []
        for i, finding in enumerate(json.loads(text).get("findings", [])):
            binding = f"{source}:{i}"
            if binding not in known:
                count = sum(v.startswith(f"F{round}-") for v in known.values())
                known[binding] = f"F{round}-{count + 1}"
                self.event(
                    "finding.mapped",
                    {
                        "binding": binding,
                        "finding_id": known[binding],
                        "raw_id": finding["id"],
                        "round": round,
                    },
                )
            result.append({**finding, "finding_id": known[binding]})
        return result

    def set_run_limits(self, limits: dict) -> None:
        config = self.config()
        config["run"] = {**(config.get("run") or {}), **limits}
        _json(self.path / "config.json", config)

    def _control(self) -> dict:
        try:
            control = json.loads((self.path / "control.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return control if isinstance(control, dict) else {}

    def _set_control(self, **fields) -> None:
        with self._control_lock:
            control = {**self._control(), **fields}
            _json(self.path / "control.json", {k: v for k, v in control.items() if v is not None})

    def request_interrupt(self, kind: Literal["cancel", "pause"]) -> None:
        if kind not in {"cancel", "pause"}:
            raise WorkflowError("Unknown interrupt kind")
        self._set_control(interrupt=kind)

    def clear_interrupt(self) -> None:
        self._set_control(interrupt=None)

    def interrupt(self) -> str | None:
        kind = self._control().get("interrupt")
        return kind if kind in {"cancel", "pause"} else None

    def remember_status(self, status: str | None) -> None:
        """The status a paused run returns to on resume."""
        self._set_control(resume_status=status)

    def remembered_status(self) -> str | None:
        status = self._control().get("resume_status")
        return status if isinstance(status, str) else None

    def _artifact(self, stage: int, name: str) -> Path:
        if not isinstance(stage, int) or isinstance(stage, bool) or not 1 <= stage <= 7:
            raise WorkflowError("Stage must be an integer from 1 to 7")
        if (
            not name
            or name in {".", ".."}
            or name.rstrip(" .") != name
            or PureWindowsPath(name).is_reserved()
            or any(c in name for c in '/\\:<>"|?*')
            or any(ord(c) < 32 for c in name)
        ):
            raise WorkflowError(f"Unsafe artifact name {name!r}")
        folder = self.path / f"stage-{stage:02d}"
        target = folder / name
        if (
            not target.resolve().is_relative_to(self.path)
            or folder.is_symlink()
            or target.is_symlink()
        ):
            raise WorkflowError("Artifact path escapes run directory")
        return target

    def read(self, stage: int, name: str) -> str:
        return self._artifact(stage, name).read_bytes().decode("utf-8")

    def write(self, stage: int, name: str, text: str) -> Path:
        path = self._artifact(stage, name)
        if name.casefold() == "manifest.json":
            raise WorkflowError("Stage manifests are written only by complete()")
        _atomic(path, text.encode("utf-8"))
        return path

    def complete(self, stage: int, inputs: list[tuple[int, str]], outputs: list[str]) -> None:
        if not outputs or len(set(outputs)) != len(outputs) or len(set(inputs)) != len(inputs):
            raise WorkflowError("Stage completion requires unique outputs and inputs")
        dependencies = []
        for source_stage, name in inputs:
            if source_stage >= stage or not self.valid(source_stage):
                raise WorkflowError("Stage dependency must be an earlier valid stage")
            source_manifest = json.loads(self._artifact(source_stage, "manifest.json").read_bytes())
            if name not in source_manifest["outputs"]:
                raise WorkflowError("Stage dependency must be a declared output")
            artifact = self._artifact(source_stage, name)
            if not artifact.is_file():
                raise WorkflowError(f"Missing dependency {source_stage}/{name}")
            dependencies.append({"stage": source_stage, "name": name, "sha256": _hash(artifact)})
        checksums = {}
        for name in outputs:
            artifact = self._artifact(stage, name)
            if name.casefold() == "manifest.json" or not artifact.is_file():
                raise WorkflowError(f"Missing or invalid output {stage}/{name}")
            checksums[name] = _hash(artifact)
        manifest = {
            "stage": stage,
            "attempt": self.state.attempt,
            "inputs": dependencies,
            "outputs": checksums,
        }
        if stage == 1:
            manifest["source_index_hash"] = _hash(self.path / "sources.json")
            manifest["sources"] = json.loads((self.path / "sources.json").read_bytes())["sources"]
        _json(self._artifact(stage, "manifest.json"), manifest)

    def valid(self, stage: int) -> bool:
        try:
            manifest = json.loads(self._artifact(stage, "manifest.json").read_bytes())
            if manifest["stage"] != stage or not manifest["outputs"]:
                return False
            for name, digest in manifest["outputs"].items():
                if _hash(self._artifact(stage, name)) != digest:
                    return False
            for dependency in manifest["inputs"]:
                source_stage = dependency["stage"]
                if source_stage >= stage or not self.valid(source_stage):
                    return False
                source_manifest = json.loads(
                    self._artifact(source_stage, "manifest.json").read_bytes()
                )
                if dependency["name"] not in source_manifest["outputs"]:
                    return False
                if _hash(self._artifact(source_stage, dependency["name"])) != dependency["sha256"]:
                    return False
            if stage == 1 and _hash(self.path / "sources.json") != manifest["source_index_hash"]:
                return False
            for source in manifest.get("sources", []):
                path = (self.path / source["path"]).resolve()
                if not path.is_relative_to(self.path) or _hash(path) != source["sha256"]:
                    return False
            return True
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return False

    def archive_from(self, stage: int) -> None:
        if not self._executing:
            with self.execution():
                self.archive_from(stage)
            return
        self._artifact(stage, "manifest.json")
        next_state = self.state.model_dump(mode="json")
        next_state.update(
            stage=stage,
            attempt=self.state.attempt + 1,
            status="RUNNING",
            review_complete=False,
            approval="pending",
            question="",
            question_kind="",
            question_options=[],
        )
        if self.state.plan_stage is None or self.state.plan_stage >= stage:
            next_state.update(plan_stage=None, plan_name=None, plan_hash=None)
        transaction = {"stage": stage, "attempt": self.state.attempt, "state": next_state}
        _json(self.path / "archive-pending.json", transaction)
        self._recover_archive()

    def _recover_archive(self) -> None:
        marker = self.path / "archive-pending.json"
        if not marker.exists():
            return
        transaction = json.loads(marker.read_bytes())
        stage, attempt = transaction["stage"], transaction["attempt"]
        self._artifact(stage, "manifest.json")
        if not isinstance(attempt, int) or attempt < 1:
            raise WorkflowError("Invalid archive attempt")
        archive = self.path / "attempts" / str(attempt)
        if not archive.resolve().is_relative_to(self.path):
            raise WorkflowError("Archive escapes run directory")
        archive.mkdir(parents=True, exist_ok=True)
        for number in range(stage, 8):
            source = self._artifact(number, "manifest.json").parent
            target = archive / source.name
            if source.exists():
                if target.exists():
                    raise WorkflowError(f"Archive collision: {target}")
                os.replace(source, target)
        state = RunState.model_validate(transaction["state"])
        _json(self.path / "checkpoint.json", state.model_dump(mode="json"))
        previous, self.state = self.state, state
        marker.unlink()
        self._notify(("state", previous, state))

    def event(self, kind: str, data: dict) -> None:
        path = self.path / "events.jsonl"
        records = self.events()  # a corrupt middle record is an integrity error before appending
        prefix = b""
        if path.exists():
            existing = path.read_bytes()
            if existing and not existing.endswith(b"\n"):
                if len(records) == existing.count(b"\n") + 1:
                    prefix = b"\n"  # complete record that only lost its newline
                else:
                    with path.open("r+b") as handle:  # drop the torn final append
                        handle.truncate(existing.rfind(b"\n") + 1)
        entry = {"kind": kind, "data": data, "timestamp": datetime.now(UTC).isoformat()}
        line = prefix + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        self._notify(("event", kind, data))

    def activity(
        self,
        type: str,
        *,
        actor: str,
        summary: str,
        step_key: str | None = None,
        refs: Sequence[str] = (),
        payload: Mapping | None = None,
    ) -> None:
        if type not in ACTIVITY_TYPES:
            raise WorkflowError(f"Unknown activity type '{type}'")
        self.event(
            "activity",
            {
                "type": type,
                "actor": actor,
                "summary": summary,
                "step_key": step_key,
                "refs": list(refs),
                "payload": dict(payload or {}),
            },
        )

    def events(self) -> list[dict]:
        path = self.path / "events.jsonl"
        if not path.exists():
            return []
        lines = path.read_bytes().splitlines(keepends=True)
        result = []
        for index, line in enumerate(lines):
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict) or not {"kind", "data", "timestamp"} <= entry.keys():
                    raise ValueError("Malformed event record")
                result.append(entry)
            except (ValueError, UnicodeDecodeError) as exc:
                if index == len(lines) - 1 and not line.endswith(b"\n"):
                    break
                raise WorkflowError("Corrupt event journal") from exc
        return result

    def sources(self) -> dict[str, str]:
        if not self.valid(1):
            raise WorkflowError("Supplied source snapshot changed or source index is invalid")
        records = json.loads((self.path / "sources.json").read_bytes())["sources"]
        result = {}
        for record in records:
            path = (self.path / record["path"]).resolve()
            if not path.is_relative_to(self.path) or _hash(path) != record["sha256"]:
                raise WorkflowError("Supplied source snapshot changed")
            result[record["path"]] = path.read_bytes().decode("utf-8")
        return result

    @contextmanager
    def execution(self):
        # Kernel locks release on process death, avoiding stale PID heuristics and termination.
        handle = (self.path / ".execution.lock").open("a+b")
        locked = False
        try:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                self._executing = True
            except OSError as exc:
                raise WorkflowError("Run already has an active execution") from exc
            self._load_state()
            self._recover_archive()
            yield self
        finally:
            if locked:
                self._executing = False
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
