import json

import httpx
import pytest

from experiment_design.api.contract_models import ModelConfig
from experiment_design.config import ConfigError, ExdConfig, JevConfig, LLMConfig, llm_config_from
from experiment_design.runtime.jev import TypeSafeJudge
from experiment_design.runtime.llm import LLMClient, LLMError


def _cfg(**kwargs):
    return LLMConfig(base_url="http://x/v1", model="m", max_retries=1, retry_base_delay=0, **kwargs)


def _completion(tokens_in, tokens_out):
    message = {
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "submit", "arguments": "{}"}}
        ],
    }
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
    }


def test_llm_config_from_request_model():
    base = LLMConfig(base_url="http://base", model="base-m", temperature=0.2)
    model = ModelConfig(
        provider="openai_compatible",
        base_url="http://x",
        model="m1",
        api_key="sk-x",
        extra={"temperature": 0.0, "max_tokens": 100, "ignored": 1},
    )
    cfg = llm_config_from(model, base)
    assert (cfg.base_url, cfg.model, cfg.temperature, cfg.max_tokens) == (
        "http://x",
        "m1",
        0.0,
        100,
    )
    assert "sk-x" not in cfg.model_dump_json()


@pytest.mark.parametrize(
    "model",
    [
        ModelConfig(provider="bedrock", model="m"),
        ModelConfig(provider="openai_compatible", model="m"),
    ],
)
def test_llm_config_rejects_unsupported_provider(model):
    with pytest.raises(ConfigError, match="unsupported model provider"):
        llm_config_from(model, LLMConfig(base_url="h", model="b"))


def test_llm_config_rejects_invalid_overrides():
    model = ModelConfig(
        provider="openai_compatible", base_url="http://x", model="m", extra={"max_tokens": -1}
    )
    with pytest.raises(ConfigError, match="max_tokens"):
        llm_config_from(model, LLMConfig(base_url="h", model="b"))


def test_exd_config_carries_review_model_and_profile():
    cfg = ExdConfig(llm=_cfg(), review_llm=_cfg(), profile={"domain": "ml"})
    assert cfg.profile.domain == "ml" and cfg.review_llm is not None
    assert ExdConfig(llm=_cfg()).review_llm is None


def test_on_call_reports_usage_without_secret():
    seen = []
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=_completion(7, 3)))
    client = LLMClient(_cfg(), "sk-secret", transport, role="review", on_call=seen.append)
    client.complete([{"role": "user", "content": "private prompt"}], [])
    assert seen[0]["role"] == "review" and seen[0]["tokens_in"] == 7 and seen[0]["outcome"] == "ok"
    assert seen[0]["model"] == "m" and seen[0]["latency_ms"] >= 0
    assert "sk-secret" not in json.dumps(seen) and "private prompt" not in json.dumps(seen)


def test_on_call_reports_every_failed_attempt():
    seen = []

    def handler(request):
        if len(seen) == 0:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=_completion(1, 1))

    client = LLMClient(_cfg(), "k", httpx.MockTransport(handler), on_call=seen.append)
    client.complete([{"role": "user", "content": "x"}], [])
    assert [c["outcome"] for c in seen] == ["timeout", "ok"]
    assert seen[0]["tokens_in"] is None


def test_on_call_reports_rejected_request_and_malformed_body():
    seen = []
    client = LLMClient(
        _cfg(), "k", httpx.MockTransport(lambda r: httpx.Response(401)), on_call=seen.append
    )
    with pytest.raises(LLMError):
        client.complete([{"role": "user", "content": "x"}], [])
    assert seen[-1]["outcome"] == "error" and seen[-1]["tokens_in"] == 0
    seen.clear()
    bad = LLMClient(
        _cfg(),
        "k",
        httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []})),
        on_call=seen.append,
    )
    with pytest.raises(LLMError):
        bad.complete([{"role": "user", "content": "x"}], [])
    assert seen[-1]["outcome"] == "error" and seen[-1]["tokens_in"] is None


def test_judge_uses_given_key_without_env(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    judge = TypeSafeJudge(JevConfig(), api_key="k-1")
    assert judge.client is not None


def test_judge_still_requires_a_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ConfigError):
        TypeSafeJudge(JevConfig())
