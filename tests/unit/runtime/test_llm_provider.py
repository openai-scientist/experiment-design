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
        assert "tool_choice" not in body
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
        assert sleeps == [2.0, 5.0]
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


@pytest.mark.parametrize("field,value", [("prompt_tokens", -1), ("completion_tokens", -1)])
def test_negative_usage_is_rejected(field, value):
    data = response()
    data["usage"][field] = value
    llm = client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(LLMError, match="malformed"):
            llm.complete([], [])
    finally:
        llm.close()


def test_retry_attempt_telemetry_preserves_unknown_usage(monkeypatch):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json=response())

    llm = client(handler)
    try:
        llm.complete([], [])
        assert [a["status"] for a in llm.last_attempts] == ["error", "success"]
        assert llm.last_attempts[0]["tokens_in"] == 0
        assert llm.last_attempts[0]["tokens_out"] == 0
        assert llm.last_attempts[0]["tokens_cached"] == 0
        assert llm.last_attempts[0]["status_code"] == 429
        assert llm.last_attempts[1]["tokens_in"] == 11
        assert (
            llm.last_attempts[1]["tool_calls"] == response()["choices"][0]["message"]["tool_calls"]
        )
        llm.complete([], [])
        assert len(llm.last_attempts) == 1
    finally:
        llm.close()


def test_missing_usage_is_recorded_and_rejected():
    data = response()
    data.pop("usage")
    llm = client(lambda _: httpx.Response(200, json=data))
    try:
        with pytest.raises(LLMError):
            llm.complete([], [])
        assert llm.last_attempts[-1]["status"] == "error"
        assert llm.last_attempts[-1]["tokens_in"] is None
    finally:
        llm.close()


def test_llm_retry_attempts_can_be_capped(monkeypatch):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)
    calls = []

    def handler(_):
        calls.append(1)
        return httpx.Response(500)

    llm = client(handler)
    llm.max_attempts = 1
    try:
        with pytest.raises(LLMError):
            llm.complete([], [])
        assert len(calls) == 1
    finally:
        llm.close()


@pytest.mark.parametrize(
    "status,known",
    [
        (400, True),
        (401, True),
        (404, True),
        (408, False),
        (500, True),
        (502, True),
        (503, True),
        (504, False),
        (524, False),
    ],
)
def test_only_timeouts_have_unknown_usage(monkeypatch, status, known):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)
    llm = client(lambda _: httpx.Response(status), retries=0)
    try:
        with pytest.raises(LLMError):
            llm.complete([], [])
        attempt = llm.last_attempts[0]
        expected = 0 if known else None
        assert (attempt["tokens_in"], attempt["tokens_out"], attempt["tokens_cached"]) == (
            expected,
            expected,
            expected,
        )
    finally:
        llm.close()


def test_agent_request_requires_a_tool_call():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    tool = {"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}
    llm = client(handler)
    try:
        llm.complete([{"role": "user", "content": "inspect"}], [tool])
    finally:
        llm.close()
    assert bodies[0]["tool_choice"] == "required"


def test_dropped_connection_is_retried_with_unknown_usage(monkeypatch):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.RemoteProtocolError("peer closed connection", request=request)
        return httpx.Response(200, json=response())

    llm = client(handler)
    try:
        assert llm.complete([], []).tokens_out == 7
    finally:
        llm.close()
    assert [a["status"] for a in llm.last_attempts] == ["error", "success"]
    assert llm.last_attempts[0]["tokens_in"] is None
    assert llm.last_attempts[0]["error_type"] == "RemoteProtocolError"


@pytest.mark.parametrize("status", [500, 502, 503, 524])
def test_http_5xx_status_backs_off_to_about_a_minute(monkeypatch, status):
    sleeps, calls = [], []
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", sleeps.append)

    def handler(_):
        calls.append(1)
        return httpx.Response(status) if len(calls) < 5 else httpx.Response(200, json=response())

    llm = LLMClient(
        LLMConfig(base_url="https://provider.example/v1", model="tool-model"),
        "test-key",
        httpx.MockTransport(handler),
    )
    try:
        assert llm.complete([], []).tokens_out == 7
        errors = llm.last_attempts[:-1]
        assert len(errors) == 4
        usage = None if status == 524 else 0
        assert all(a["tokens_in"] == usage and a["tokens_out"] == usage for a in errors)
        assert sleeps == [2.0, 5.0, 15.0, 30.0]
    finally:
        llm.close()


def test_timeout_keeps_usage_unknown(monkeypatch):
    monkeypatch.setattr("experiment_design.runtime.llm.time.sleep", lambda _: None)

    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    llm = client(handler, retries=0)
    try:
        with pytest.raises(LLMError):
            llm.complete([], [])
        assert llm.last_attempts[0]["tokens_in"] is None
    finally:
        llm.close()
