"""The per-repo review state file, outside the repo: locked, replaced atomically, and treated as
empty (with a warning) when it can't be read, so the review hooks fail open."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .snapshot import TREE_ID, cache_dir

MAX_REVIEWED = 100
SESSION_TTL = timedelta(days=7)


def repo_hash(top: str | os.PathLike) -> str:
    """The repo's id outside it: a hash of its real path (the state file's name, and the logs' repo hash)."""
    return hashlib.sha256(os.fsencode(os.path.realpath(top))).hexdigest()[:16]


def path_for(top: str | os.PathLike) -> Path:
    return cache_dir() / f"{repo_hash(top)}.json"


def empty() -> dict[str, Any]:
    return {"reviewed": [], "sessions": {}}


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def _tree(v: Any) -> bool:
    return isinstance(v, str) and TREE_ID.fullmatch(v) is not None


def _counts(d: Any, keys: tuple[str, ...]) -> bool:
    return isinstance(d, dict) and all(type(d.get(k)) is int and d[k] >= 0 for k in keys)


def _loop(loop: Any) -> bool:
    if loop is None:
        return True
    rec = loop.get("record") if isinstance(loop, dict) else None
    return (isinstance(loop, dict) and _counts(loop, ("round", "max")) and isinstance(loop.get("id", ""), str)
            and _tree(loop.get("from"))
            and _tree(loop.get("at")) and _counts(loop.get("totals"), ("fixed", "open"))
            and (rec is None or _counts(rec, ("fixed", "open", "dropped"))
                 or (isinstance(rec, dict) and isinstance(rec.get("skipped"), str))))


def _session(s: Any) -> bool:
    return (isinstance(s, dict) and _tree(s.get("baseline")) and _tree(s.get("turn_start"))
            and all(s.get(k) is None or _tree(s[k]) for k in ("last_reviewed", "warned"))
            and isinstance(s.get("updated"), str) and _loop(s.get("loop")))


def _last_review(v: Any) -> bool:
    return v is None or (isinstance(v, dict) and isinstance(v.get("at"), str) and isinstance(v.get("session"), str))


def _valid(data: Any) -> bool:
    """Every field the state machine reads has its expected shape, so a hand-edited file can't crash it."""
    return (isinstance(data, dict) and _last_review(data.get("last_review")) and isinstance(data.get("reviewed"), list)
            and all(_tree(t) for t in data["reviewed"])
            and isinstance(data.get("sessions"), dict)
            and all(isinstance(k, str) and _session(s) for k, s in data["sessions"].items()))


def read(path: Path, warn: Callable[[str], None] = _stderr) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as e:
        warn(f"devkit: review state {path} is unreadable ({type(e).__name__}); starting from empty")
        return empty()
    if not _valid(data):
        warn(f"devkit: review state {path} is invalid; starting from empty")
        return empty()
    return data


def prune(data: dict[str, Any], now: datetime) -> None:
    """Keep the last MAX_REVIEWED trees and drop sessions not updated within SESSION_TTL."""
    data["reviewed"] = list(dict.fromkeys(reversed(data["reviewed"])))[:MAX_REVIEWED][::-1]
    for sid, s in list(data["sessions"].items()):
        try:
            updated = datetime.fromisoformat(s["updated"])
        except (KeyError, TypeError, ValueError):
            updated = None
        if updated is None or updated.tzinfo is None or now - updated > SESSION_TTL:
            del data["sessions"][sid]


def write(path: Path, data: dict[str, Any], now: datetime) -> None:
    prune(data, now)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class LockTimeout(Exception):
    pass


@contextmanager
def _lock(path: Path, timeout_s: float | None) -> Iterator[None]:
    """timeout_s: give up after that long (hooks must fail open within their time limit); None waits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    with open(path.with_suffix(".lock"), "a") as f:  # the json itself is replaced, so it can't hold the lock
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if deadline is None else fcntl.LOCK_NB))
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"timed out waiting for the review state lock on {path}") from None
                time.sleep(0.05)
        yield


@contextmanager
def locked(top: str | os.PathLike, *, now: datetime | None = None, warn: Callable[[str], None] = _stderr,
           timeout_s: float | None = None) -> Iterator[dict[str, Any]]:
    """The repo's state, held under the lock; written back on exit only if it changed."""
    path = path_for(top)
    with _lock(path, timeout_s):
        data = read(path, warn)
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            write(path, data, now or datetime.now(timezone.utc))


def reset(top: str | os.PathLike) -> bool:
    """Delete the repo's state; whether there was any."""
    path = path_for(top)
    with _lock(path, None):
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True
