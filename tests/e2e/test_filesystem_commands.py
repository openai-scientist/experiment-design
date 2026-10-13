import hashlib
import json

import pytest

from experiment_design import cli
from experiment_design.config import RunConfig
from experiment_design.persistence.store import RunStore
from tests.support.providers import Provider


def make_run(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_bytes(b"# Hypothesis\r\nExact input.\r\n")
    store = RunStore.create(tmp_path / "runs", [source], {}, {})
    plan = "# Plan\r\nKeep exact reviewed bytes.\r\n"
    store.write(3, "draft_plan.md", plan)
    store.complete(3, inputs=[(1, "hypothesis.md")], outputs=["draft_plan.md"])
    store.update(
        plan_stage=3, plan_name="draft_plan.md", plan_hash=hashlib.sha256(plan.encode()).hexdigest()
    )
    return store, plan


def test_offline_commands_need_no_config_or_keys(tmp_path, capsys):
    store, plan = make_run(tmp_path)
    assert cli.main(["status", str(store.path)]) == 0
    assert "pending" in capsys.readouterr().out
    assert cli.main(["review", str(store.path)]) == 0
    assert "Keep exact reviewed bytes." in capsys.readouterr().out
    destination = tmp_path / "published"
    assert cli.main(["export", str(store.path), "--out", str(destination), "--allow-draft"]) == 0
    assert (destination / "exp_plan.md").read_bytes() == plan.encode()
    assert json.loads((destination / "publication.json").read_text())["approval"] == "pending"


def test_decide_forwards_actual_identity_text_and_revision(tmp_path, monkeypatch):
    store, _ = make_run(tmp_path)
    replies = []

    def respond(run, **kwargs):
        replies.append(kwargs)
        return run.state

    monkeypatch.setattr(cli, "respond", respond)
    assert (
        cli.main(
            [
                "decide",
                str(store.path),
                "--action",
                "CLARIFY",
                "--text",
                "Why?",
                "--by",
                "Researcher",
                "--revision",
                "1",
            ]
        )
        == 0
    )
    assert replies == [
        {
            "action": "CLARIFY",
            "text": "Why?",
            "researcher": "Researcher",
            "revision": 1,
            "option": None,
        }
    ]


def test_sqlite_path_is_clear_error(tmp_path, capsys):
    old = tmp_path / "historical.db"
    old.write_bytes(b"SQLite format 3\0")
    assert cli.main(["status", str(old)]) == 1
    assert "SQLite" in capsys.readouterr().err


def test_budget_increase_preserves_usage_and_requires_reason(tmp_path):
    store, _ = make_run(tmp_path)
    store.update(llm_tokens=100, llm_token_limit=200)
    cli.raise_budget(store, 300, "Researcher authorized more allowance")
    restored = RunStore(store.path)
    assert restored.state.llm_tokens == 100
    assert restored.state.llm_token_limit == 300
    assert any(event["kind"] == "budget.increased" for event in restored.events())


@pytest.mark.parametrize("ceiling,reason", [(200, "more"), (300, "")])
def test_invalid_increase_preserves_state(tmp_path, ceiling, reason):
    store, _ = make_run(tmp_path)
    store.update(llm_token_limit=200, llm_tokens=100)
    with pytest.raises(ValueError):
        cli.raise_budget(store, ceiling, reason)
    assert RunStore(store.path).state.llm_token_limit == 200


def test_unknown_usage_cannot_be_reset_by_ceiling_increase(tmp_path):
    store, _ = make_run(tmp_path)
    store.update(usage_unknown=True)
    with pytest.raises(ValueError, match="Unknown provider usage"):
        cli.raise_budget(store, 400000, "More allowance")
    assert RunStore(store.path).state.usage_unknown


def test_resume_with_invalid_config_does_not_persist_budget_increase(tmp_path, monkeypatch, capsys):
    store, _ = make_run(tmp_path)
    store.update(llm_tokens=100, llm_token_limit=200)
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "missing.yaml"
    args = ["--config", str(missing), "resume", str(store.path)]
    args += ["--llm-token-limit", "300", "--reason", "More allowance"]
    assert cli.main(args) == 1
    restored = RunStore(store.path)
    assert restored.state.llm_token_limit == 200
    assert not any(event["kind"] == "budget.increased" for event in restored.events())


def test_status_counts_explanations_for_current_plan(tmp_path, capsys):
    store, plan = make_run(tmp_path)
    current = store.state.plan_hash
    store.event("clarification.answer", {"question": "Q1", "markdown": "A1", "plan_hash": current})
    store.event("clarification.answer", {"question": "Q2", "markdown": "A2", "plan_hash": "old"})
    assert cli.main(["status", str(store.path)]) == 0
    assert "Clarification explanations for current plan: 1" in capsys.readouterr().out


def test_status_does_not_claim_stale_checkpoint_approval(tmp_path, capsys):
    store, _ = make_run(tmp_path)
    store.update(review_complete=True, approval="approved")
    assert cli.main(["status", str(store.path)]) == 0
    text = capsys.readouterr().out
    assert '"review_complete": false' in text
    assert '"approval": "pending"' in text


def test_real_cli_run_then_offline_review_and_approval(tmp_path, monkeypatch, capsys):
    from experiment_design.runtime.jev import Decision

    source = tmp_path / "input.md"
    original = b"# Question\r\nCompare real measurements.\r\n"
    source.write_bytes(original)
    plan = "# Plan\r\nProtocol with exact reviewed bytes.\r\n"
    created = []

    class CliProvider(Provider):
        def __init__(self, *args):
            texts = ["Brief", plan, "Method review", "Skeptic review", "Final review"]
            super().__init__([{"markdown": text} for text in texts])
            created.append(self)

        def close(self):
            pass

    class Judge:
        def __init__(self, *args):
            pass

        def decide(self, state, questions):
            return {
                key: Decision(next(iter(q.options)), {}, 1, tokens=1)
                for key, q in questions.items()
            }

    config = tmp_path / "public.yaml"
    config.write_text(
        f"llm:\n  base_url: https://example.invalid/v1\n  model: fake\n"
        f"  api_key_env: TEST_C_KEY\nruns_root: {tmp_path.as_posix()}/runs\n"
    )
    monkeypatch.setenv("TEST_C_KEY", "fake-test-key")
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "LLMClient", CliProvider)
    monkeypatch.setattr(cli, "TypeSafeJudge", Judge)
    assert cli.main(["--config", str(config), "run", str(source)]) == 0
    capsys.readouterr()
    run = next((tmp_path / "runs").iterdir())
    assert (run / "stage-01/hypothesis.md").read_bytes() == original
    assert source.read_bytes() == original
    assert (run / "stage-07/exp_plan.md").read_bytes() == plan.encode()
    assert all((run / f"stage-{stage:02d}/manifest.json").is_file() for stage in range(1, 8))
    assert len(created[0].starts) == 5
    monkeypatch.delenv("TEST_C_KEY")
    assert cli.main(["review", str(run)]) == 0
    assert "pending" in capsys.readouterr().out
    assert (
        cli.main(
            [
                "decide",
                str(run),
                "--action",
                "APPROVE",
                "--text",
                "Reviewed and accepted",
                "--by",
                "Actual researcher",
                "--revision",
                "1",
            ]
        )
        == 0
    )
    metadata = json.loads((run / "stage-07/publication.json").read_bytes())
    assert metadata["approval"] == "approved"
    assert (run / "stage-07/exp_plan.md").read_bytes() == plan.encode()
    assert len(created[0].starts) == 5


