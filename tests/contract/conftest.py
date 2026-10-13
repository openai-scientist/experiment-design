import json
from pathlib import Path

import pytest

VENDOR = Path(__file__).resolve().parent / "vendor"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def vendor() -> Path:
    return VENDOR
