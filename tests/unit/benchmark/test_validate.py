"""Verification needs real fetched metadata; every other outcome stays unverified."""

from datetime import UTC, datetime

import httpx
import pytest

from experiment_design.agents.benchmark.acquire import acquire
from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.benchmark.validate import check_minimums, validate
from experiment_design.packs import load_pack

NOW = datetime(2026, 10, 14, 1, 2, 3, tzinfo=UTC)


def _resource(**kwargs) -> Resource:
    base = {
        "resource_id": "r1",
        "kind": "benchmark",
        "name": "Data",
        "origin": "registry",
        "hf_repo_id": "owner/data",
    }
    return Resource(**{**base, **kwargs})


def _run(resource: Resource, handler) -> Resource:
    client = HFClient(transport=httpx.MockTransport(handler))
    try:
        return validate(acquire(resource, client, lambda: NOW))
    finally:
        client.close()


def test_real_metadata_with_revision_is_verified():
    body = {
        "sha": "abc123",
        "cardData": {
            "license": "mit",
            "dataset_info": {"splits": [{"name": "train"}, {"name": "test"}]},
        },
        "usedStorage": 42,
    }
    out = _run(_resource(), lambda request: httpx.Response(200, json=body))
    assert out.status == "verified"
    assert out.revision == "abc123"
    assert out.license == "mit"
    assert out.splits == ["train", "test"]
    assert out.size_bytes == 42
    assert out.retrieved_at == "2026-10-14T01:02:03Z"
    assert out.source_url == "https://huggingface.co/datasets/owner/data"


def test_metadata_without_revision_is_unknown():
    out = _run(_resource(), lambda request: httpx.Response(200, json={"id": "owner/data"}))
    assert out.status == "unknown"
    assert out.revision is None
    assert out.reasons


def test_missing_repository_is_not_found():
    out = _run(_resource(), lambda request: httpx.Response(404))
    assert out.status == "not_found"


def test_unavailable_hub_is_unknown_with_reason():
    out = _run(_resource(), lambda request: httpx.Response(503))
    assert out.status == "unknown"
    assert any("503" in reason for reason in out.reasons)


def test_resource_without_hub_repository_is_unknown():
    out = _run(_resource(hf_repo_id=None), lambda request: pytest.fail("no request expected"))
    assert out.status == "unknown"
    assert out.revision is None


def test_baseline_uses_model_endpoint():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"sha": "m1"})

    out = _run(_resource(kind="baseline", hf_repo_id="owner/model"), handler)
    assert seen == ["/api/models/owner/model"]
    assert out.status == "verified"
    assert out.source_url == "https://huggingface.co/owner/model"


def test_baseline_with_source_url_is_cited_not_verified():
    resource = _resource(kind="baseline", hf_repo_id=None, source_url="https://github.com/org/repo")
    out = _run(resource, lambda request: pytest.fail("no request expected"))
    assert out.status == "cited"
    assert out.revision is None


def test_baseline_without_source_stays_unknown():
    out = _run(_resource(kind="baseline", hf_repo_id=None), lambda request: httpx.Response(500))
    assert out.status == "unknown"


def test_researcher_dataset_needs_sha256_and_version():
    complete = _resource(origin="researcher", hf_repo_id=None, sha256="a" * 64, revision="3")
    assert validate(complete).status == "verified"
    assert validate(complete.model_copy(update={"sha256": None})).status == "unknown"
    assert validate(complete.model_copy(update={"revision": None})).status == "unknown"


def test_preset_verified_status_is_not_trusted():
    claimed = _resource(status="verified", hf_repo_id=None)
    assert validate(claimed).status == "unknown"


@pytest.mark.parametrize(
    "status", [201, 204, 301, 302, 400, 401, 403, 404, 408, 429, 500, 502, 503]
)
@pytest.mark.parametrize("kind", ["dataset", "benchmark", "baseline", "metric"])
@pytest.mark.parametrize("body", [{}, {"sha": "abc"}, {"id": "x", "cardData": {"license": "mit"}}])
def test_no_non_200_response_is_ever_verified(status, kind, body):
    out = _run(_resource(kind=kind), lambda request: httpx.Response(status, json=body))
    assert out.status != "verified"


def test_minimums_count_verified_and_cited_only_and_never_invent():
    pack = load_pack(None)
    resources = [
        _resource(resource_id="b", status="verified", revision="x", retrieved_at="t"),
        _resource(resource_id="m1", kind="baseline", status="verified"),
        _resource(resource_id="m2", kind="baseline", status="unknown"),
    ]
    shortfalls = check_minimums(resources, pack)
    assert shortfalls == ["only 1 verified or cited baseline (minimum 2)"]
    assert len(resources) == 3


def test_minimums_report_missing_benchmark():
    shortfalls = check_minimums([], load_pack(None))
    assert shortfalls == [
        "only 0 verified benchmark (minimum 1)",
        "only 0 verified or cited baseline (minimum 2)",
    ]
