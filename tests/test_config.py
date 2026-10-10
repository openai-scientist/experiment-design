from pathlib import Path

import pytest

from experiment_design.config import ConfigError, load_config


def test_example_config_loads(monkeypatch):
    monkeypatch.setenv("EXD_LLM_API_KEY", "test-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-judge")
    cfg = load_config(Path("exd.example.yaml"))
    assert cfg.run.llm_tokens == 300_000
    assert cfg.store == Path(".exd/exd.db")
    assert cfg.llm.max_tokens == 4096


def test_load_config_missing_key_env(monkeypatch):
    monkeypatch.delenv("EXD_LLM_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="EXD_LLM_API_KEY"):
        load_config(Path("exd.example.yaml"))
