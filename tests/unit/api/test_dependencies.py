import json
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "contract" / "vendor"


def test_vendored_contract_is_pinned_to_one_management_commit():
    source = (VENDOR / "SOURCE").read_text(encoding="utf-8").split()[0]
    assert len(source) == 40 and set(source) <= set("0123456789abcdef")
    assert (VENDOR / "README.md").is_file()
    assert json.loads((VENDOR / "schema" / "Capabilities.json").read_text(encoding="utf-8"))


def test_api_dependencies_available():
    import fastapi  # noqa: F401
    import jsonschema  # noqa: F401
    import uvicorn  # noqa: F401
