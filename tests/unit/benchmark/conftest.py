import pytest

from experiment_design.agents.benchmark import http


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(http, "RETRY_DELAY", 0)
