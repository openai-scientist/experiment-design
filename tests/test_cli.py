import json
import shlex
from pathlib import Path

import pytest
import yaml
from conftest import design, package
from fakes import ScriptedJudge, ScriptedLLM, submit

from experiment_design import cli
from experiment_design.config import RunConfig
from experiment_design.contracts import Claim, ClaimField, ResearcherRequest
from experiment_design.runtime.store import Store
from experiment_design.workflow import Deps

EXAMPLE = Path(__file__).resolve().parents[1] / "hypothesis.example.md"


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("EXD_LLM_API_KEY", "x")
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "exd.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "llm": {"base_url": "https://provider.example/v1", "model": "test"},
                "store": str(tmp_path / "exd.db"),
            }
        ),
        encoding="utf-8",
    )
    store = Store(tmp_path / "exd.db")
    rid = store.create_run(package(), {})
    store.add_version(rid, design(), author="design", parent=None)
    store.update_run(rid, status="NEEDS_RESEARCHER")
    req = store.save_request(
        ResearcherRequest(
            run_id=rid,
            kind="input",
            question="Confirm district data?",
            status="open",
            raised_by="design",
        )
    )
    yield store, rid, req, cfg
    store.close()


def call(state, args):
    return cli.main(["--config", str(state[3]), *args])


@pytest.mark.parametrize("custom_config", [False, True])
def test_status_answer_hint_is_parseable(state, capsys, custom_config):
    if custom_config:
        custom = state[3].parent / "custom config.yaml"
        custom.write_bytes(state[3].read_bytes())
        default = yaml.safe_load(state[3].read_text(encoding="utf-8"))
        default["store"] = str(state[3].parent / "other.db")
        state[3].write_text(yaml.safe_dump(default), encoding="utf-8")
        state = (*state[:3], custom)
    assert call(state, ["status", state[1]]) == 0
    line = next(
        line.strip()
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("exd ")
    )
    assert cli.main(shlex.split(line.replace('"<your answer>"', '"yes"'))[1:]) == 0
    assert state[0].requests(state[1])[0].answer == "yes"


def test_answer_unknown_request_exit_2(state, capsys):
    before = state[0].events(state[1])
    assert call(state, ["answer", state[1], "R9", "yes"]) == 2
    assert "unknown request R9" in capsys.readouterr().err
    assert state[0].events(state[1]) == before


def test_answer_twice_exit_2(state, capsys):
    assert call(state, ["answer", state[1], state[2].id, "yes"]) == 0
    before = state[0].events(state[1])
    assert call(state, ["answer", state[1], state[2].id, "no"]) == 2
    assert "already answered" in capsys.readouterr().err
    assert state[0].events(state[1]) == before


def test_approve_stale_version_exit_2(state, capsys):
    store, rid, _, _ = state
    store.add_version(rid, design(), author="design", parent=1)
    store.update_run(rid, status="REVIEWABLE")
    assert call(state, ["approve", rid, "--version", "1", "--by", "PI"]) == 2
    assert "latest version" in capsys.readouterr().err
    assert store.approvals(rid) == []


def test_approve_then_export_has_approval(state, tmp_path):
    store, rid, _, _ = state
    store.add_version(rid, design(), author="design", parent=1)
    store.update_run(rid, status="REVIEWABLE")
    assert call(state, ["approve", rid, "--version", "2", "--by", "PI"]) == 0
    assert call(state, ["export", rid, "--out", str(tmp_path / "final")]) == 0
    data = json.loads((tmp_path / "final/experiment_design.json").read_bytes())
    assert data["provenance"]["approval"]["version"] == 2
    assert data["provenance"]["approval"]["design_hash"] == store.version(rid).design_hash
    assert "Approved by PI" in (tmp_path / "final/experiment_design.md").read_text(encoding="utf-8")


def test_unknown_run_exit_2(state, capsys):
    assert call(state, ["status", "runknown"]) == 2
    assert "unknown run" in capsys.readouterr().err


def test_run_with_scripted_deps(state, monkeypatch, capsys):
    d = design()
    d.claim = Claim(
        estimand=ClaimField(value="ITT difference in class means", origin="designer_default")
    )
    d.sampling.cluster.icc.provenance = "assumption"
    d.sampling.cluster.icc.source_ref = None
    scripted = Deps(
        Store(state[3].parent / "exd.db"),
        ScriptedLLM(
            [
                submit(
                    "submit_design",
                    {
                        "design": d.model_dump(mode="json"),
                        "rationale": "Draft",
                    },
                )
            ]
        ),
        ScriptedJudge([{"next_action": "STOP"}]),
        RunConfig(),
    )
    monkeypatch.setattr(cli, "build_deps", lambda _: scripted)
    assert call(state, ["run", str(EXAMPLE)]) == 0
    assert "run r" in capsys.readouterr().out


def test_run_bad_hypothesis_file_exit_2(state, monkeypatch, tmp_path, capsys):
    bad = tmp_path / "bad.md"
    bad.write_text("# Question\n## Hypothesis\nStatement\n## Outcomes\n", encoding="utf-8")

    def forbidden(_):
        pytest.fail("Dependencies constructed for malformed input")

    monkeypatch.setattr(cli, "build_deps", forbidden)
    assert call(state, ["run", str(bad)]) == 2
    assert "line 4" in capsys.readouterr().err
