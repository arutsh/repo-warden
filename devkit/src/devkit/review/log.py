"""Review log records (design D10): OTel-log-shaped JSON lines, one file per day in the devkit cache.
A write never fails its caller: a failure is a one-line warning on stderr."""

from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import state
from .snapshot import cache_dir

PREFIX = "devkit.review."
# Agent-written finding text, logged only with the user's log_content.
CONTENT = {"quote": "quote", "claim": "claim", "failure_scenario": "failure_scenario", "reason": "drop_reason"}
LOCK_TIMEOUT_S = 2.0
_DAY_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl")


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr)


def log_dir() -> Path:
    return cache_dir() / "logs"  # next to the state: ~/.cache is writable inside cplt, ~/.local/state isn't


@lru_cache(maxsize=1)
def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("repo-warden-devkit")
    except PackageNotFoundError:
        return "unknown"


@lru_cache(maxsize=8)
def repo_attrs(top: str | os.PathLike) -> dict[str, str]:
    """The repo by its directory name and a hash of its path (the state file's), never the path itself.
    Callers must not mutate the result (it is cached: a round's records all resolve the same repo)."""
    return {"devkit.repo.name": os.path.basename(os.path.realpath(top)), "devkit.repo.path_hash": state.repo_hash(top)}


def _homes() -> list[str]:
    """The home dir as given and as resolved (they differ when it is a symlink), longest first."""
    home = os.path.expanduser("~").rstrip(os.sep)
    return sorted({h for h in (home, os.path.realpath(home) if home else "") if h}, key=len, reverse=True)


def scrub(text: str) -> str:
    """`text` with the home dir, wherever it appears as a path prefix, written as `~`."""
    for h in _homes():
        # Followed by a separator or the end; a period counts unless a name goes on (`/home/u.bak`).
        text = re.sub(re.escape(h) + r"(?=$|[/\s\"'`:;,)\]}]|\.(?![\w.-]))", "~", text)
    return text


def _timestamp(now: datetime) -> str:
    now = now.astimezone(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond:06d}000Z"  # RFC 3339, nanoseconds


def make(top: str | os.PathLike, name: str, attrs: Mapping[str, Any], body: str, now: datetime) -> dict[str, Any]:
    return {"timestamp": _timestamp(now), "severity_text": "INFO", "severity_number": 9, "body": body,
            "resource": {"service.name": "devkit", "service.version": _version()},
            "attributes": {"event.name": PREFIX + name, **repo_attrs(top),  # free text may name a home path
                           **{k: scrub(v) if isinstance(v, str) else v for k, v in attrs.items() if v is not None}}}


def context(session: str, loop: Mapping[str, Any]) -> dict[str, Any]:
    return {"session.id": session, PREFIX + "loop_id": loop.get("id", ""), PREFIX + "round": loop["round"]}


def event_records(top, events: Iterable[tuple[str, Mapping[str, Any]]], now: datetime) -> list[dict[str, Any]]:
    """Records for the state machine's events: (name, {session, loop: {id, round}, **fields})."""
    out = []
    for name, e in events:
        fields = {k: v for k, v in e.items() if k not in ("session", "loop")}
        body = f"review {name.replace('_', ' ')}, round {e['loop']['round']}"
        body += f": {e['reason']}" if "reason" in e else ""
        out.append(make(top, name, {**context(e["session"], e["loop"]), **{PREFIX + k: v for k, v in fields.items()}},
                        body, now))
    return out


def finding_id(loop_id: str, round_: int, focus: str, file: str | None, line: int | None, claim: str) -> str:
    key = "\0".join([loop_id, str(round_), focus, file or "", "" if line is None else str(line), claim])
    return "f-" + hashlib.sha256(key.encode()).hexdigest()[:8]


def rel_path(top: str | os.PathLike, file: str) -> str:
    """Repo-relative when the agent gave an absolute path inside the repo; else as given (`make` writes the
    home dir as `~`)."""
    if not os.path.isabs(file):
        return file
    real, top = os.path.realpath(file), os.path.realpath(top)
    if real == top or real.startswith(top + os.sep):
        return os.path.relpath(real, top)
    return file


def _secret(path: str, patterns: Iterable[str]) -> bool:
    """As in snapshots: the file, or a directory it is in, matches a secret pattern, case-insensitively
    (exemptions don't apply). Each name is literal: `secrets.match` would take `a[1].key` for a glob query."""
    patterns = [p.lower() for p in patterns]
    return any(fnmatch.fnmatchcase(c.lower(), p) for c in path.split("/") if c not in ("", ".", "..")
               for p in patterns)


def finding_record(top, ctx: Mapping[str, Any], f: Mapping[str, Any], *, content: bool, secrets: Iterable[str],
                   now: datetime) -> dict[str, Any]:
    path = scrub(rel_path(top, f["file"])) if f.get("file") else None  # the id hashes the path as logged
    fid = finding_id(ctx[PREFIX + "loop_id"], ctx[PREFIX + "round"], f["focus"], path, f.get("line"), f["claim"])
    attrs = {**ctx, PREFIX + "finding_id": fid, PREFIX + "focus": f["focus"], PREFIX + "model": f["model"],
             PREFIX + "outcome": f["outcome"], PREFIX + "score": f.get("score"),
             "code.file.path": path, "code.line.number": f.get("line")}
    if content and not (path and _secret(path, secrets)):
        attrs.update({PREFIX + name: f.get(k) for k, name in CONTENT.items()})
    return make(top, "finding", attrs, f"finding {fid} {f['outcome']}", now)


def _expire(d: Path, today: date, days: int) -> None:
    for p in d.iterdir():
        m = _DAY_FILE.fullmatch(p.name)
        if m:
            try:
                old = today - date.fromisoformat(m.group(1)) > timedelta(days=days)
            except ValueError:
                continue
            if old:
                p.unlink(missing_ok=True)


def _flock(fd: int) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_S  # a stuck writer must not hold up a hook
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("timed out waiting for the log lock") from None
            time.sleep(0.02)


def write(records: list[dict[str, Any]], settings: Mapping[str, Any], *, now: datetime | None = None,
          warn: Callable[[str], None] = _stderr) -> bool:
    """Append records to today's file and delete files past retention; whether they were written."""
    if not settings["log"] or not records:
        return False
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        data = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records).encode()
        fd = os.open(d / f"{today.isoformat()}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            _flock(fd)
            os.write(fd, data)  # one write under the lock: concurrent sessions' lines never interleave
            _expire(d, today, settings["log_retention_days"])
        finally:
            os.close(fd)
    except Exception as e:  # noqa: BLE001 - a log must never fail the hook or command
        warn(f"devkit: could not write the review log ({type(e).__name__}: {e})")
        return False
    return True


def read(days: int, *, now: datetime | None = None) -> Iterator[dict[str, Any]]:
    """Every readable record in the files within `days`, oldest file first; bad lines are skipped."""
    d = log_dir()
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    try:
        names = sorted(p.name for p in d.iterdir() if _DAY_FILE.fullmatch(p.name))
    except OSError:
        return
    for name in names:
        try:
            if today - date.fromisoformat(name[:10]) > timedelta(days=days):
                continue
            lines = (d / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(r, dict) and isinstance(r.get("attributes"), dict):
                yield r
