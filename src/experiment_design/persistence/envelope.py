"""Append-only log of contract event envelopes and the Platform delivery cursor."""

import json
import os
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from experiment_design.api.contract_models import EventClass, EventEnvelope, Phase
from experiment_design.contracts import WorkflowError
from experiment_design.persistence.store import _json

_LOCKS: dict[Path, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


class EnvelopeLog:
    """``source_seq`` is contiguous from 1 across appends, reopen and concurrent writers."""

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir).resolve() / "platform-events.jsonl"
        self._cursor = self.path.with_name("platform-cursor.json")
        self._lock = _lock_for(self.path)

    def _read(self) -> list[EventEnvelope]:
        if not self.path.exists():
            return []
        lines = self.path.read_bytes().splitlines(keepends=True)
        events = []
        for index, line in enumerate(lines):
            try:
                events.append(EventEnvelope.model_validate_json(line))
            except ValueError as exc:
                if index == len(lines) - 1 and not line.endswith(b"\n"):
                    break  # a torn final append is dropped, never delivered
                raise WorkflowError("Corrupt envelope log") from exc
        return events

    @property
    def last_seq(self) -> int:
        with self._lock:
            events = self._read()
        return events[-1].source_seq if events else 0

    def since(self, after_seq: int) -> list[EventEnvelope]:
        with self._lock:
            return [event for event in self._read() if event.source_seq > after_seq]

    def append(
        self,
        type: str,
        event_class: EventClass,
        *,
        phase: Phase | None,
        step_key: str | None,
        actor: str | None = None,
        summary: str | None = None,
        refs: Sequence[str] = (),
        payload: Mapping | None = None,
    ) -> EventEnvelope:
        with self._lock:
            events = self._read()
            envelope = EventEnvelope(
                source_seq=(events[-1].source_seq if events else 0) + 1,
                type=type,
                event_class=event_class,
                occurred_at=datetime.now(UTC),
                phase=phase,
                step_key=step_key,
                actor=actor,
                summary=summary,
                refs=list(refs),
                payload=dict(payload or {}),
            )
            line = json.dumps(envelope.model_dump(mode="json", by_alias=True), ensure_ascii=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            prefix = b""
            if self.path.exists():
                existing = self.path.read_bytes()
                if existing and not existing.endswith(b"\n"):
                    if len(events) == existing.count(b"\n") + 1:
                        prefix = b"\n"  # a complete record that only lost its newline
                    else:
                        with self.path.open("r+b") as handle:  # drop the torn final append
                            handle.truncate(existing.rfind(b"\n") + 1)
            with self.path.open("ab") as handle:
                handle.write(prefix + line.encode("utf-8") + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
        return envelope

    @property
    def delivered_seq(self) -> int:
        try:
            value = json.loads(self._cursor.read_text(encoding="utf-8"))["delivered_seq"]
        except (OSError, ValueError, KeyError, TypeError):
            return 0
        return value if isinstance(value, int) and value >= 0 else 0

    def mark_delivered(self, seq: int) -> None:
        with self._lock:
            if seq > self.delivered_seq:
                _json(self._cursor, {"delivered_seq": seq})
