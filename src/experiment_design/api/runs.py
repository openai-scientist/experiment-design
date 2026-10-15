"""Run manager: one worker thread per run over the seven-stage workflow.

Credentials live only in memory (``_Run``); nothing here writes a key to disk, a log or an
event. The workflow runs in a worker thread; HTTP handlers touch a run only under its lock.
"""

import hashlib
import json
import logging
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from experiment_design.api.callbacks import CallbackError, PlatformClient, StageClosed
from experiment_design.api.contract_models import (
    EXD_EXP_PLAN,
    GATE_ACTION_INVALID,
    GATE_NOT_OPEN,
    GATE_OPTION_INVALID,
    RUN_CLOSED,
    SERVICE_KEY_HEADER,
    TERMINAL_PHASES,
    VALIDATION_ERROR,
    ArtifactMeta,
    EventClass,
    EventEnvelope,
    FailureInfo,
    Gate,
    GateAnswer,
    GateKind,
    InteractionMode,
    ModelRole,
    Phase,
    RevisionDiff,
    RevisionResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunState,
    is_compatible,
)
from experiment_design.api.findings import review_findings
from experiment_design.api.inputs import InputError, fetch_inputs
from experiment_design.api.mapping import failure_of, open_gate, step_key
from experiment_design.api.observability import SECRETS
from experiment_design.api.projector import Projector
from experiment_design.api.settings import ApiSettings
from experiment_design.config import (
    ConfigError,
    ExdConfig,
    JevConfig,
    LLMConfig,
    llm_config_from,
    load_config,
)
from experiment_design.contracts import Profile, RunInterrupted, WorkflowError
from experiment_design.persistence.api_state import (
    ApiIndex,
    read_run_meta,
    update_run_meta,
    write_run_meta,
)
from experiment_design.persistence.envelope import EnvelopeLog
from experiment_design.persistence.store import RunStore
from experiment_design.runtime.jev import Judge, JudgeError, TypeSafeJudge
from experiment_design.runtime.llm import LLMClient, LLMError
from experiment_design.vocabulary import INTEGRATIONS, PUBLISH_EXTRAS
from experiment_design.workflow.engine import (
    _refine_reserve,
    advance,
    pin_limits,
    respond,
    start_run,
)

logger = logging.getLogger("experiment_design.api")
_STOPPED_STATUSES = {"STOPPED", "USAGE_UNKNOWN", "REJECTED", "CANCELLED", "FAILED"}

LlmFactory = Callable[[LLMConfig, str, ModelRole, Callable[[dict], None]], Any]
JudgeFactory = Callable[[JevConfig, str], Judge]

STAGE_KEY = "experiment_plan"
_DEFAULT_ANSWER = "Proceed with the recommended default."
_CANCEL_WAIT_SECONDS = 5.0


class RequestRejected(Exception):
    """HTTP 422: the request cannot be served as sent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ConflictError(Exception):
    """HTTP 409: the run is not in a state that allows the action."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class RunNotFound(Exception):
    pass


def _default_llm(cfg: LLMConfig, key: str, role: ModelRole, on_call: Callable[[dict], None]):
    return LLMClient(cfg, key, role=role.value, on_call=on_call)


def _default_judge(cfg: JevConfig, key: str) -> Judge:
    return TypeSafeJudge(cfg, api_key=key)


