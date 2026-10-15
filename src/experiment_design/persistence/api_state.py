"""Files the HTTP layer keeps beside a run: the stage-run index and the run metadata.

Neither file ever holds a credential: only identifiers, counters and open-gate facts.
"""

import threading
from pathlib import Path

from experiment_design.persistence import filecache
from experiment_design.persistence.store import _json

_INDEX_LOCK = threading.Lock()


class ApiIndex:
    """``stage_run_id -> engine_run_id`` under ``runs_root``, making creation idempotent."""

    def __init__(self, runs_root: Path):
        self.path = Path(runs_root).resolve() / "api-index.json"

    def _read(self) -> dict[str, str]:
        try:
            data = filecache.read_json(self.path)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, stage_run_id: str) -> str | None:
        with _INDEX_LOCK:
            return self._read().get(stage_run_id)

    def put(self, stage_run_id: str, engine_run_id: str) -> None:
        with _INDEX_LOCK:
            _json(self.path, {**self._read(), stage_run_id: engine_run_id})


def read_run_meta(run_dir: Path) -> dict:
    try:
        data = filecache.read_json(Path(run_dir) / "api-run.json")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_run_meta(run_dir: Path, meta: dict) -> None:
    _json(Path(run_dir) / "api-run.json", meta)


def update_run_meta(run_dir: Path, **fields) -> dict:
    meta = {**read_run_meta(run_dir), **fields}
    write_run_meta(run_dir, meta)
    return meta
