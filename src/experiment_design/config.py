"""Configuration contains provider settings and secret environment variable names."""

import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import Field, ValidationError

from experiment_design.contracts import Profile, Record

if TYPE_CHECKING:
    from experiment_design.api.contract_models import ModelConfig


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
    llm_tokens: int = Field(default=1_000_000, gt=0)
    max_refinements: int = Field(default=2, ge=0)
    task_max_turns: int = Field(default=12, gt=0)
    task_max_tokens: int = Field(default=120_000, gt=0)


class ExdConfig(Record):
    llm: LLMConfig
    review_llm: LLMConfig | None = None
    jev: JevConfig = Field(default_factory=JevConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    profile: Profile = Field(default_factory=Profile)
    interaction_mode: str = "copilot"
    runs_root: Path = Path("runs")


class ConfigError(ValueError):
    pass


_MODEL_OVERRIDES = ("timeout_sec", "max_retries", "temperature", "max_tokens")


def llm_config_from(model: "ModelConfig", base: LLMConfig) -> LLMConfig:
    """Provider settings from a run request; the API key stays out of the returned config."""
    if model.provider != "openai_compatible" or not model.base_url:
        raise ConfigError("unsupported model provider")
    values = base.model_dump() | {"base_url": model.base_url, "model": model.model}
    values |= {name: model.extra[name] for name in _MODEL_OVERRIDES if name in model.extra}
    try:
        return LLMConfig.model_validate(values)
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors())
        raise ConfigError(f"invalid model settings: {fields}") from None


def load_config(path: Path, *, require_provider_keys: bool = True) -> ExdConfig:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError(f"Invalid configuration YAML{location}") from None
    try:
        cfg = ExdConfig.model_validate(raw)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, error['loc']))}: {error['msg']}"
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        )
        raise ConfigError(
            "Invalid configuration (use runs_root and max_refinements; "
            "legacy store, max_actions, max_reviews and max_revisions are retired): " + details
        ) from None
    if require_provider_keys and not os.environ.get(cfg.llm.api_key_env):
        raise ConfigError(f"missing API key environment variable {cfg.llm.api_key_env}")
    return cfg
