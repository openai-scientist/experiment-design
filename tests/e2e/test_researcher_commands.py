import json
import os
import shlex
import shutil
import subprocess
import sys

import pytest
import yaml

from experiment_design import cli
from experiment_design.config import RunConfig
from experiment_design.contracts import DecisionOption, ResearcherDecision
from experiment_design.persistence.store import Store
from experiment_design.workflow import Deps, advance, start_run
from tests.support.providers import ScriptedJudge, ScriptedLLM
from tests.support.scenarios import normal_responses, source_snapshot


@pytest.fixture(scope="module")
def reviewed_run(tmp_path_factory):
    path = tmp_path_factory.mktemp("cli-reviewed") / "exd.db"
    store = Store(path)
    deps = Deps(
        store,
        ScriptedLLM(normal_responses()),
        ScriptedJudge([{"next_action": "REVIEW"}, {"next_action": "QUALIFY"}]),
        RunConfig(),
    )
    rid = start_run(source_snapshot(), deps, {})
    assert advance(rid, deps) == "REVIEWABLE"
    store.close()
    return path, rid


@pytest.fixture
def state(tmp_path, monkeypatch, reviewed_run):
    monkeypatch.chdir(tmp_path)
    source, rid = reviewed_run
    path = tmp_path / "exd.db"
    shutil.copyfile(source, path)
    cfg = tmp_path / "exd.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "llm": {"base_url": "https://provider.example/v1", "model": "test"},
                "store": str(path),
            }
        ),
        encoding="utf-8",
    )
    store = Store(path)
    yield store, rid, cfg
    store.close()


def call(state, args):
    return cli.main(["--config", str(state[2]), *args])


def scientific_decision(state):
    store, rid, _ = state
    final = store.decisions(rid)[-1]
    final.status = "APPLIED"
    store.save_decision(final)
    return store.save_decision(
        ResearcherDecision(
            id="D1",
            run_id=rid,
            binding=store.binding(rid),
            status="AWAITING_RESEARCHER",
            problem="Choose margin",
            affected_paths=["/claim/margin"],
            options=[DecisionOption(id="two", label="Two points", proposal={"margin": 2})],
        )
    )


