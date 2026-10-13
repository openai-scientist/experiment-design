from pathlib import Path

import pytest

from experiment_design.contracts import WorkflowError
from experiment_design.persistence import store as persistence_store
from experiment_design.persistence.store import RunStore


def make_store(tmp_path):
    source = tmp_path / "input.md"
    source.write_bytes(b"# Hypothesis\r\nExact source.\r\n")
    return RunStore.create(tmp_path / "runs", [source], {}, {})


def test_exact_snapshot_and_dependency_tampering(tmp_path):
    store = make_store(tmp_path)
    assert (
        store.path / "stage-01/hypothesis.md"
    ).read_bytes() == b"# Hypothesis\r\nExact source.\r\n"
    assert store.valid(1)
    store.write(2, "research_brief.md", "Brief")
    store.complete(2, [(1, "hypothesis.md")], ["research_brief.md"])
    assert store.valid(2)
    store.write(1, "hypothesis.md", "Tampered")
    assert not store.valid(1)
    assert not store.valid(2)


def test_archive_preserves_usage_responses_and_clears_review(tmp_path):
    store = make_store(tmp_path)
    store.write(3, "draft_plan.md", "Plan")
    store.complete(3, [(1, "hypothesis.md")], ["draft_plan.md"])
    store.update(
        llm_tokens=99,
        refinements=1,
        plan_stage=3,
        plan_name="draft_plan.md",
        plan_hash="hash",
        review_complete=True,
        approval="approved",
    )
    store.event("researcher.response", {"text": "Actual response"})
    store.archive_from(3)
    loaded = RunStore(store.path)
    assert loaded.state.attempt == 2 and loaded.state.stage == 3
    assert loaded.state.llm_tokens == 99 and loaded.state.refinements == 1
    assert loaded.state.plan_hash is None and not loaded.state.review_complete
    assert loaded.state.approval == "pending"
    assert (store.path / "attempts/1/stage-03/draft_plan.md").read_text() == "Plan"
    assert loaded.events()[0]["data"]["text"] == "Actual response"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "nested/file", "..", "C:escape"])
def test_artifact_paths_cannot_escape(tmp_path, name):
    with pytest.raises(WorkflowError):
        make_store(tmp_path).write(2, name, "Bad")


def test_execution_lock_rejects_overlap_and_releases(tmp_path):
    store = make_store(tmp_path)
    with (
        store.execution(),
        pytest.raises(WorkflowError, match="execut"),
        RunStore(store.path).execution(),
    ):
        pass
    with store.execution():
        pass


