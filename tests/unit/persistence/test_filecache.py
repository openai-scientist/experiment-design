"""File cache: serves unchanged files from memory, never stale."""

import builtins
import os

import pytest

from experiment_design.persistence import filecache
from experiment_design.persistence.store import _atomic


@pytest.fixture
def count_opens(monkeypatch):
    opened = []
    real = builtins.open

    def counting(file, mode="r", *args, **kwargs):
        opened.append((str(file), mode))
        return real(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting)
    return opened


def test_unchanged_file_is_read_once(tmp_path, count_opens):
    path = tmp_path / "a.txt"
    path.write_bytes(b"one")
    assert filecache.read_bytes(path) == b"one"
    assert filecache.read_bytes(path) == b"one"
    assert [o for o in count_opens if o[0] == str(path)] == [(str(path), "rb")]


def test_write_through_needs_no_read_open(tmp_path, count_opens):
    path = tmp_path / "a.json"
    _atomic(path, b'{"a": 1}')
    count_opens.clear()
    assert filecache.read_json(path) == {"a": 1}
    assert count_opens == []


def test_outside_modification_is_seen(tmp_path):
    path = tmp_path / "a.txt"
    _atomic(path, b"cached")
    assert filecache.read_bytes(path) == b"cached"
    path.write_bytes(b"tampered!")  # plain open(): size changes
    assert filecache.read_bytes(path) == b"tampered!"
    path.write_bytes(b"tampered?")  # same size: mtime or inode must differ
    os.utime(path, ns=(1, 1))
    assert filecache.read_bytes(path) == b"tampered?"


def test_append_extends_cached_bytes(tmp_path, count_opens):
    path = tmp_path / "log"
    _atomic(path, b"a\n")
    before = filecache.signature(path)
    with path.open("ab") as handle:
        handle.write(b"b\n")
    filecache.appended(path, before, b"b\n")
    count_opens.clear()
    assert filecache.read_bytes(path) == b"a\nb\n"
    assert [o for o in count_opens if o[0] == str(path)] == []


def test_append_after_outside_change_drops_entry(tmp_path):
    path = tmp_path / "log"
    _atomic(path, b"a\n")
    path.write_bytes(b"zzzz\n")
    before = filecache.signature(path)
    filecache.forget(path)
    with path.open("ab") as handle:
        handle.write(b"b\n")
    filecache.appended(path, before, b"b\n")
    assert filecache.read_bytes(path) == b"zzzz\nb\n"


def test_missing_file_raises_as_open_would(tmp_path):
    with pytest.raises(FileNotFoundError):
        filecache.read_bytes(tmp_path / "nope")
    path = tmp_path / "gone"
    _atomic(path, b"x")
    path.unlink()
    with pytest.raises(FileNotFoundError):
        filecache.read_text(path)


def test_oversized_file_is_not_cached(tmp_path, count_opens):
    path = tmp_path / "big"
    path.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    filecache.read_bytes(path)
    filecache.read_bytes(path)
    assert len([o for o in count_opens if o[0] == str(path)]) == 2


def test_parsed_json_is_fresh_per_call(tmp_path):
    path = tmp_path / "a.json"
    _atomic(path, b'{"a": []}')
    filecache.read_json(path)["a"].append(1)
    assert filecache.read_json(path) == {"a": []}
