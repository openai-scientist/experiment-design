import json
import logging
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parent / "vendor"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def vendor() -> Path:
    return VENDOR


class Harness:
    """The engine served over HTTP, a local FakePlatform and the recorded model calls."""

    service_key_header = "X-Service-Key"

    def __init__(self, client, manager, platform, factory, logs, runs_root):
        self.client = client
        self.manager = manager
        self.platform = platform
        self.factory = factory
        self.logs = logs
        self.runs_root = runs_root

    @property
    def headers(self) -> dict[str, str]:
        return {self.service_key_header: self.platform.service_key}

    def request(self, stage_run_id: str = "sr1", **kwargs) -> dict:
        from tests.support.api_fakes import make_request

        body = make_request(self.platform, stage_run_id=stage_run_id, **kwargs)
        return body.model_dump(mode="json", by_alias=True)

    def create(self, stage_run_id: str = "sr1", **kwargs):
        return self.client.post(
            "/runs", json=self.request(stage_run_id, **kwargs), headers=self.headers
        )

    def state(self, run_id: str) -> dict:
        return self.client.get(f"/runs/{run_id}", headers=self.headers).json()

    def wait(self, run_id: str, phase: str, *, gate: bool = False, timeout: float = 60) -> dict:
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.state(run_id)
            if state["phase"] == phase and (not gate or state["open_gate"] is not None):
                return state
            time.sleep(0.02)
        raise AssertionError(f"timed out in {self.state(run_id)['phase']}, wanted {phase}")

    def answer(self, run_id: str, gate_id: str, body: dict):
        return self.client.post(f"/runs/{run_id}/gates/{gate_id}", json=body, headers=self.headers)

    def settle(self, run_id: str) -> None:
        self.manager.wait_idle(run_id)
        assert self.manager.flush(run_id)

    def events(self, stage_run_id: str = "sr1"):
        return self.platform.events.get(stage_run_id, [])


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@pytest.fixture
def contract_harness(tmp_path):
    from fastapi.testclient import TestClient

    from experiment_design.api.app import create_app
    from tests.integration.api.test_run_manager import _manager

    manager, platform, factory = _manager(tmp_path)
    capture = _Capture()
    logger = logging.getLogger("experiment_design")
    old_level = logger.level
    logger.addHandler(capture)
    logger.setLevel(logging.DEBUG)
    try:
        yield Harness(
            TestClient(create_app(manager.settings, manager=manager)),
            manager,
            platform,
            factory,
            capture,
            tmp_path / "runs",
        )
    finally:
        logger.removeHandler(capture)
        logger.setLevel(old_level)