def test_status_preserves_unicode_researcher_question_on_windows_output(state):
    decision = scientific_decision(state)
    decision.revision += 1
    decision.problem = "Confirm AI − teacher contrast for điểm toán"
    state[0].save_decision(decision)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "experiment_design.cli",
            "--config",
            str(state[2]),
            "status",
            state[1],
        ],
        env=os.environ | {"PYTHONIOENCODING": "cp1252"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Confirm AI − teacher contrast for điểm toán" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("custom_config", [False, True])
def test_status_decision_hint_is_parseable_without_provider_keys(state, capsys, custom_config):
    decision = scientific_decision(state)
    if custom_config:
        custom = state[2].parent / "custom config.yaml"
        custom.write_bytes(state[2].read_bytes())
        state = (*state[:2], custom)
    assert call(state, ["status", state[1]]) == 0
    line = next(
        line.strip()
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("exd ")
    )
    line = line.replace('"<your answer>"', '"Use 2.5 points"').replace('"<your name>"', '"PI"')
    assert cli.main(shlex.split(line)[1:]) == 0
    stored = state[0].decision(state[1], decision.id)
    assert stored.responses[-1].text == "Use 2.5 points"
    assert stored.application == {}
    assert state[0].version(state[1]).version == 1


@pytest.mark.parametrize(
    "revision,option,error", [(2, "two", "stale"), (1, "missing", "unknown option")]
)
def test_invalid_decision_does_not_mutate(state, capsys, revision, option, error):
    decision = scientific_decision(state)
    before = state[0].events(state[1])
    assert (
        call(
            state,
            [
                "decide",
                state[1],
                decision.id,
                "--revision",
                str(revision),
                "--action",
                "SELECT_OPTION",
                "--option",
                option,
                "--by",
                "PI",
            ],
        )
        == 2
    )
    assert error in capsys.readouterr().err
    assert state[0].events(state[1]) == before


def test_select_option_preserves_choice_without_provider(state):
    decision = scientific_decision(state)
    assert (
        call(
            state,
            [
                "decide",
                state[1],
                decision.id,
                "--revision",
                "1",
                "--action",
                "SELECT_OPTION",
                "--option",
                "two",
                "--by",
                "PI",
            ],
        )
        == 0
    )
    assert state[0].decision(state[1], decision.id).responses[-1].option_id == "two"


def test_legacy_answer_command_is_removed_without_mutation(state, capsys):
    before = state[0].events(state[1])
    with pytest.raises(SystemExit) as error:
        call(state, ["answer", state[1], "R9", "yes"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert state[0].events(state[1]) == before


def test_status_explains_budget_stop_from_journal(state, capsys):
    store, rid, _ = state
    store.append_event(
        rid,
        "budget.stop",
        {
            "scope": "task",
            "reason": "token_limit",
            "agent_id": "skeptic",
            "tokens_used": 63979,
            "tokens_limit": 60000,
            "llm_calls": 10,
        },
    )
    store.update_run(rid, status="BUDGET_STOP")
    assert call(state, ["status", rid]) == 0
    output = capsys.readouterr().out
    assert "S6" in output and "skeptic" in output and "token_limit" in output
    assert "63979/60000" in output


def test_approve_stale_version_exit_2(state, capsys):
    assert call(state, ["approve", state[1], "--version", "9", "--by", "PI"]) == 2
    assert "current version" in capsys.readouterr().err
    assert state[0].approvals(state[1]) == []


def test_approve_then_export_has_exact_approval_without_provider(state, tmp_path):
    store, rid, _ = state
    assert call(state, ["approve", rid, "--version", "1", "--by", "PI"]) == 0
    assert call(state, ["export", rid, "--out", str(tmp_path / "final")]) == 0
    data = json.loads((tmp_path / "final/exp_plan.json").read_bytes())
    assert data["approval"]["version"] == 1
    assert data["approval"]["design_hash"] == store.version(rid).design_hash
    assert store.run(rid).status == "PUBLISHED"


def test_unknown_run_exit_2(state, capsys):
    assert call(state, ["status", "runknown"]) == 2
    assert "unknown run" in capsys.readouterr().err


def test_unreadable_source_rejected_before_provider(state, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("EXD_LLM_API_KEY", "x")

    def forbidden(_):
        pytest.fail("Dependencies constructed before source intake")

    monkeypatch.setattr(cli, "build_deps", forbidden)
    assert call(state, ["run", str(tmp_path / "missing.md")]) == 2
    assert "missing.md" in capsys.readouterr().err


def test_explicit_resume_ceiling_is_persisted_without_resetting_usage(state, monkeypatch):
    monkeypatch.setenv("EXD_LLM_API_KEY", "x")
    observed = []

    def build(cfg):
        deps = Deps(Store(cfg.store), ScriptedLLM([]), ScriptedJudge([]), cfg.run)
        observed.append(deps)
        return deps

    monkeypatch.setattr(cli, "build_deps", build)
    before = state[0].usage(state[1]).llm_tokens
    assert (
        call(
            state,
            [
                "resume",
                state[1],
                "--llm-token-limit",
                "400000",
                "--reason",
                "PI authorizes more work",
            ],
        )
        == 0
    )
    assert state[0].usage(state[1]).llm_tokens == before
    event = [e for e in state[0].events(state[1]) if e.type == "budget.changed"][-1]
    assert event.data == {"old": 300000, "new": 400000, "reason": "PI authorizes more work"}
    assert call(state, ["resume", state[1]]) == 0
    assert observed[-1].run.llm_tokens == 400000


def test_request_changes_alias_uses_final_decision_without_provider(state):
    store, run, _ = state
    final = store.decisions(run)[-1]
    assert (
        call(
            state,
            [
                "request-changes",
                run,
                "Clarify assignment",
                "--path",
                "/sampling/randomization",
                "--revision",
                str(final.revision),
                "--by",
                "PI",
            ],
        )
        == 0
    )
    recorded = store.decision(run, final.id)
    assert recorded.responses[-1].action == "REQUEST_CHANGES"
    assert recorded.responses[-1].text == "Clarify assignment"
    assert recorded.responses[-1].researcher == "PI"
    assert store.version(run).version == 1
