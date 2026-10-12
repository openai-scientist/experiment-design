"""Synthetic historical records for read/export compatibility tests only."""

from uuid import uuid4

from experiment_design.contracts import ResearcherRequest, ScientificPackage
from experiment_design.runtime.store import RunRecord, now


def create_historical_run(store, package: ScientificPackage, config: dict) -> str:
    run_id = "r" + uuid4().hex[:8]
    record = RunRecord(
        run_id=run_id,
        package=package,
        config=config,
        status="RUNNING",
        created_at=now(),
    )
    with store.transaction():
        store.connection.execute(
            "INSERT INTO runs VALUES (?, ?)", (run_id, record.model_dump_json())
        )
        store.append_event(run_id, "run.status", {"status": "RUNNING"})
    return run_id


def save_historical_request(store, req: ResearcherRequest) -> ResearcherRequest:
    with store.transaction():
        req = store._save("requests", req, "R")
        store.append_event(req.run_id, "researcher.request", req.model_dump(mode="json"))
    return req
