"""Metadata access must distinguish absence from an unavailable source."""

import httpx
import pytest

from experiment_design.agents.benchmark.hf import HFClient, HFUnavailable


@pytest.mark.parametrize("token", [None, "hf-test-secret"])
def test_metadata_request_auth_and_search(token):
    def response(request):
        assert request.headers.get("Authorization") == (f"Bearer {token}" if token else None)
        if request.url.path == "/api/datasets":
            assert request.url.params["search"] == "classification"
            return httpx.Response(200, json=[{"id": "owner/data"}])
        return httpx.Response(200, json={"sha": "revision"})

    client = HFClient(token=token, transport=httpx.MockTransport(response))
    assert client.search_datasets("classification") == [{"id": "owner/data"}]
    assert client.dataset_info("owner/data") == {"sha": "revision"}
    assert client.model_info("owner/model") == {"sha": "revision"}
    assert "hf-test-secret" not in repr(client)
    client.close()


def test_missing_repository_is_none():
    client = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    assert client.dataset_info("missing") is None
    client.close()


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_unavailable_metadata_is_not_an_empty_success(status):
    client = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
    with pytest.raises(HFUnavailable):
        client.dataset_info("owner/data")
    client.close()


def test_malformed_metadata_is_unavailable():
    client = HFClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    with pytest.raises(HFUnavailable):
        client.model_info("owner/model")
    client.close()


@pytest.mark.parametrize("first", ["network", 429, 503])
def test_one_transient_failure_is_retried(first):
    calls = []

    def response(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            if first == "network":
                raise httpx.ConnectError("dropped", request=request)
            return httpx.Response(first)
        return httpx.Response(200, json={"sha": "revision"})

    client = HFClient(transport=httpx.MockTransport(response))
    assert client.dataset_info("owner/data") == {"sha": "revision"}
    assert len(calls) == 2
    client.close()


def test_a_client_error_is_not_retried():
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(401)

    client = HFClient(transport=httpx.MockTransport(response))
    with pytest.raises(HFUnavailable):
        client.dataset_info("owner/data")
    assert len(calls) == 1
    client.close()
