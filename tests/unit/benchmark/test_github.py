"""GitHub-hosted resources are verified from the REST API; failures stay unverified."""

from datetime import UTC, datetime

import httpx

from experiment_design.agents.benchmark.acquire import acquire
from experiment_design.agents.benchmark.github import GitHubClient
from experiment_design.agents.benchmark.hf import HFClient
from experiment_design.agents.benchmark.models import Resource
from experiment_design.agents.benchmark.validate import validate

NOW = datetime(2026, 10, 14, 1, 2, 3, tzinfo=UTC)
REPO = {"default_branch": "main", "license": {"spdx_id": "MIT"}}


def _resource(url="https://github.com/o/r", **kwargs) -> Resource:
    base = {
        "resource_id": "r1",
        "kind": "benchmark",
        "name": "Bench",
        "origin": "registry",
        "source_url": url,
    }
    return Resource(**{**base, **kwargs})


def _run(resource: Resource, handler, token=None) -> Resource:
    github = GitHubClient(token=token, transport=httpx.MockTransport(handler))
    hf = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        return validate(acquire(resource, hf, lambda: NOW, github))
    finally:
        github.close()
        hf.close()


def test_latest_release_tag_is_resolved_to_its_commit_sha():
    def handler(request):
        path = request.url.path
        if path == "/repos/o/r":
            return httpx.Response(200, json=REPO)
        if path == "/repos/o/r/releases/latest":
            return httpx.Response(200, json={"tag_name": "v1.2"})
        assert path == "/repos/o/r/commits/v1.2"
        return httpx.Response(200, json={"sha": "f" * 40})

    out = _run(_resource("https://github.com/o/r/tree/main/data"), handler)
    assert out.status == "verified"
    assert out.revision == "f" * 40
    assert out.license == "MIT"
    assert out.retrieved_at == "2026-10-14T01:02:03Z"


def test_reserved_or_invalid_github_paths_are_not_repositories():
    def handler(request):
        raise AssertionError("no request expected")

    for url in (
        "https://github.com/orgs/acme",
        "https://github.com/../r",
        "https://github.com/o/..",
        "https://github.com/o%20x/r",
    ):
        out = _run(_resource(url, kind="baseline"), handler)
        assert out.status == "cited"
        assert out.revision is None


def test_no_release_falls_back_to_the_default_branch_commit():
    def handler(request):
        path = request.url.path
        if path == "/repos/o/r":
            return httpx.Response(200, json=REPO)
        if path == "/repos/o/r/releases/latest":
            return httpx.Response(404)
        assert path == "/repos/o/r/commits/main"
        return httpx.Response(200, json={"sha": "abc123"})

    out = _run(_resource(), handler)
    assert out.status == "verified"
    assert out.revision == "abc123"


def test_missing_repository_is_not_found():
    out = _run(_resource(), lambda request: httpx.Response(404))
    assert out.status == "not_found"
    assert not out.revision


def test_network_error_keeps_the_resource_unknown_with_a_reason():
    def handler(request):
        raise httpx.ConnectError("down")

    out = _run(_resource(), handler)
    assert out.status == "unknown"
    assert any("GitHub unavailable" in reason for reason in out.reasons)


def test_rate_limit_keeps_the_resource_unknown():
    out = _run(_resource(), lambda request: httpx.Response(403))
    assert out.status == "unknown"
    assert any("HTTP 403" in reason for reason in out.reasons)


def test_token_is_sent_and_hub_resources_are_left_to_the_hub():
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"tag_name": "t", "sha": "a", **REPO})

    _run(_resource(), handler, token="ghp-x")
    assert seen and set(seen) == {"Bearer ghp-x"}
    hub = _resource(hf_repo_id="o/data")
    out = _run(hub, lambda request: httpx.Response(500))
    assert "Hub unavailable" in out.reasons[0]


def test_unresolvable_tag_leaves_no_revision_and_says_github_gave_none():
    def handler(request):
        path = request.url.path
        if path == "/repos/o/r":
            return httpx.Response(200, json=REPO)
        if path == "/repos/o/r/releases/latest":
            return httpx.Response(200, json={"tag_name": "v1"})
        return httpx.Response(404)

    out = _run(_resource(), handler)
    assert out.status == "unknown"
    assert out.revision is None
    assert any("GitHub lookup gave no revision" in reason for reason in out.reasons)
    assert not any("Hub metadata" in reason or "Hub response" in reason for reason in out.reasons)
