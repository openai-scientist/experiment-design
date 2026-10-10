"""OpenAI-compatible synchronous tool-calling client with bounded retries."""

import time
from typing import Self

import httpx
from pydantic import Field, ValidationError, model_validator

from experiment_design.config import LLMConfig
from experiment_design.contracts import Record


class ToolCall(Record):
    id: str
    name: str
    arguments: str


class LLMResponse(Record):
    content: str | None
    tool_calls: list[ToolCall]
    tokens_in: int
    tokens_out: int
    tokens_cached: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_cache_usage(self) -> Self:
        if self.tokens_cached is not None and self.tokens_cached > self.tokens_in:
            raise ValueError("Cached input tokens cannot exceed total input tokens")
        return self


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        cfg: LLMConfig,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self.cfg = cfg
        self.client = httpx.Client(
            timeout=cfg.timeout_sec,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self.client.close()

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        for attempt in range(self.cfg.max_retries + 1):
            try:
                response = self.client.post(
                    self.cfg.base_url.rstrip("/") + "/chat/completions",
                    json={
                        "model": self.cfg.model,
                        "messages": messages,
                        "tools": tools,
                        "temperature": self.cfg.temperature,
                        "max_tokens": self.cfg.max_tokens,
                    },
                )
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TimeoutException) or (
                    exc.response.status_code == 429 or exc.response.status_code >= 500
                )
                if not retryable or attempt == self.cfg.max_retries:
                    raise LLMError(f"LLM request failed: {exc}") from exc
                time.sleep(self.cfg.retry_base_delay * 2**attempt)
                continue
            except httpx.RequestError as exc:
                raise LLMError(f"LLM connection failed: {exc}") from exc
            try:
                body = response.json()
                message = body["choices"][0]["message"]
                return LLMResponse(
                    content=message.get("content"),
                    tool_calls=[
                        ToolCall(
                            id=t["id"],
                            name=t["function"]["name"],
                            arguments=t["function"]["arguments"],
                        )
                        for t in message.get("tool_calls", []) or []
                    ],
                    tokens_in=body["usage"]["prompt_tokens"],
                    tokens_out=body["usage"]["completion_tokens"],
                    tokens_cached=(body["usage"].get("prompt_tokens_details") or {}).get(
                        "cached_tokens"
                    ),
                )
            except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                raise LLMError(f"malformed LLM response: {exc}") from exc
        raise LLMError("LLM retry budget exhausted")
