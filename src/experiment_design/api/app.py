"""HTTP face of the engine: the routes of the engine contract, nothing else."""

import hmac
import logging

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from experiment_design.api.contract_models import (
    CONTRACT_VERSION,
    ENGINE_RUN,
    ENGINE_RUN_CANCEL,
    ENGINE_RUN_EVENTS,
    ENGINE_RUN_GATE,
    ENGINE_RUN_PAUSE,
    ENGINE_RUN_RESUME,
    ENGINE_RUNS,
    RUN_NOT_FOUND,
    SERVICE_KEY_HEADER,
    SERVICE_KEY_INVALID,
    VALIDATION_ERROR,
    Capabilities,
    EventPage,
    GateAnswer,
    Health,
    OutputDecl,
    ResearchProfile,
    RunCreateRequest,
    RunCreateResponse,
    RunState,
    StepInfo,
)
from experiment_design.api.runs import (
    ConflictError,
    RequestRejected,
    RunManager,
    RunNotFound,
)
from experiment_design.api.settings import ApiSettings
from experiment_design.vocabulary import (
    ACTIVITY_TYPES,
    INPUT_SCHEMAS,
    INTEGRATIONS,
    OPTIONAL_INTEGRATIONS,
    OUTPUT_SCHEMAS,
    PROFILES,
    STEPS,
)

logger = logging.getLogger("experiment_design.api")

ENGINE_KEY = "exd"


class _Unauthorized(Exception):
    pass


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, **extra})


def create_app(settings: ApiSettings, *, manager: RunManager | None = None) -> FastAPI:
    app = FastAPI(title="experiment-design engine", docs_url=None, redoc_url=None, openapi_url=None)
    runs = manager or RunManager(settings)
    expected = settings.service_key.get_secret_value().encode("utf-8")

    def key_valid(provided: str | None) -> bool:
        return provided is not None and hmac.compare_digest(provided.encode("utf-8"), expected)

    def require_key(x_service_key: str | None = Header(default=None, alias=SERVICE_KEY_HEADER)):
        if not key_valid(x_service_key):
            raise _Unauthorized

    @app.exception_handler(_Unauthorized)
    async def unauthorized(request: Request, exc: _Unauthorized):
        return _error(401, SERVICE_KEY_INVALID, "invalid or missing service key")

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError):
        # Never echo the input: it can hold API keys. Only where and why it failed.
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return _error(422, VALIDATION_ERROR, "request validation failed", details=details)

    @app.exception_handler(RequestRejected)
    async def rejected(request: Request, exc: RequestRejected):
        return _error(422, exc.code, exc.message)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError):
        return _error(409, exc.code, exc.message)

    @app.exception_handler(RunNotFound)
    async def missing(request: Request, exc: RunNotFound):
        return _error(404, RUN_NOT_FOUND, "no such run")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        logger.error("unhandled error on %s: %s", request.url.path, type(exc).__name__)
        return _error(500, "INTERNAL_ERROR", "internal error")

    @app.get("/health", response_model=Health, response_model_exclude_none=True)
    def health(x_service_key: str | None = Header(default=None, alias=SERVICE_KEY_HEADER)):
        detailed = key_valid(x_service_key)
        return Health(
            status="ok",
            engine_version=settings.engine_version,
            contract_version=CONTRACT_VERSION,
            active_runs=runs.active_runs(),
            checks={"config": True} if detailed else None,
        )

    @app.get("/capabilities", response_model=Capabilities)
    def capabilities(_: None = Depends(require_key)):
        return Capabilities(
            engine_key=ENGINE_KEY,
            engine_version=settings.engine_version,
            contract_version=CONTRACT_VERSION,
            stage_keys=["experiment_plan"],
            input_schemas=list(INPUT_SCHEMAS),
            output_schemas=[OutputDecl(schema=s, primary=p) for s, p in OUTPUT_SCHEMAS],
            steps=[
                StepInfo(step_key=k, label=label, order=i) for i, (k, label) in enumerate(STEPS, 1)
            ],
            question_gates={},
            activity_types=list(ACTIVITY_TYPES),
            integrations=list(INTEGRATIONS),
            optional_integrations=list(OPTIONAL_INTEGRATIONS),
            providers=["openai_compatible"],
            auto_revision=True,
            reuses_prior_run=False,
            profiles=[ResearchProfile(**p) for p in PROFILES],
        )

    @app.post(ENGINE_RUNS, response_model=RunCreateResponse, status_code=201)
    def create_run(request: RunCreateRequest, _: None = Depends(require_key)):
        created = runs.create(request)
        if not created.created:
            return JSONResponse(status_code=200, content=created.model_dump(mode="json"))
        return created

    @app.get(ENGINE_RUN, response_model=RunState)
    def get_run(engine_run_id: str, _: None = Depends(require_key)):
        return runs.state(engine_run_id)

    @app.get(ENGINE_RUN_EVENTS, response_model=EventPage)
    def get_events(
        engine_run_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        _: None = Depends(require_key),
    ):
        return EventPage(events=runs.events(engine_run_id, after, limit))

    @app.post(ENGINE_RUN_GATE, response_model=RunState)
    def answer_gate(
        engine_run_id: str, gate_id: str, answer: GateAnswer, _: None = Depends(require_key)
    ):
        return runs.answer(engine_run_id, gate_id, answer)

    @app.post(ENGINE_RUN_PAUSE, response_model=RunState)
    def pause(engine_run_id: str, _: None = Depends(require_key)):
        return runs.pause(engine_run_id)

    @app.post(ENGINE_RUN_RESUME, response_model=RunState)
    def resume(engine_run_id: str, _: None = Depends(require_key)):
        return runs.resume(engine_run_id)

    @app.post(ENGINE_RUN_CANCEL, response_model=RunState)
    def cancel(engine_run_id: str, _: None = Depends(require_key)):
        return runs.cancel(engine_run_id)

    app.state.manager = runs
    return app
