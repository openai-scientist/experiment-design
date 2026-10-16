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
        # A terminal phase travels only in its own event (run.failed, run.cancelled,
        # run.published): Platform refuses artifacts once a phase_changed says published.
        if phase is Phase.failed:
            failure = self.failure or failure_of(state)
            if failure is not None:
                self._emit("run.failed", phase, self.step, failure.model_dump())
        elif phase is Phase.cancelled:
            self._emit("run.cancelled", phase, self.step, {})
        elif phase is not Phase.published:
            self._emit("run.phase_changed", phase, self.step, {"from": previous.value})

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
            activity = _activity(kind, data)
            if activity:
                self.on_append(
                    self.log.append(
                        activity["type"],
                        EventClass.activity,
                        phase=self.phase,
                        step_key=activity.get("step_key") or self.step,
                        actor=activity["actor"],
                        summary=activity["summary"],
                        refs=activity.get("refs", []),
                        payload=activity.get("payload", {}),
                    )
                )


#: Display names shared in style with i2h (contract 1.2.0 adds a shared actor_kind).
_PERSONAS = {
    "framing": "strategist",
    "clarification": "strategist",
    "profile_inference": "strategist",
    "design": "designer",
    "revision": "reviser",
    "methodologist_rejoinder": "methodologist",
    "skeptic_rejoinder": "skeptic",
    "final_check": "reviewer",
    "indexer": "reporter",
    "surveyor": "librarian",
    "selector": "librarian",
    "jev": "coordinator",
    "acquirer": "system",
    "validator": "system",
    "calculator": "system",
    "workflow": "system",
    "stage": "system",
}


def persona(actor: str) -> str:
    return _PERSONAS.get(actor, actor)


def _activity(kind: str, data: dict) -> dict | None:
    activity = _raw_activity(kind, data)
    if activity:
        activity = {**activity, "actor": persona(activity["actor"])}
    return activity


def _raw_activity(kind: str, data: dict) -> dict | None:
    if kind == "activity":
        return data
    payload = {}
    if kind == "agent.started":
        type, actor, summary = "exd.agent.started", data["role"], f"{data['role']} started"
    elif kind == "agent.accepted":
        actor = data["task"].split(":")[0]
        type, summary = "exd.agent.accepted", f"{actor} submitted accepted output"
    elif kind == "calculation" and data["result"].get("status") == "computed":
        type, actor = "exd.calculation.computed", "calculator"
        summary = f"Computed {data['result'].get('method_id', 'statistic')}"
        payload = {"result": data["result"]}
    elif kind == "route":
        type = "exd.route.decided"
        actor = "system" if data.get("automatic") else "jev"
        summary = f"Next action: {data['choice']}"
        payload = {"choice": data["choice"], "automatic": bool(data.get("automatic"))}
    elif kind == "reframe.objections":
        type, actor, summary = "exd.review.reframed", "workflow", "Reframing from objections"
        payload = {"count": len(data.get("objections", {}))}
    elif kind == "revision.pending":
        type, actor, summary = "exd.revision.started", "revision", "Revising the plan"
        payload = {"attempt": data["attempt"]}
    elif kind == "researcher.response":
        # A role like the other activities; the researcher's id is not an actor name.
        type, actor = "exd.researcher.responded", "researcher"
        summary = f"Researcher action: {data['action']}"
        payload = {"action": data["action"], "option": data.get("option")}
    else:
        return None
    return {"type": type, "actor": actor, "summary": summary, "payload": payload}
