"""Append-only log of contract event envelopes and the Platform delivery cursor."""

import json
import os
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from experiment_design.api.contract_models import EventClass, EventEnvelope, Phase
from experiment_design.persistence.store import LogSnapshot, _json, forget_log, read_log

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

    def _read(self) -> LogSnapshot | None:
        return read_log(self.path, EventEnvelope.model_validate_json, "Corrupt envelope log")

    @property
    def last_seq(self) -> int:
        with self._lock:
            snapshot = self._read()
        events = [] if snapshot is None else snapshot.all
        return events[-1].source_seq if events else 0

    def since(self, after_seq: int) -> list[EventEnvelope]:
        with self._lock:
            snapshot = self._read()
        events = [] if snapshot is None else snapshot.all
        return [event.model_copy(deep=True) for event in events if event.source_seq > after_seq]

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
            snapshot = self._read()
            events = [] if snapshot is None else snapshot.all
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
            if snapshot is not None and snapshot.torn:
                if snapshot.tail is not None:
                    prefix = b"\n"  # a complete record that only lost its newline
                else:
                    with self.path.open("r+b") as handle:  # drop the torn final append
                        handle.truncate(snapshot.offset)
                    forget_log(self.path)
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

    def reset_delivered(self, seq: int) -> None:
        """Platform reports what it actually holds; resend from the next sequence."""
        with self._lock:
            _json(self._cursor, {"delivered_seq": seq})
