from pathlib import Path

import pytest

from experiment_design.config import ConfigError, RunConfig, load_config


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("EXD_LLM_API_KEY", "test-key")
    cfg = load_config(Path("exd.example.yaml"))
    assert cfg.run.llm_tokens == 1_000_000
    assert cfg.runs_root == Path("runs")
    assert cfg.run.task_max_turns == 12
    assert cfg.run.task_max_tokens == 120_000
    assert RunConfig().model_dump() == cfg.run.model_dump()
    assert cfg.llm.max_tokens == 4096


def test_load_config_missing_key_env(monkeypatch, tmp_path):
    monkeypatch.delenv("EXD_LLM_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  base_url: https://example.test\n  model: test\n")
    with pytest.raises(ConfigError, match="EXD_LLM_API_KEY"):
        load_config(path)


@pytest.mark.parametrize(
    "payload",
    [
        "llm:\n  base_url: https://example.test\n  model: test\n  api_key: literal-secret-123\n",
        "llm: [literal-secret-123\n",
        "llm:\n  base_url: https://example.test\n  model: test\nstore: literal-secret-123\n",
    ],
)
def test_bad_configuration_diagnostics_do_not_expose_values(tmp_path, payload):
    path = tmp_path / "config.yaml"
    path.write_text(payload)
    with pytest.raises(ConfigError) as exc:
        load_config(path, require_provider_keys=False)
    assert "literal-secret-123" not in str(exc.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("llm_tokens", 0),
        ("jev_max_calls", 0),
        ("task_max_tokens", 0),
        ("task_max_turns", 0),
        ("max_refinements", -1),
    ],
)
def test_invalid_budgets_rejected(field, value):
    with pytest.raises(ValueError):
        RunConfig(**{field: value})
