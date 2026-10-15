"""Zenodo-hosted resources are verified from the records API; failures stay unverified."""

from datetime import UTC, datetime

import httpx
import pytest

from experiment_design.agents.benchmark.acquire import acquire
from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.benchmark.validate import validate
from experiment_design.agents.benchmark.zenodo import ZenodoClient
from experiment_design.contracts import Profile
from experiment_design.packs import load_pack

NOW = datetime(2026, 10, 15, 1, 2, 3, tzinfo=UTC)
RECORD = {
    "id": 2535967,
    "doi": "10.5281/zenodo.2535967",
    "metadata": {"license": {"id": "cc-by-4.0"}, "version": "v1"},
    "files": [
        {"key": "CIFAR-10-C.tar", "checksum": "md5:56bf"},
        {"key": "CIFAR-10-P.tar", "checksum": "md5:125d"},
    ],
}


def _resource(url: str) -> Resource:
    return Resource(
        resource_id="r1", kind="benchmark", name="CIFAR-10-C", origin="registry", source_url=url
    )


def _run(resource: Resource, handler) -> Resource:
    zenodo = ZenodoClient(transport=httpx.MockTransport(handler))
    hf = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        return validate(acquire(resource, hf, lambda: NOW, None, zenodo))
    finally:
        zenodo.close()
        hf.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://doi.org/10.5281/zenodo.2535967",
        "https://zenodo.org/records/2535967",
        "https://zenodo.org/record/2535967#.Xabc",
        "doi:10.5281/ZENODO.2535967",
    ],
)
def test_a_zenodo_doi_or_url_is_verified_from_the_record(url):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json=RECORD)

    out = _run(_resource(url), handler)
    assert paths == ["/api/records/2535967"]
    assert out.status == "verified"
    assert out.revision == "zenodo:2535967 (doi 10.5281/zenodo.2535967, version v1)"
    assert out.license == "cc-by-4.0"
    assert out.retrieved_at == "2026-10-15T01:02:03Z"
    assert out.source_url == "https://zenodo.org/records/2535967"
    assert out.reasons == [
        "Zenodo file checksums: CIFAR-10-C.tar md5:56bf; CIFAR-10-P.tar md5:125d"
    ]


def test_a_missing_record_is_not_found():
    out = _run(_resource("https://zenodo.org/records/1"), lambda request: httpx.Response(404))
    assert out.status == "not_found"
    assert any("not found" in reason for reason in out.reasons)


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_an_unavailable_zenodo_leaves_the_resource_unverified(status):
    out = _run(_resource("https://zenodo.org/records/1"), lambda request: httpx.Response(status))
    assert out.status == "unknown" and out.revision is None
    assert any(f"HTTP {status}" in reason for reason in out.reasons)


def test_without_a_client_the_resource_is_left_as_it_was():
    hf = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        resource = _resource("https://zenodo.org/records/1")
        assert acquire(resource, hf, lambda: NOW) == resource
    finally:
        hf.close()


def test_the_ml_pack_registry_lists_cifar_10_c_on_zenodo():
    entries = {e.name: e for e in load_pack(Profile(domain="ml")).registry}
    assert entries["CIFAR-10-C"].source_url == "https://zenodo.org/records/2535967"