class _Flusher(threading.Thread):
    """The single sender of a run's events: keeps retrying until the log is delivered."""

    def __init__(self, client: PlatformClient, log: EnvelopeLog, on_closed: Callable[[], None]):
        super().__init__(daemon=True, name="exd-flusher")
        self.client, self.log, self.on_closed = client, log, on_closed
        self._wake = threading.Event()
        self._halt = threading.Event()

    def kick(self) -> None:
        self._wake.set()

    def halt(self) -> None:
        self._halt.set()
        self._wake.set()

    def run(self) -> None:
        delay = 0.5
        while not self._halt.is_set():
            self._wake.wait(timeout=30)
            self._wake.clear()
            if self._halt.is_set():
                return
            try:
                self.client.flush_events(self.log)
                delay = 0.5
            except StageClosed:
                self.on_closed()
                return
            except CallbackError as exc:
                logger.warning("event delivery failed, will retry: %s", exc)
                self._halt.wait(delay)
                delay = min(delay * 2, 30.0)
                self._wake.set()

    def drain(self, timeout: float) -> bool:
        """Block until every appended event is delivered (tests and shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.log.delivered_seq >= self.log.last_seq:
                return True
            self.kick()
            time.sleep(0.01)
        return self.log.delivered_seq >= self.log.last_seq


@dataclass
class _Run:
    store: RunStore
    cfg: ExdConfig
    request: RunCreateRequest
    llm: Any
    review_llm: Any
    judge: Judge
    integrations: dict[str, str]
    log: EnvelopeLog
    projector: Projector
    client: PlatformClient
    flusher: _Flusher
    lock: threading.RLock = field(default_factory=threading.RLock)
    thread: threading.Thread | None = None
    work_pending: bool = False
    gate: Gate | None = None
    closed_gates: dict[str, list] = field(default_factory=dict)
    stage_closed: bool = False

    @property
    def engine_run_id(self) -> str:
        return self.store.state.run_id

    @property
    def phase(self) -> Phase:
        return self.projector.phase

    @property
    def mode(self) -> InteractionMode:
        return self.request.interaction_mode


def _normalized(answer: GateAnswer) -> list:
    return [
        answer.action,
        answer.option_id,
        (answer.text or "").strip(),
        [[f.finding_id, f.issue, f.suggestion] for f in answer.findings],
    ]


def _comment_text(answer: GateAnswer) -> str:
    lines = [
        f"- [{f.anchor.locator}] {f.issue}" + (f" - {f.suggestion}" if f.suggestion else "")
        for f in answer.findings
    ]
    return "\n".join(
        [*([answer.text.strip()] if answer.text and answer.text.strip() else []), *lines]
    )


class RunManager:
    def __init__(
        self,
        settings: ApiSettings,
        *,
        llm_factory: LlmFactory | None = None,
        judge_factory: JudgeFactory | None = None,
        platform_transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self._llm_factory = llm_factory or _default_llm
        self._judge_factory = judge_factory or _default_judge
        self._transport = platform_transport
        self._runs: dict[str, _Run] = {}
        self._create_lock = threading.Lock()
        self._base = load_config(settings.config_path, require_provider_keys=False)
        self._index = ApiIndex(self._base.runs_root)
        self._deliver_attempts = 5

    # --- creation --------------------------------------------------------------------------

    def _validate(self, request: RunCreateRequest) -> None:
        if not is_compatible(request.contract_version):
            raise RequestRejected(VALIDATION_ERROR, "incompatible contract_version")
        if request.stage_key != STAGE_KEY:
            raise RequestRejected(VALIDATION_ERROR, f"unsupported stage_key '{request.stage_key}'")
        for name in INTEGRATIONS:
            if not request.integrations.get(name) or not (
                request.integrations[name].get_secret_value()
            ):
                raise RequestRejected(VALIDATION_ERROR, f"missing integration: {name}")
        for role in ("stage", "review"):
            key = getattr(request.models, role).api_key
            if key is None or not key.get_secret_value():
                raise RequestRejected(VALIDATION_ERROR, f"missing api_key for the {role} model")

    def _config_for(self, request: RunCreateRequest) -> ExdConfig:
        try:
            stage = llm_config_from(request.models.stage, self._base.llm)
            review = llm_config_from(request.models.review, self._base.llm)
        except ConfigError as exc:
            raise RequestRejected(VALIDATION_ERROR, str(exc)) from None
        run = self._base.run.model_copy(
            update={"max_refinements": request.review_policy.max_auto_revisions}
        )
        return self._base.model_copy(
            update={
                "llm": stage,
                "review_llm": review,
                "run": run,
                "profile": Profile(**request.profile.model_dump()),
                "interaction_mode": request.interaction_mode.value,
            }
        )

    def create(self, request: RunCreateRequest) -> RunCreateResponse:
        with self._create_lock:
            known = self._index.get(request.stage_run_id)
            if known:
                run = self._runs.get(known)
                phase = run.phase if run else Phase.failed
                return RunCreateResponse(engine_run_id=known, phase=phase, created=False)
            self._validate(request)
            for secret in (
                request.models.stage.api_key,
                request.models.review.api_key,
                *request.integrations.values(),
            ):
                SECRETS.add(secret.get_secret_value())
            cfg = self._config_for(request)
            service_key = self.settings.service_key.get_secret_value()
            with (
                tempfile.TemporaryDirectory() as tmp,
                httpx.Client(
                    transport=self._transport,
                    headers={SERVICE_KEY_HEADER: service_key},
                    timeout=60,
                ) as http,
            ):
                try:
                    paths = fetch_inputs(request, http, Path(tmp))
                except InputError as exc:
                    raise RequestRejected(exc.code, str(exc)) from None
                try:
                    store = start_run(paths, cfg)
                except WorkflowError as exc:
                    raise RequestRejected(VALIDATION_ERROR, str(exc)) from None
            run = self._build_run(request, cfg, store, service_key)
            write_run_meta(
                store.path,
                {
                    "stage_run_id": request.stage_run_id,
                    "platform_run_id": request.platform_run_id,
                    "stage_key": request.stage_key,
                    "iteration": request.iteration,
                    "interaction_mode": request.interaction_mode.value,
                    "callback_url": request.callback_url,
                    "budget_usd": str(request.budget_usd),
                    "llm_token_limit": store.state.llm_token_limit,
                    "artifact_version": 0,
                    "gate": None,
                    "closed_gates": {},
                },
            )
            self._runs[run.engine_run_id] = run
            self._index.put(request.stage_run_id, run.engine_run_id)
            run.projector.start()
            run.flusher.start()
            with run.lock:
                self._spawn(run)
            return RunCreateResponse(engine_run_id=run.engine_run_id, phase=run.phase, created=True)

    def _build_run(
        self, request: RunCreateRequest, cfg: ExdConfig, store: RunStore, service_key: str
    ) -> _Run:
        models = request.models
        keep_alive = lambda call: None  # noqa: E731 - usage reporting arrives with telemetry
        llm = self._llm_factory(
            cfg.llm, models.stage.api_key.get_secret_value(), ModelRole.stage, keep_alive
        )
        review_llm = self._llm_factory(
            cfg.review_llm, models.review.api_key.get_secret_value(), ModelRole.review, keep_alive
        )
        integrations = {name: v.get_secret_value() for name, v in request.integrations.items()}
        judge = self._judge_factory(cfg.jev, integrations["typesafe"])
        log = EnvelopeLog(store.path)
        client = PlatformClient(request.callback_url, service_key, transport=self._transport)
        projector = Projector(store, log)
        holder: dict[str, _Run] = {}
        flusher = _Flusher(client, log, lambda: self._stage_closed(holder["run"]))
        projector.on_append = lambda event: flusher.kick()
        store.subscribe(projector)
        run = _Run(
            store=store,
            cfg=cfg,
            request=request,
            llm=llm,
            review_llm=review_llm,
            judge=judge,
            integrations=integrations,
            log=log,
            projector=projector,
            client=client,
            flusher=flusher,
        )
        holder["run"] = run
        return run

    # --- lookup ----------------------------------------------------------------------------

    def _get(self, engine_run_id: str) -> _Run:
        run = self._runs.get(engine_run_id)
        if run is None:
            raise RunNotFound(engine_run_id)
        return run

    def active_runs(self) -> int:
        return sum(1 for run in self._runs.values() if run.phase not in TERMINAL_PHASES)

    def phase(self, engine_run_id: str) -> Phase:
        return self._get(engine_run_id).phase

    def state(self, engine_run_id: str) -> RunState:
        return self._state_of(self._get(engine_run_id))

    def _state_of(self, run: _Run) -> RunState:
        failure = None
        if run.phase is Phase.failed:
            failure = run.projector.failure or failure_of(run.store.state)
        return RunState(
            engine_run_id=run.engine_run_id,
            stage_run_id=run.request.stage_run_id,
            phase=run.phase,
            step_key=run.projector.step,
            waiting_input=run.gate is not None,
            open_gate=run.gate,
            failure=failure,
            last_source_seq=run.log.last_seq,
            cost_usd=Decimal(0),
        )

    def events(self, engine_run_id: str, after: int, limit: int) -> list[EventEnvelope]:
        return self._get(engine_run_id).log.since(after)[:limit]

    def flush(self, engine_run_id: str, timeout: float = 10.0) -> bool:
        return self._get(engine_run_id).flusher.drain(timeout)

    def wait_idle(self, engine_run_id: str, timeout: float = 30.0) -> bool:
        """Block until the worker of a run has stopped (tests and shutdown)."""
        deadline = time.monotonic() + timeout
        run = self._get(engine_run_id)
        while time.monotonic() < deadline:
            if run.thread is None:
                return True
            time.sleep(0.01)
        return run.thread is None

    def shutdown(self) -> None:
        for run in self._runs.values():
            run.flusher.halt()

    # --- emitting --------------------------------------------------------------------------

    def _emit(
        self,
        run: _Run,
        type: str,
        payload: dict,
        *,
        phase: Phase | None = None,
        step: str | None = None,
    ) -> None:
        run.log.append(
            type,
            EventClass.lifecycle,
            phase=phase or run.phase,
            step_key=step or run.projector.step,
            payload=payload,
        )
        run.flusher.kick()

    def _next_version(self, run: _Run) -> int:
        meta = read_run_meta(run.store.path)
        version = int(meta.get("artifact_version", 0)) + 1
        update_run_meta(run.store.path, artifact_version=version)
        return version

    def _push(self, run: _Run, meta: ArtifactMeta, data: bytes) -> str:
        delay = 0.5
        for attempt in range(self._deliver_attempts):
            try:
                return run.client.push_artifact(meta, data)
            except StageClosed:
                raise
            except CallbackError as exc:
                if attempt == self._deliver_attempts - 1:
                    raise
                logger.warning("artifact delivery failed, will retry: %s", exc)
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
        raise CallbackError("artifact delivery failed")  # pragma: no cover

    def _upload_plan(self, run: _Run, data: bytes, *, primary: bool, metadata: dict) -> str:
        meta = ArtifactMeta(
            schema_id=EXD_EXP_PLAN,
            version=self._next_version(run),
            filename="exp_plan.md",
            media_type="text/markdown",
            sha256=hashlib.sha256(data).hexdigest(),
            primary=primary,
            review_round=run.store.state.attempt,
            metadata=metadata,
        )
        artifact_id = self._push(run, meta, data)
        self._emit(
            run,
            "artifact.delivered",
            {
                "artifact_id": artifact_id,
                "schema": meta.schema_id,
                "version": meta.version,
                "primary": primary,
            },
        )
        return artifact_id

    # --- worker ----------------------------------------------------------------------------

    def _spawn(self, run: _Run) -> None:
        """Ask for another worker pass; the caller holds ``run.lock``."""
        run.work_pending = True
        if run.thread is None:
            run.thread = threading.Thread(
                target=self._work, args=(run,), daemon=True, name=f"exd-{run.engine_run_id}"
            )
            run.thread.start()

    def _work(self, run: _Run) -> None:
        try:
            while True:
                with run.lock:
                    run.work_pending = False
                again = False
                try:
                    again = self._cycle(run)
                except RunInterrupted as exc:
                    self._interrupted(run, exc.kind)
                except StageClosed:
                    self._stage_closed(run)
                except WorkflowError as exc:
                    # advance stops the run itself and the projector announces run.failed;
                    # any other WorkflowError would leave the run waiting with no gate.
                    if run.store.state.status not in _STOPPED_STATUSES:
                        logger.exception("workflow error in run %s", run.engine_run_id)
                        self._fail(run, "internal_error", str(exc))
                except (LLMError, JudgeError, CallbackError, ConfigError) as exc:
                    self._fail(run, _failure_code(exc), _failure_message(exc))
                except Exception:  # a dead worker thread would leave the run stuck RUNNING
                    logger.exception("unexpected error in run %s", run.engine_run_id)
                    self._fail(run, "internal_error", "The engine hit an unexpected error.")
                if again:
                    continue
                with run.lock:
                    self._apply_pending_interrupt(run)
                    if not run.work_pending:
                        run.thread = None
                        return
        finally:
            run.flusher.kick()

    def _cycle(self, run: _Run) -> bool:
        """One pass: advance the workflow, then act on where it stopped. True = go again."""
        state = run.store.state
        # INCOMPLETE waits for the researcher and PUBLISHED is done; advancing would redo them.
        if state.status not in {"INCOMPLETE", "PUBLISHED"}:
            state = advance(
                run.store,
                run.llm,
                run.judge,
                run.cfg.run,
                review_llm=run.review_llm,
                hold_at_review=True,
                integrations=run.integrations,
            )
        with run.lock:
            self._deliver_revisions(run)
            if state.status == "PUBLISHED":
                self._deliver_published(run)
            elif state.status in {"PENDING_REVIEW", "REVIEW_ERROR"}:
                self._open_review_gate(run)
            elif state.status == "AWAITING_RESEARCHER":
                if state.question_kind == "REVIEW":
                    requests = [
                        e["data"] for e in run.store.events() if e["kind"] == "review.requested"
                    ]
                    request = requests[-1]
                    body = run.store.read(request["stage"], request["name"]).encode()
                    artifact_id = self._upload_artifact(
                        run, request["schema_id"], request["name"], body
                    )
                    if run.mode in {InteractionMode.auto, InteractionMode.light}:
                        respond(
                            run.store,
                            action="APPROVE",
                            text="Optional review approved by system",
                            researcher="system",
                            revision=state.attempt,
                        )
                        return True
                    self._set_gate(
                        run, open_gate(run.store, artifact_ids=[artifact_id], mode=run.mode)
                    )
                    return False
                if run.mode in {InteractionMode.copilot, InteractionMode.full}:
                    gate = open_gate(run.store, artifact_ids=[], mode=run.mode)
                    self._set_gate(run, gate)
                    return False
                return self._answer_for_system(run)
            elif state.status == "INCOMPLETE" and run.gate is None:
                self._open_incomplete_gate(run)
        return False

    def _deliver_revisions(self, run: _Run) -> None:
        delivered = read_run_meta(run.store.path).get("revisions", {})
        for event in run.store.events():
            if event["kind"] != "revision.completed":
                continue
            data = event["data"]
            key = str(data["attempt"])
            if key in delivered:
                continue
            before_id = self._upload_plan(run, data["before"].encode(), primary=False, metadata={})
            after_id = self._upload_plan(run, data["after"].encode(), primary=False, metadata={})
            diff = RevisionDiff(
                round=data["round"],
                artifact_before=before_id,
                artifact_after=after_id,
                patch=data["patch"],
            )
            response = RevisionResponse(
                round=data["round"],
                artifact_before=before_id,
                artifact_after=after_id,
                items=data["responses"],
            )
            self._upload_artifact(
                run,
                "revision.diff/v1",
                "plan_diff.json",
                diff.model_dump_json(by_alias=True).encode(),
            )
            self._upload_artifact(
                run,
                "revision.response/v1",
                "revision_response.json",
                response.model_dump_json(by_alias=True).encode(),
            )
            delivered[key] = {"before": before_id, "after": after_id}
            update_run_meta(run.store.path, revisions=delivered)

    def _answer_for_system(self, run: _Run) -> bool:
        state = run.store.state
        respond(
            run.store,
            action="ANSWER",
            text="" if state.question_options else _DEFAULT_ANSWER,
            researcher="system",
            revision=state.attempt,
            option=state.question_default if state.question_options else None,
        )
        return True

    def _open_review_gate(self, run: _Run) -> None:
        store = run.store
        data = store.read(store.state.plan_stage, store.state.plan_name).encode("utf-8")
        artifact_id = self._upload_plan(run, data, primary=False, metadata={})
        self._deliver_review(run, artifact_id)
        gate = open_gate(store, artifact_ids=[artifact_id], mode=run.mode)
        if gate is None:
            return
        self._set_gate(run, gate)

    def _open_incomplete_gate(self, run: _Run) -> None:
        """The revisions ran out without a passed review: the researcher decides, mode aside."""
        store = run.store
        data = store.read(store.state.plan_stage, store.state.plan_name).encode("utf-8")
        artifact_id = self._upload_plan(run, data, primary=False, metadata={})
        self._deliver_review(run, artifact_id)
        self._set_gate(run, open_gate(store, artifact_ids=[artifact_id], mode=run.mode))

    def _deliver_review(self, run: _Run, artifact_id: str) -> None:
        store = run.store
        error = None
        if store.state.status == "REVIEW_ERROR":
            error = "Reviewer failed after two retries"
        elif store.state.status == "INCOMPLETE" and not store.valid(5) and not store.valid(4):
            error = "The run stopped before any review existed"
        report = None
        if not error:
            try:
                report = review_findings(
                    store,
                    round=store.state.attempt,
                    artifact_id=artifact_id,
                    reviewer_model=run.cfg.review_llm.model,
                )
            except WorkflowError as exc:
                error = f"The final review is invalid: {exc}"
        if report is None:
            self._emit(
                run,
                "review.completed",
                {
                    "round": store.state.attempt,
                    "passed": False,
                    "artifact_id": None,
                    "error": error,
                },
            )
            return
        if store.state.status == "INCOMPLETE":
            report = report.model_copy(update={"passed": False})
        body = report.model_dump_json(by_alias=True).encode()
        report_id = self._upload_artifact(run, "review.findings/v1", "review_findings.json", body)
        self._emit(
            run,
            "review.completed",
            {"round": report.round, "passed": report.passed, "artifact_id": report_id},
        )

    def _upload_artifact(self, run: _Run, schema_id: str, name: str, body: bytes) -> str:
        meta = ArtifactMeta(
            schema_id=schema_id,
            version=self._next_version(run),
            filename=name,
            media_type="application/json",
            sha256=hashlib.sha256(body).hexdigest(),
            primary=False,
            review_round=run.store.state.attempt,
        )
        result = self._push(run, meta, body)
        self._emit(
            run,
            "artifact.delivered",
            {"artifact_id": result, "schema": schema_id, "version": meta.version, "primary": False},
        )
        return result

    def _set_gate(self, run: _Run, gate: Gate | None) -> None:
        if gate is None:
            return
        run.gate = gate
        update_run_meta(run.store.path, gate=gate.model_dump(mode="json"))
        self._emit(
            run,
            "run.gate_opened",
            gate.model_dump(mode="json"),
            phase=run.phase,
            step=gate.step_key,
        )

    def _deliver_published(self, run: _Run) -> None:
        store = run.store
        data = store.read(7, "exp_plan.md").encode("utf-8")
        publication = json.loads(store.read(7, "publication.json"))
        artifact_id = self._upload_plan(run, data, primary=True, metadata=publication)
        for filename, schema_id in PUBLISH_EXTRAS:
            if (store.path / "stage-07" / filename).is_file():
                self._upload_artifact(run, schema_id, filename, store.read(7, filename).encode())
        run.gate = None
        update_run_meta(store.path, gate=None)
        self._emit(
            run,
            "run.published",
            {"artifact_id": artifact_id},
            phase=Phase.published,
            step=step_key(7),
        )

    def _fail(self, run: _Run, code: str, message: str) -> None:
        run.projector.failure = FailureInfo(code=code, message=message)
        run.gate = None
        run.store.update(status="FAILED")

    # --- interrupts ------------------------------------------------------------------------

    def _interrupted(self, run: _Run, kind: str) -> None:
        with run.lock:
            run.store.clear_interrupt()
            if kind == "cancel":
                run.gate = None
                run.store.update(status="CANCELLED")
            else:
                run.store.remember_status(run.store.state.status)
                run.store.update(status="PAUSED")

    def _apply_pending_interrupt(self, run: _Run) -> None:
        """A request that arrived after the last agent call; the caller holds the lock."""
        kind = run.store.interrupt()
        if kind is None:
            return
        if run.phase in TERMINAL_PHASES:
            run.store.clear_interrupt()
        else:
            self._interrupted(run, kind)

    def _stage_closed(self, run: _Run) -> None:
        """Platform closed this stage run: stop local work and stop delivering."""
        run.stage_closed = True
        run.flusher.halt()
        run.store.request_interrupt("cancel")
        with run.lock:
            if run.thread is None and run.phase not in TERMINAL_PHASES:
                self._interrupted(run, "cancel")

    def _stop_worker(self, run: _Run) -> None:
        thread = run.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_CANCEL_WAIT_SECONDS)

    def cancel(self, engine_run_id: str) -> RunState:
        run = self._get(engine_run_id)
        with run.lock:
            if run.phase in TERMINAL_PHASES:
                raise ConflictError(RUN_CLOSED, "the run is already finished")
            run.store.request_interrupt("cancel")
            idle = run.thread is None
            if idle:
                self._interrupted(run, "cancel")
        if not idle:
            self._stop_worker(run)
        return self._state_of(run)

    def pause(self, engine_run_id: str) -> RunState:
        run = self._get(engine_run_id)
        with run.lock:
            if run.phase in TERMINAL_PHASES:
                raise ConflictError(RUN_CLOSED, "the run is already finished")
            if run.phase is Phase.paused:
                return self._state_of(run)
            run.store.request_interrupt("pause")
            idle = run.thread is None
            if idle:
                self._interrupted(run, "pause")
        if not idle:
            self._stop_worker(run)
        return self._state_of(run)

    def resume(self, engine_run_id: str) -> RunState:
        run = self._get(engine_run_id)
        with run.lock:
            if run.phase is not Phase.paused:
                if run.phase in TERMINAL_PHASES:
                    raise ConflictError(RUN_CLOSED, "the run is already finished")
                return self._state_of(run)
            previous = run.store.remembered_status() or "RUNNING"
            run.store.remember_status(None)
            run.store.update(status=previous)
            if previous != "PENDING_REVIEW":
                self._spawn(run)
        return self._state_of(run)

    # --- gates -----------------------------------------------------------------------------

    def answer(self, engine_run_id: str, gate_id: str, answer: GateAnswer) -> RunState:
        run = self._get(engine_run_id)
        with run.lock:
            normalized = _normalized(answer)
            gate = run.gate
            if gate is None or gate.gate_id != gate_id:
                if run.closed_gates.get(gate_id) == normalized:
                    return self._state_of(run)  # the same answer again: nothing to redo
                raise ConflictError(GATE_NOT_OPEN, "no such gate is open")
            if run.phase is Phase.paused:
                raise ConflictError(GATE_NOT_OPEN, "the run is paused")
            if (gate.kind is GateKind.question) != (answer.action == "answer"):
                raise RequestRejected(GATE_ACTION_INVALID, "action does not match gate kind")
            option = None
            if gate.kind is GateKind.question:
                option = next(
                    (i for i, o in enumerate(gate.options, 1) if o.option_id == answer.option_id),
                    None,
                )
                if option is None:
                    raise RequestRejected(GATE_OPTION_INVALID, "select a listed option")
            researcher = (
                answer.decided_by.strip()
                if answer.actor == "user" and answer.decided_by and answer.decided_by.strip()
                else "system"
            )
            state = run.store.state
            try:
                if answer.action == "answer":
                    respond(
                        run.store,
                        action="ANSWER",
                        option=option,
                        text=answer.text or "",
                        researcher=researcher,
                        revision=state.attempt,
                    )
                elif answer.action == "approve":
                    respond(
                        run.store,
                        action="APPROVE",
                        text="Approved on the platform.",
                        researcher=researcher,
                        revision=state.attempt,
                    )
                else:
                    text = _comment_text(answer)
                    if not text.strip():
                        raise RequestRejected(
                            GATE_ACTION_INVALID, "a comment needs text or findings"
                        )
                    if run.store.state.question_kind != "REVIEW":
                        remaining = state.llm_token_limit - state.llm_tokens
                        if remaining < _refine_reserve(
                            "REQUEST_CHANGES",
                            run.cfg.run.task_max_tokens,
                            profiled=state.profile is not None,
                        ):
                            raise RequestRejected(VALIDATION_ERROR, "token_allowance_exhausted")
                        limits = run.cfg.run.model_copy(
                            update={"max_refinements": run.cfg.run.max_refinements + 1}
                        )
                        pin_limits(run.store, limits)
                        run.cfg.run = limits
                    respond(
                        run.store,
                        action="REQUEST_CHANGES",
                        text=text,
                        researcher=researcher,
                        revision=state.attempt,
                    )
            except WorkflowError as exc:
                raise RequestRejected(GATE_ACTION_INVALID, str(exc)) from None
            run.closed_gates[gate_id] = normalized
            run.gate = None
            update_run_meta(run.store.path, gate=None, closed_gates=run.closed_gates)
            self._emit(
                run,
                "run.gate_closed",
                {"gate_id": gate_id, "action": answer.action, "actor": answer.actor},
            )
            self._spawn(run)
            return self._state_of(run)


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, LLMError):
        return "llm_error"
    if isinstance(exc, JudgeError):
        return "judge_error"
    if isinstance(exc, CallbackError):
        return "platform_unavailable"
    return "config_error"


def _failure_message(exc: Exception) -> str:
    return {
        "llm_error": "The language model provider failed.",
        "judge_error": "The decision service failed.",
        "platform_unavailable": "Platform could not be reached.",
        "config_error": "The engine configuration is invalid.",
    }[_failure_code(exc)]
