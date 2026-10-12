"""Offline inspection and authority for exploration inside the canonical workflow."""

import re

import pytest
import yaml

from experiment_design import cli
from experiment_design.contracts import (
    FieldAnnotation,
    Hypothesis,
    ResearchContext,
    ScientificPackage,
)
from experiment_design.package import snapshot_sources
from experiment_design.runtime.store import Store
from tests.support.exploration import exploration_payload
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "question.md"
    source.write_bytes(b"# Preliminary hypothesis\nThere might be a signal.\n")
    intake = snapshot_sources([source])
    ref = intake.artifacts[0].ref
    context = ResearchContext(
        id="context",
        revision=1,
        source_snapshot_id=intake.id,
        package=ScientificPackage(
            id="signal",
            research_question="Investigate the signal",
            hypothesis=Hypothesis(statement="There might be a signal."),
        ),
        field_annotations={
            "hypothesis": FieldAnnotation(
                status="source-stated", source_ref=ref, source_span="There might be a signal."
            )
        },
    )
    payload = exploration_payload(source_ref=ref)
    payload["defer_reason"] = "The intended observation needs clarification"
    payload["focused_questions"] = ["What observation matters to this study?"]
    llm = ScriptedLLM(
        [
            submit("submit_understanding", {"context": context.model_dump(mode="json")}),
            submit("submit_exploration", payload),
        ]
    )
    monkeypatch.setattr(cli, "LLMClient", lambda *_: llm)
    monkeypatch.setattr(cli, "TypeSafeJudge", lambda *_: ScriptedJudge([{"next_probe": "DEFER"}]))
    monkeypatch.setenv("EXD_LLM_API_KEY", "test-only")
    cfg = tmp_path / "config.yaml"
    db = tmp_path / "exd.db"
    cfg.write_text(
        yaml.safe_dump(
            {"llm": {"base_url": "https://example.invalid", "model": "test"}, "store": str(db)}
        ),
        encoding="utf-8",
    )
    return source, cfg, db, llm


def run(case, capsys):
    assert cli.main(["--config", str(case[1]), "run", str(case[0])]) == 0
    output = capsys.readouterr().out
    return re.search(r"run (r[0-9a-f]+):", output).group(1), output


def test_explore_prints_candidates_and_run_id(case, capsys):
    rid, output = run(case, capsys)
    assert "S2" in output and "NEEDS_RESEARCHER" in output
    store = Store(case[2])
    assert store.exploration_snapshot(rid).selection.choice == "DEFER"
    assert store.version(rid) is None
    assert store.run(rid).plan_contract == "exp-plan/v1"
    store.close()


def test_candidate_status_review_export_need_no_provider_keys(case, capsys, monkeypatch, tmp_path):
    rid, _ = run(case, capsys)
    monkeypatch.delenv("EXD_LLM_API_KEY")

    def trap(*_):
        pytest.fail("Offline command constructed a provider")

    monkeypatch.setattr(cli, "LLMClient", trap)
    monkeypatch.setattr(cli, "TypeSafeJudge", trap)
    for command in ["status", "review"]:
        assert cli.main(["--config", str(case[1]), command, rid]) == 0
    assert "probe-a" in capsys.readouterr().out
    assert cli.main(["--config", str(case[1]), "export", rid, "--out", str(tmp_path / "out")]) == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["approve", "--version", "1", "--by", "PI"],
        ["decide", "foreign", "--revision", "1", "--action", "DEFER", "--by", "PI"],
        ["request-changes", "change", "--by", "PI", "--revision", "1"],
        ["export", "--version", "1", "--out", "output"],
    ],
)
def test_canonical_commands_reject_candidate_only_run(case, capsys, args):
    rid, _ = run(case, capsys)
    store = Store(case[2])
    before = store.events(rid)
    assert cli.main(["--config", str(case[1]), args[0], rid, *args[1:]]) == 2
    assert store.events(rid) == before and store.approvals(rid) == []
    store.close()
