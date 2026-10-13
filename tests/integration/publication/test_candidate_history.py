import hashlib
import json
from pathlib import Path

import pytest

from experiment_design.config import RunConfig
from experiment_design.design.intake import snapshot_sources
from experiment_design.persistence.store import Store
from experiment_design.publication.exploration_history import export_exploration
from experiment_design.workflow import Deps
from tests.support.exploration import (
    advance_framing,
    exploration_payload,
    exploration_task,
    framed_run,
    historical_exploration,
)
from tests.support.providers import ScriptedJudge, ScriptedLLM, submit, tool_call


@pytest.fixture
def frozen(tmp_path):
    source = tmp_path / "giả thuyết.md"
    source.write_bytes(b"# Hypothesis\r\nA signal might be measurable.\r\n")
    intake = snapshot_sources([source])
    store = Store(tmp_path / "exd.db")
    body = exploration_payload(source_ref=intake.artifacts[0].ref)
    deps = Deps(
        store, ScriptedLLM([submit("submit_exploration", body)]), ScriptedJudge([]), RunConfig()
    )
    rid = framed_run(store, intake)
    assert advance_framing(rid, deps) == "RUNNING"
    rid = historical_exploration(store, store.exploration_snapshot(rid))
    yield store, rid, source
    store.close()


def test_export_uses_frozen_sources_without_provider_or_recomputation(frozen, tmp_path):
    store, rid, source = frozen
    source.write_text("Replaced after intake", encoding="utf-8")
    paths = export_exploration(store, rid, tmp_path / "xuất bản")
    assert set(paths) == {"json", "markdown", "manifest"}
    body = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert (
        body["source"]["artifacts"][0]["text"]
        == "# Hypothesis\r\nA signal might be measurable.\r\n"
    )
    assert body["selection"]["choice"] == "recommend:probe-a"
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "probe-a" in markdown
    assert "unknown" in markdown
    assert "This does not test efficacy" in markdown
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == store.exploration_snapshot(rid).id
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((paths["manifest"].parent / name).read_bytes()).hexdigest() == digest
    before = {key: path.read_bytes() for key, path in paths.items()}
    again = export_exploration(store, rid, paths["json"].parent)
    assert {key: path.read_bytes() for key, path in again.items()} == before


def test_export_does_not_overwrite_partial_or_different_bundle(frozen, tmp_path):
    store, rid, _ = frozen
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "notes.txt").write_text("Researcher notes", encoding="utf-8")
    with pytest.raises(ValueError, match="existing|different|partial"):
        export_exploration(store, rid, destination)
    assert list(destination.iterdir()) == [destination / "notes.txt"]
    assert (destination / "notes.txt").read_text() == "Researcher notes"


def test_export_failure_leaves_destination_untouched(frozen, tmp_path, monkeypatch):
    store, rid, _ = frozen
    destination = tmp_path / "new"
    write = Path.write_bytes

    def fail_manifest(path, data):
        if path.name == "manifest.json":
            raise OSError("disk full")
        return write(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_manifest)
    with pytest.raises(OSError, match="disk full"):
        export_exploration(store, rid, destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".new-*")) == []


def test_export_cited_observations_are_resolvable_without_the_database(tmp_path):
    store = Store(tmp_path / "exd.db")
    deps = Deps(store, ScriptedLLM([]), ScriptedJudge([]), RunConfig())
    rid = framed_run(store)
    reference = f"tool:{exploration_task(store, rid).id}:lookup-1"
    body = exploration_payload()
    body["candidates"][0]["evidence_refs"] = [reference]
    deps.llm = ScriptedLLM(
        [
            tool_call("lookup_evidence", {"query": "Standard deviation"}, "lookup-1"),
            submit("submit_exploration", body),
        ]
    )
    assert advance_framing(rid, deps) == "RUNNING"
    task_id = exploration_task(store, rid).id
    expected = store.accepted_observations(task_id)[0].model_dump(mode="json")
    historical = historical_exploration(store, store.exploration_snapshot(rid))
    paths = export_exploration(store, historical, tmp_path / "export")
    store.close()
    snapshot = json.loads(paths["json"].read_text(encoding="utf-8"))
    observations = {
        observation["evidence_ref"]: observation for observation in snapshot["observations"]
    }
    assert observations[reference] == expected
    assert observations[reference]["task_id"] == snapshot["task_id"]
    assert observations[reference]["binding"] == snapshot["binding"]
