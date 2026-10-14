"""Surveying and selecting never lose researcher data nor accept resources the pool lacks."""

import json

import httpx

from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.select import select
from experiment_design.agents.benchmark.survey import researcher_resources, survey
from experiment_design.config import RunConfig
from experiment_design.contracts import Profile
from experiment_design.packs import load_pack
from experiment_design.persistence.store import RunStore
from tests.support.providers import ToolProvider

DESCRIPTOR = {
    "dataset_version_id": "v1",
    "version_number": 3,
    "original_filename": "my-data.csv",
    "sha256": "a" * 64,
    "row_count": 10,
    "source_type": "upload",
    "download_url": "https://signed.example/secret-token",
}


def _store(tmp_path, evidence="Prior work compares against BaselineX (https://ex.test/bx).\n"):
    hypothesis = tmp_path / "hypothesis.md"
    hypothesis.write_text("## H1: a claim\n", encoding="utf-8")
    card = tmp_path / "evidence.md"
    card.write_text(evidence, encoding="utf-8")
    descriptor = tmp_path / "dataset-abc.json"
    descriptor.write_text(json.dumps(DESCRIPTOR), encoding="utf-8")
    return RunStore.create(
        tmp_path / "runs", [hypothesis, card, descriptor], {"run": RunConfig().model_dump()}, {}
    )


def _hf(handler):
    return HFClient(transport=httpx.MockTransport(handler))


def _hub(request):
    return httpx.Response(200, json=[{"id": "owner/found"}])


PLAN = {
    "queries": ["sentiment"],
    "tasks": ["sentiment"],
    "mentions": [
        {"name": "BaselineX", "kind": "baseline", "source_url": "https://ex.test/bx"},
        {"name": "Invented", "kind": "baseline", "source_url": "https://made.up/x"},
    ],
}


def test_researcher_descriptor_becomes_resource_without_download_url(tmp_path):
    store = _store(tmp_path)
    found = researcher_resources(store.sources())
    assert [r.name for r in found] == ["my-data.csv"]
    assert found[0].origin == "researcher"
    assert found[0].revision == "3"
    assert found[0].sha256 == "a" * 64
    assert "secret-token" not in found[0].model_dump_json()


def test_survey_collects_every_origin_and_quotes_only_visible_urls(tmp_path):
    store = _store(tmp_path)
    pack = load_pack(Profile(domain="ml"))
    found = survey(
        store,
        ToolProvider([("submit", PLAN)]),
        RunConfig(),
        pack,
        _hf(_hub),
        "brief",
        "## H1: a claim",
        researcher_resources(store.sources()),
    )
    by_name = {r.name: r for r in found}
    assert by_name["my-data.csv"].origin == "researcher"
    assert by_name["SST-2"].origin == "registry"
    assert "GLUE" not in by_name  # tasks filter: GLUE is not tagged sentiment
    assert by_name["owner/found"].origin == "hf_hub"
    assert by_name["BaselineX"].source_url == "https://ex.test/bx"
    assert by_name["Invented"].source_url is None
    assert len({r.resource_id for r in found}) == len(found)


def test_mention_without_url_is_kept_without_url(tmp_path):
    store = _store(tmp_path)
    plan = PLAN | {"mentions": [{"name": "NoLink", "kind": "baseline"}]}
    found = survey(
        store,
        ToolProvider([("submit", plan)]),
        RunConfig(),
        load_pack(Profile(domain="ml")),
        _hf(_hub),
        "brief",
        "## H1: a claim",
        [],
    )
    assert {r.name: r.source_url for r in found}["NoLink"] is None


def test_hub_failure_still_returns_registry_and_researcher(tmp_path):
    store = _store(tmp_path)
    found = survey(
        store,
        ToolProvider([("submit", PLAN)]),
        RunConfig(),
        load_pack(Profile(domain="ml")),
        _hf(lambda request: httpx.Response(503)),
        "brief",
        "## H1",
        researcher_resources(store.sources()),
    )
    names = {r.name for r in found}
    assert {"my-data.csv", "SST-2"} <= names
    assert not any(r.origin == "hf_hub" for r in found)
    assert any(e["kind"] == "benchmark.hub_unavailable" for e in store.events())


def _candidates(tmp_path):
    store = _store(tmp_path)
    pack = load_pack(Profile(domain="ml"))
    found = survey(
        store,
        ToolProvider([("submit", PLAN)]),
        RunConfig(),
        pack,
        _hf(_hub),
        "brief",
        "## H1",
        researcher_resources(store.sources()),
    )
    return store, pack, found


def test_select_keeps_researcher_dataset_and_ignores_unknown_ids(tmp_path):
    store, pack, found = _candidates(tmp_path)
    sst2 = next(r for r in found if r.name == "SST-2")
    chosen = select(
        store,
        ToolProvider(
            [
                (
                    "submit",
                    {
                        "choices": [
                            {
                                "resource_id": sst2.resource_id,
                                "rank": 1,
                                "rationale": "standard",
                                "hypothesis_ids": ["H1"],
                                "contamination_risk": "medium",
                            },
                            {"resource_id": "benchmark:made-up", "rank": 2},
                        ]
                    },
                )
            ]
        ),
        RunConfig(),
        pack,
        found,
        "brief",
    )
    names = [r.name for r in chosen]
    assert names == ["SST-2", "my-data.csv"]
    assert chosen[0].rank == 1
    assert chosen[0].contamination_risk == "medium"
    assert chosen[0].status == "unknown"
    assert any(e["kind"] == "benchmark.unknown_resource" for e in store.events())


def test_comment_requesting_baseline_adds_unknown_researcher_resource(tmp_path):
    store, pack, found = _candidates(tmp_path)
    submit = {
        "choices": [],
        "additions": [{"name": "X", "kind": "baseline"}],
    }
    chosen = select(
        store,
        ToolProvider([("submit", submit)]),
        RunConfig(),
        pack,
        found,
        "brief",
        comments="thêm baseline X",
    )
    added = next(r for r in chosen if r.name == "X")
    assert (added.origin, added.kind, added.status) == ("researcher", "baseline", "unknown")


def test_additions_are_ignored_without_researcher_comment(tmp_path):
    store, pack, found = _candidates(tmp_path)
    submit = {"choices": [], "additions": [{"name": "X", "kind": "baseline"}]}
    chosen = select(store, ToolProvider([("submit", submit)]), RunConfig(), pack, found, "brief")
    assert not any(r.name == "X" for r in chosen)
