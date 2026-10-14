"""The resources step runs the four roles in order and never fails on a shortfall."""

import json

import httpx

from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.models import ResourcesDoc
from experiment_design.agents.benchmark.step import run_resources
from experiment_design.config import RunConfig
from experiment_design.contracts import Profile
from experiment_design.packs import load_pack
from tests.support.providers import ToolProvider
from tests.unit.benchmark.test_survey_select import _store

PLAN = {"queries": [], "tasks": [], "mentions": []}


def _hub(request):
    return httpx.Response(200, json={"sha": "rev1", "cardData": {"license": "mit"}})


def _run(tmp_path, selection, handler=_hub, comments=""):
    store = _store(tmp_path)
    store.write(2, "research_brief.md", "Brief\n")
    pack = load_pack(Profile(domain="ml"))
    client = HFClient(transport=httpx.MockTransport(handler))
    doc = run_resources(
        store,
        ToolProvider([("submit", PLAN), ("submit", selection)]),
        RunConfig(),
        pack,
        client,
        comments=comments,
    )
    return store, doc


SELECT_SST2 = {"choices": [{"resource_id": "benchmark:sst-2", "rank": 1}]}


def test_full_run_writes_parseable_files_and_four_events(tmp_path):
    store, doc = _run(tmp_path, SELECT_SST2)
    stored = ResourcesDoc.model_validate_json(store.read(3, "resources.json"))
    assert stored == doc
    assert json.loads(store.read(3, "resources.json"))["schema"] == "exd.resources/v1"
    events = [e["data"] for e in store.events() if e["kind"] == "activity"]
    assert [e["type"] for e in events] == [
        "exd.resources.surveyed",
        "exd.resources.selected",
        "exd.resources.acquired",
        "exd.resources.validated",
    ]
    assert all(e["step_key"] == "s2b_resources" for e in events)
    by_name = {r.name: r for r in doc.resources}
    assert by_name["SST-2"].status == "verified"
    assert by_name["SST-2"].revision == "rev1"
    assert by_name["my-data.csv"].status == "verified"
    assert "SST-2" in store.read(3, "resources.md")


def test_shortfall_is_reported_without_error(tmp_path):
    _, doc = _run(tmp_path, SELECT_SST2)
    assert doc.shortfalls == ["only 0 verified or cited baseline (minimum 2)"]


def test_unavailable_hub_leaves_resources_unverified(tmp_path):
    store, doc = _run(tmp_path, SELECT_SST2, handler=lambda request: httpx.Response(503))
    assert next(r for r in doc.resources if r.name == "SST-2").status == "unknown"
    assert any("benchmark" in item for item in doc.shortfalls)
    assert "Limitations" in store.read(3, "resources.md")


def test_comment_added_baseline_is_listed_unknown(tmp_path):
    selection = {**SELECT_SST2, "additions": [{"name": "X", "kind": "baseline"}]}
    _, doc = _run(tmp_path, selection, comments="add baseline X")
    added = next(r for r in doc.resources if r.name == "X")
    assert added.status == "unknown"
