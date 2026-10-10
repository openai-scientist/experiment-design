import json

import httpx
import pytest

from experiment_design.config import LLMConfig
from experiment_design.runtime.llm import LLMClient, LLMError


def response():
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call1",
                            "type": "function",
                            "function": {
                                "name": "read_design",
                                "arguments": '{"path":"/claim"}',
                            },
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


def client(handler, retries=3):
    return LLMClient(
        LLMConfig(base_url="https://provider.example/v1", model="tool-model", max_retries=retries),
        "test-key",
        httpx.MockTransport(handler),
    )


@pytest.mark.parametrize("cached", [None, 0, 9, 11])
def test_complete_parses_tool_calls(cached):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "tool-model"
        assert body["messages"] == [{"role": "user", "content": "inspect"}]
        data = response()
        if cached is not None:
            data["usage"]["prompt_tokens_details"] = {"cached_tokens": cached}
        return httpx.Response(200, json=data)

    llm = client(handler)
    try:
        r = llm.complete([{"role": "user", "content": "inspect"}], [])
        assert (r.tool_calls[0].name, r.tool_calls[0].arguments) == (
            "read_design",
            '{"path":"/claim"}',
        )
        assert (r.tokens_in, r.tokens_out) == (11, 7)
        assert r.tokens_cached == cached
        assert llm.complete([{"role": "user", "content": "inspect"}], []).tokens_cached == cached
        assert len(requests) == 2
    finally:
        llm.close()


@pytest.mark.parametrize("cached", [-1, 12])
def test_invalid_cached_token_accounting_is_rejected(cached):
    data = response()
    data["usage"]["prompt_tokens_details"] = {"cached_tokens": cached}
    llm = client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(LLMError, match="malformed LLM response"):
            llm.complete([], [])
    finally:
        llm.close()


def test_complete_retries_429_then_succeeds(monkeypatch):
    sleeps, requests = [], []
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", sleeps.append)

    def handler(request):
        requests.append(request)
        return httpx.Response(429) if len(requests) < 3 else httpx.Response(200, json=response())

    llm = client(handler)
    try:
        assert llm.complete([], []).tokens_out == 7
        assert len(requests) == 3
        assert sleeps == [2.0, 4.0]
    finally:
        llm.close()


def test_complete_raises_after_max_retries(monkeypatch):
    requests = []
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    llm = client(handler, retries=2)
    try:
        with pytest.raises(LLMError, match="500"):
            llm.complete([], [])
        assert len(requests) == 3
    finally:
        llm.close()
