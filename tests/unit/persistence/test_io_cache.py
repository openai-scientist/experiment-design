"""Hash and log-read caches: faster, never stale."""

import json

import pytest

from experiment_design.api.contract_models import EventClass
from experiment_design.contracts import WorkflowError
from experiment_design.persistence import store as store_module
from experiment_design.persistence.envelope import EnvelopeLog
from experiment_design.persistence.store import RunStore, _hash


def _store(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_text("## H1: a claim\n", encoding="utf-8")
    return RunStore.create(tmp_path / "runs", [source], {}, {})


def _envelope(log, n):
    return log.append(
        "run.note", EventClass.activity, phase=None, step_key="s", actor="a", summary=f"n{n}"
    )


def _full_events(path):
    """Reference: parse the journal from scratch, as the uncached reader did."""
    return [json.loads(line) for line in path.read_bytes().splitlines() if line]


def test_hash_cache_hit_skips_reading(tmp_path, monkeypatch):
    path = tmp_path / "a.txt"
    path.write_bytes(b"one")
    first = _hash(path)
    monkeypatch.setattr(type(path), "read_bytes", lambda self: pytest.fail("re-read"))
    assert _hash(path) == first


def test_hash_recomputed_after_change_of_same_size(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"one")
    first = _hash(path)
    path.write_bytes(b"two")
    assert _hash(path) != first


def test_valid_detects_tampered_artifact(tmp_path):
    store = _store(tmp_path)
    (store.path / "stage-01" / "out.md").write_bytes(b"aaaa")
    store.complete(1, [], ["hypothesis.md", "out.md"])
    assert store.valid(1)
    (store.path / "stage-01" / "out.md").write_bytes(b"bbbb")
    assert not store.valid(1)


def test_events_append_reads_tail_only_and_equals_full_read(tmp_path):
    store = _store(tmp_path)
    path = store.path / "events.jsonl"
    store.event("a", {"n": 1})
    assert store.events() == _full_events(path)
    store.event("b", {"n": 2})
    assert store.events() == _full_events(path)
    snapshot = store_module._LOGS[path.resolve()]
    assert len(snapshot.records) == 2 and snapshot.offset == path.stat().st_size


def test_events_copies_do_not_poison_cache(tmp_path):
    store = _store(tmp_path)
    store.event("a", {"n": 1})
    store.events()[0]["data"]["n"] = 99
    assert store.events()[0]["data"]["n"] == 1


def test_events_torn_tail_dropped_then_truncated_by_next_event(tmp_path):
    store = _store(tmp_path)
    path = store.path / "events.jsonl"
    store.event("a", {})
    with path.open("ab") as handle:
        handle.write(b'{"kind": "tor')
    assert [e["kind"] for e in store.events()] == ["a"]
    store.event("b", {})
    assert [e["kind"] for e in store.events()] == ["a", "b"]
    assert [e["kind"] for e in _full_events(path)] == ["a", "b"]


def test_events_corrupt_middle_still_raises(tmp_path):
    store = _store(tmp_path)
    path = store.path / "events.jsonl"
    store.event("a", {})
    with path.open("ab") as handle:
        handle.write(b"not json\n")
    with pytest.raises(WorkflowError, match="Corrupt event journal"):
        store.events()
    with pytest.raises(WorkflowError, match="Corrupt event journal"):
        store.event("b", {})


def test_events_see_append_from_second_instance(tmp_path):
    first = _store(tmp_path)
    second = RunStore(first.path)
    first.event("a", {})
    assert len(first.events()) == 1
    second.event("b", {})
    assert [e["kind"] for e in first.events()] == ["a", "b"]


def test_events_reset_when_file_is_truncated(tmp_path):
    store = _store(tmp_path)
    path = store.path / "events.jsonl"
    store.event("a", {})
    store.event("b", {})
    assert len(store.events()) == 2
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    assert [e["kind"] for e in store.events()] == ["a"]
    path.unlink()
    assert store.events() == []


def test_envelope_incremental_equals_full_and_second_instance_sees_append(tmp_path):
    one, two = EnvelopeLog(tmp_path), EnvelopeLog(tmp_path)
    _envelope(one, 1)
    assert one.last_seq == 1
    _envelope(two, 2)
    assert one.last_seq == 2
    assert [e.source_seq for e in one.since(0)] == [1, 2]
    assert [e.source_seq for e in EnvelopeLog(tmp_path).since(1)] == [2]
    one.since(0)[0].summary = "mutated"
    assert one.since(0)[0].summary == "n1"


def test_envelope_torn_tail_and_corrupt_middle(tmp_path):
    log = EnvelopeLog(tmp_path)
    _envelope(log, 1)
    with log.path.open("ab") as handle:
        handle.write(b'{"source_seq": 2, "ty')
    assert log.last_seq == 1
    _envelope(log, 2)  # drops the torn bytes, appends cleanly
    assert [e.source_seq for e in log.since(0)] == [1, 2]
    with log.path.open("ab") as handle:
        handle.write(b"garbage\n")
    with pytest.raises(WorkflowError, match="Corrupt envelope log"):
        _ = log.last_seq


def test_envelope_reset_when_file_replaced(tmp_path):
    log = EnvelopeLog(tmp_path)
    _envelope(log, 1)
    _envelope(log, 2)
    assert log.last_seq == 2
    log.path.write_bytes(log.path.read_bytes().splitlines(keepends=True)[0])
    assert log.last_seq == 1


def test_the_log_cache_keeps_the_most_recently_used_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_LOGS", store_module.OrderedDict())
    paths = []
    for n in range(store_module._LOG_LIMIT + 1):
        path = tmp_path / f"{n}.jsonl"
        path.write_bytes(b'{"n": 1}\n')
        paths.append(path.resolve())
        store_module.read_log(path, json.loads, "corrupt")
        if n == 1:
            store_module.read_log(paths[0], json.loads, "corrupt")  # use the oldest again
    assert len(store_module._LOGS) == store_module._LOG_LIMIT
    assert paths[0] in store_module._LOGS and paths[1] not in store_module._LOGS
