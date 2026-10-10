"""Configuration contains provider settings and secret environment variable names."""

import os
from pathlib import Path

import yaml
from pydantic import Field, ValidationError

from experiment_design.contracts import Record


class LLMConfig(Record):
    base_url: str
    model: str
    api_key_env: str = "EXD_LLM_API_KEY"
    timeout_sec: float = Field(default=120, gt=0)
    max_retries: int = Field(default=3, ge=0)
    retry_base_delay: float = Field(default=2.0, ge=0)
    temperature: float = 0.2
    max_tokens: int = Field(default=4096, gt=0)


class JevConfig(Record):
    api_key_env: str = "TYPESAFE_API_KEY"
    model: str | None = None


class RunConfig(Record):
    jev_max_calls: int = Field(default=24, gt=0)
    llm_tokens: int = Field(default=300_000, gt=0)
    max_actions: int = Field(default=12, gt=0)
    max_reviews: int = Field(default=2, ge=0)
    max_revisions: int = Field(default=3, ge=0)
    task_max_turns: int = Field(default=15, gt=0)
    task_max_tokens: int = Field(default=60_000, gt=0)


class ExdConfig(Record):
    llm: LLMConfig
    jev: JevConfig = Field(default_factory=JevConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    store: Path = Path(".exd/exd.db")


class ConfigError(ValueError):
    pass


def load_config(path: Path, *, require_provider_keys: bool = True) -> ExdConfig:
    try:
        cfg = ExdConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc
    if require_provider_keys and not os.environ.get(cfg.llm.api_key_env):
        raise ConfigError(f"missing API key environment variable {cfg.llm.api_key_env}")
    return cfg
