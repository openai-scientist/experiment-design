"""Translate store changes into contract lifecycle events, in a contiguous sequence."""

import threading
from collections.abc import Callable

from experiment_design.api.contract_models import EventClass, EventEnvelope, FailureInfo, Phase
from experiment_design.api.mapping import failure_of, phase_of, step_key
from experiment_design.contracts import RunState
from experiment_design.persistence.envelope import EnvelopeLog
from experiment_design.persistence.store import RunStore, StoreChange


class Projector:
    """Subscribe with ``store.subscribe(projector)``; call ``start()`` once for a new run."""

    def __init__(
        self,
        store: RunStore,
        log: EnvelopeLog,
        *,
        on_append: Callable[[EventEnvelope], None] = lambda event: None,
    ):
        self.store = store
        self.log = log
        self.on_append = on_append
        self.role: str | None = None
        self.phase: Phase = phase_of(store.state, role=None)
        self.step: str = step_key(store.state.stage)
        self.failure: FailureInfo | None = None  # set by the run manager for non-workflow failures
        self._lock = threading.RLock()

    def start(self) -> None:
        """The first events of a run: its phase and its first step."""
        with self._lock:
            self._emit("run.phase_changed", self.phase, self.step, {"from": None})
            self._emit("run.step_changed", self.phase, self.step, {"from": None})

    def _emit(self, type: str, phase: Phase | None, step: str | None, payload: dict) -> None:
        self.on_append(
            self.log.append(type, EventClass.lifecycle, phase=phase, step_key=step, payload=payload)
        )

    def _enter_step(self, new: str) -> None:
        if new != self.step:
            previous, self.step = self.step, new
            self._emit("run.step_changed", self.phase, new, {"from": previous})

    def _settle(self, state: RunState) -> None:
        phase = phase_of(state, role=self.role)
        if phase is self.phase:
            return
        previous, self.phase = self.phase, phase
        self._emit("run.phase_changed", phase, self.step, {"from": previous.value})
        if phase is Phase.failed:
            failure = self.failure or failure_of(state)
            if failure is not None:
                self._emit("run.failed", phase, self.step, failure.model_dump())

    def __call__(self, change: StoreChange) -> None:
        with self._lock:
            if change[0] == "state":
                _, old, new = change
                if new.stage != old.stage:
                    self.role = None
                    self._enter_step(step_key(new.stage))
                self._settle(new)
                return
            _, kind, data = change
            if kind == "agent.started":
                self.role = data.get("role")
                self._settle(self.store.state)
            elif kind == "step.entered":
                self._enter_step(data["step_key"])