def test_missing_outputs_cannot_complete(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(WorkflowError):
        store.complete(2, [(1, "hypothesis.md")], ["missing.md"])
    assert not store.valid(2)


def test_truncated_last_event_recovers_without_losing_prior_event(tmp_path):
    store = make_store(tmp_path)
    store.event("one", {"value": 1})
    with (store.path / "events.jsonl").open("ab") as handle:
        handle.write(b'{"kind":')
    store.event("two", {"value": 2})
    assert [e["kind"] for e in store.events()] == ["one", "two"]


def test_manifest_cannot_authorize_unlisted_dependency(tmp_path):
    store = make_store(tmp_path)
    store.write(2, "research_brief.md", "Accepted brief")
    store.complete(2, [(1, "hypothesis.md")], ["research_brief.md"])
    store.write(2, "unreviewed.md", "Unbound extra")
    store.write(3, "draft_plan.md", "Plan")
    with pytest.raises(WorkflowError, match="declared"):
        store.complete(3, [(2, "unreviewed.md")], ["draft_plan.md"])


def test_archive_crash_recovers_correct_stage_and_attempt(tmp_path, monkeypatch):
    import experiment_design.persistence.store as module

    store = make_store(tmp_path)
    store.write(3, "draft_plan.md", "Plan")
    store.write(4, "review.md", "Review")
    store.update(llm_tokens=123, jev_calls=4)
    replace = module.os.replace

    def crash(source, target):
        if Path(source).name == "stage-04":
            raise OSError("Injected archive interruption")
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", crash)
    with pytest.raises(OSError, match="interruption"):
        store.archive_from(3)
    monkeypatch.setattr(module.os, "replace", replace)
    recovered = RunStore(store.path)
    assert recovered.state.stage == 3 and recovered.state.attempt == 2
    assert recovered.state.llm_tokens == 123 and recovered.state.jev_calls == 4
    assert (store.path / "attempts/1/stage-03/draft_plan.md").is_file()
    assert (store.path / "attempts/1/stage-04/review.md").is_file()
    assert not (store.path / "archive-pending.json").exists()


def test_multiple_source_names_are_unique_and_manifest_binds_them(tmp_path):
    first = tmp_path / "a" / "same.md"
    second = tmp_path / "b" / "same.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"First")
    second.write_bytes(b"Second")
    store = RunStore.create(tmp_path / "runs", [first, second], {}, {})
    assert list(store.sources().values()) == ["First", "Second"]
    assert store.valid(1)
    (store.path / "sources/001-same.md").write_bytes(b"Changed")
    assert not store.valid(1)
    with pytest.raises(WorkflowError, match="snapshot changed"):
        store.sources()


@pytest.mark.parametrize("inputs", [[], [Path("does-not-exist.md")]])
def test_missing_intake_inputs_are_rejected(tmp_path, inputs):
    with pytest.raises(WorkflowError, match="input"):
        RunStore.create(tmp_path / "runs", inputs, {}, {})


def test_duplicate_run_directory_is_rejected(tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    import experiment_design.persistence.store as module

    monkeypatch.setattr(module.uuid, "uuid4", lambda: SimpleNamespace(hex="12345678abcdef"))
    monkeypatch.setattr(
        module, "datetime", SimpleNamespace(now=lambda _: datetime(2026, 1, 1, tzinfo=UTC))
    )
    source = tmp_path / "source.md"
    source.write_text("Input")
    RunStore.create(tmp_path / "runs", [source], {}, {})
    with pytest.raises(WorkflowError, match="collision"):
        RunStore.create(tmp_path / "runs", [source], {}, {})


def test_artifact_alias_cannot_overwrite_manifest_on_windows(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(WorkflowError):
        store.write(1, "manifest.json.", "{}")
    assert store.valid(1)


@pytest.mark.parametrize("name", ["MANIFEST.JSON", "Manifest.Json"])
def test_case_alias_cannot_write_or_declare_manifest(tmp_path, name):
    store = make_store(tmp_path)
    with pytest.raises(WorkflowError):
        store.write(1, name, "{}")
    with pytest.raises(WorkflowError):
        store.complete(1, [], [name])
    assert store.valid(1)


def test_source_index_tamper_cannot_omit_additional_input(tmp_path):
    import json

    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("First source")
    second.write_text("Required constraint")
    store = RunStore.create(tmp_path / "runs", [first, second], {}, {})
    index = store.path / "sources.json"
    data = json.loads(index.read_bytes())
    data["sources"].pop()
    index.write_text(json.dumps(data))
    assert not store.valid(1)
    with pytest.raises(WorkflowError, match="source"):
        store.sources()


def test_constructor_cannot_recover_an_active_archive(tmp_path, monkeypatch):
    import experiment_design.persistence.store as module

    store = make_store(tmp_path)
    store.write(3, "draft_plan.md", "Plan")
    replace = module.os.replace
    observed = []

    def pause_archive(source, target):
        if Path(source).name == "stage-03":
            with pytest.raises(WorkflowError, match="execution"):
                RunStore(store.path)
            observed.append(True)
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", pause_archive)
    with store.execution():
        store.archive_from(3)
    assert observed == [True]
    assert store.state.attempt == 2


def test_event_appends_without_rewriting_journal(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    rewritten = []
    real = persistence_store._atomic
    monkeypatch.setattr(
        persistence_store,
        "_atomic",
        lambda path, data: rewritten.append(path.name) or real(path, data),
    )
    store.event("one", {"value": 1})
    journal = store.path / "events.jsonl"
    before = journal.read_bytes()
    store.event("two", {"value": 2})
    assert journal.read_bytes().startswith(before)
    assert [e["kind"] for e in store.events()] == ["one", "two"]
    assert "events.jsonl" not in rewritten


def test_torn_final_line_is_ignored_but_middle_corruption_is_an_error(tmp_path):
    store = make_store(tmp_path)
    store.event("one", {"value": 1})
    journal = store.path / "events.jsonl"
    with journal.open("ab") as handle:
        handle.write(b'{"kind": "tor')
    assert [e["kind"] for e in store.events()] == ["one"]
    journal.write_bytes(b"not json\n" + journal.read_bytes())
    with pytest.raises(WorkflowError, match="Corrupt event journal"):
        store.events()


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    import os

    real, calls = os.replace, []

    def flaky(source, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("held by reader")
        real(source, target)

    monkeypatch.setattr("experiment_design.persistence.store.os.replace", flaky)
    monkeypatch.setattr("experiment_design.persistence.store.time.sleep", lambda _: None)
    store = make_store(tmp_path)
    store.update(stage=2)
    assert store.path.joinpath("checkpoint.json").is_file()
    assert len(calls) >= 3


def test_atomic_write_reports_persistent_permission_error(tmp_path, monkeypatch):
    store = make_store(tmp_path)

    def locked(source, target):
        raise PermissionError("held by reader")

    monkeypatch.setattr("experiment_design.persistence.store.os.replace", locked)
    monkeypatch.setattr("experiment_design.persistence.store.time.sleep", lambda _: None)
    with pytest.raises(WorkflowError, match="checkpoint.json"):
        store.update(stage=2)
    assert not list(store.path.glob(".*.tmp"))


def test_non_utf8_input_is_rejected_before_run_directory_exists(tmp_path):
    source = tmp_path / "bad.md"
    source.write_bytes(b"caf\xe9")
    runs = tmp_path / "runs"
    with pytest.raises(WorkflowError, match="UTF-8"):
        RunStore.create(runs, [source], {}, {})
    assert not runs.exists() or not list(runs.iterdir())


def test_utf8_bom_and_newlines_stay_exact(tmp_path):
    source = tmp_path / "bom.md"
    source.write_bytes(b"\xef\xbb\xbfa\r\nb\n")
    store = RunStore.create(tmp_path / "runs", [source], {}, {})
    assert (store.path / "stage-01/hypothesis.md").read_bytes() == b"\xef\xbb\xbfa\r\nb\n"