def ask_options(store):
    store.update(
        status="AWAITING_RESEARCHER",
        question="Which population?",
        question_kind="ANSWER",
        question_options=["Adults", "Children"],
    )


def test_status_and_review_show_numbered_options(tmp_path, capsys):
    store, _ = make_run(tmp_path)
    ask_options(store)
    assert cli.main(["status", str(store.path)]) == 0
    out = capsys.readouterr().out
    assert "Pending question: Which population?\n  1. Adults\n  2. Children" in out
    assert cli.main(["review", str(store.path)]) == 0
    assert "Researcher question: Which population?\n  1. Adults\n  2. Children" in (
        capsys.readouterr().out
    )


def test_decide_option_records_exact_option_text(tmp_path, capsys):
    store, _ = make_run(tmp_path)
    ask_options(store)
    args = ["decide", str(store.path), "--action", "ANSWER", "--option", "2"]
    assert cli.main([*args, "--by", "Researcher", "--revision", "1"]) == 0
    record = [
        e["data"] for e in RunStore(store.path).events() if e["kind"] == "researcher.response"
    ][-1]
    assert (record["option"], record["option_text"], record["text"]) == (2, "Children", "")
    assert RunStore(store.path).state.question_options == []


def test_decide_option_out_of_range_is_a_clear_error(tmp_path, capsys):
    store, _ = make_run(tmp_path)
    ask_options(store)
    args = ["decide", str(store.path), "--action", "ANSWER", "--option", "3"]
    assert cli.main([*args, "--by", "Researcher", "--revision", "1"]) == 1
    assert "Option" in capsys.readouterr().err
    assert RunStore(store.path).state.question_options == ["Adults", "Children"]


def test_decide_help_lists_option(capsys):
    with pytest.raises(SystemExit):
        cli.main(["decide", "--help"])
    assert "--option" in capsys.readouterr().out


def resume_with_config(tmp_path, monkeypatch, run_section):
    store = RunStore.create(
        tmp_path / "runs", [tmp_path / "hypothesis.md"], {"run": RunConfig().model_dump()}, {}
    )
    config = tmp_path / "exd.yaml"
    config.write_text(
        "llm:\n  base_url: https://example.invalid\n  model: fake\n" + run_section,
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EXD_LLM_API_KEY", "test-key")
    seen = []
    monkeypatch.setattr(cli, "_advance", lambda run, cfg: seen.append(run.config()["run"]))
    assert cli.main(["--config", str(config), "resume", str(store.path)]) == 0
    return store, seen


def test_resume_persists_changed_run_limits_before_advancing(tmp_path, monkeypatch):
    (tmp_path / "hypothesis.md").write_text("Hypothesis", encoding="utf-8")
    store, seen = resume_with_config(tmp_path, monkeypatch, "run:\n  task_max_tokens: 777\n")
    assert seen[0]["task_max_tokens"] == 777
    (event,) = [e["data"] for e in store.events() if e["kind"] == "limits.changed"]
    assert (event["old"]["task_max_tokens"], event["new"]["task_max_tokens"]) == (120_000, 777)


def test_resume_with_unchanged_limits_changes_nothing(tmp_path, monkeypatch):
    (tmp_path / "hypothesis.md").write_text("Hypothesis", encoding="utf-8")
    store, seen = resume_with_config(tmp_path, monkeypatch, "")
    assert seen[0] == RunConfig().model_dump()
    assert not [e for e in store.events() if e["kind"] == "limits.changed"]
