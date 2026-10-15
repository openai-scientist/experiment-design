"""Stat-validated, write-through file cache.

An ``open()`` costs milliseconds on Windows while ``os.stat`` is nearly free, so a file
whose ``(size, mtime, inode)`` signature is unchanged is served from memory. Writers
report what they wrote, so a read right after a write needs no open either. A change made
by anything else alters the signature and forces a re-read.
"""

import json
import os
import threading
from collections import OrderedDict

Signature = tuple[int, int, int]

_MAX_TOTAL = 32 * 1024 * 1024
_MAX_FILE = 4 * 1024 * 1024

_LOCK = threading.Lock()
_ENTRIES: OrderedDict[str, tuple[Signature, bytes]] = OrderedDict()
_total = 0


def _key(path: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(path))


def _sig(status: os.stat_result) -> Signature:
    return (status.st_size, status.st_mtime_ns, status.st_ino)


def _drop(key: str) -> None:
    global _total
    entry = _ENTRIES.pop(key, None)
    if entry is not None:
        _total -= len(entry[1])


def _store(key: str, signature: Signature, data: bytes) -> None:
    global _total
    with _LOCK:
        _drop(key)
        if len(data) > _MAX_FILE:
            return
        _ENTRIES[key] = (signature, data)
        _total += len(data)
        while _total > _MAX_TOTAL and _ENTRIES:
            _drop(next(iter(_ENTRIES)))


def signature(path: str | os.PathLike) -> Signature | None:
    """The current signature, or None when the file cannot be stat-ed."""
    try:
        return _sig(os.stat(path))
    except OSError:
        return None


def read_bytes(path: str | os.PathLike) -> bytes:
    key = _key(path)
    before = _sig(os.stat(path))
    with _LOCK:
        entry = _ENTRIES.get(key)
        if entry is not None and entry[0] == before:
            _ENTRIES.move_to_end(key)
            return entry[1]
    with open(path, "rb") as handle:
        data = handle.read()
    if signature(path) == before:
        _store(key, before, data)
    else:
        forget(path)
    return data


def read_text(path: str | os.PathLike, encoding: str = "utf-8") -> str:
    return read_bytes(path).decode(encoding)


def read_json(path: str | os.PathLike):
    """Parsed fresh on every call, so callers may mutate the result."""
    return json.loads(read_bytes(path))


def wrote(path: str | os.PathLike, data: bytes) -> None:
    """Call after a complete write or atomic replace of ``path`` succeeded."""
    current = signature(path)
    if current is None:
        forget(path)
    else:
        _store(_key(path), current, data)


def appended(path: str | os.PathLike, before: Signature | None, data: bytes) -> None:
    """Call after an append succeeded; ``before`` is the signature taken before it."""
    key = _key(path)
    current = signature(path)
    with _LOCK:
        entry = _ENTRIES.get(key)
        known = (
            entry[1] if entry is not None and before is not None and entry[0] == before else None
        )
    if current is None or known is None:
        forget(path)
    else:
        _store(key, current, known + data)


def forget(path: str | os.PathLike) -> None:
    with _LOCK:
        _drop(_key(path))
