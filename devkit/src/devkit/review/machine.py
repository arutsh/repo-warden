"""The review loop's decisions (design D6) as pure functions: state and the current tree in, a
decision and new state out. Callers snapshot, lock the state file and run git; nothing here does."""

from __future__ import annotations

import copy
import re
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal

State = dict[str, Any]


@dataclass(frozen=True)
class Decision:
    action: Literal["allow", "block", "notice"] = "allow"  # notice: the stop goes ahead, the user is told
    message: str = ""


ALLOW = Decision()


def never_blocks(meta: Mapping[str, Any]) -> bool:
    """Stops that go ahead before any snapshot: plan mode, or background tasks still running."""
    return meta.get("permission_mode") == "plan" or bool(meta.get("background_task_count"))


def _fresh(tree: str, now: datetime) -> dict[str, Any]:
    return {"baseline": tree, "turn_start": tree, "last_reviewed": None, "loop": None, "warned": None,
            "updated": now.isoformat()}


def _session(data: State, sid: str, tree: str, now: datetime) -> dict[str, Any]:
    s = data["sessions"].setdefault(sid, _fresh(tree, now))
    s["updated"] = now.isoformat()
    return s


def on_session_start(data: State, sid: str, tree: str, source: str | None, now: datetime) -> State:
    """A new session starts its baseline at `tree`. A known one (resume, or compact mid-turn) keeps its
    entry, so changes made before the restart stay in the review's scope. `source` is not needed."""
    data = copy.deepcopy(data)
    _session(data, sid, tree, now)
    return data


def on_prompt(data: State, sid: str, tree: str, now: datetime) -> tuple[State, Decision]:
    """A new turn starts at `tree`. A loop still open (the user interrupted it) is dropped unreviewed."""
    data = copy.deepcopy(data)
    s = _session(data, sid, tree, now)
    s["turn_start"] = tree
    loop, s["loop"] = s.get("loop"), None
    if loop:
        return data, Decision("notice", f"review round {loop['round']} of {loop['max']} was interrupted; "
                                        "its changes will be reviewed with the next ones")
    return data, ALLOW


def trees(data: State, sid: str) -> list[str]:
    """The trees the next stop diffs from, so the caller can check git still has them; an unused
    baseline that `gc` pruned must not restart the session."""
    s = data["sessions"].get(sid)
    if not s:
        return []
    loop = s.get("loop")
    return [loop["from"], loop["at"]] if loop else [s.get("last_reviewed") or s["baseline"]]


def forget(data: State, sid: str, tree: str, now: datetime) -> State:
    """The session restarted at `tree`: what git no longer has (pruned by gc) can't be diffed."""
    data = copy.deepcopy(data)
    data["sessions"][sid] = _fresh(tree, now)
    return data


def _end(data: State, s: dict[str, Any], cur: str) -> None:
    data["reviewed"] = [t for t in data["reviewed"] if t != cur] + [cur]
    s["last_reviewed"], s["loop"] = cur, None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def block_reason(round_: int, max_rounds: int, sid: str) -> str:
    return (f"Before stopping, run review round {round_} of {max_rounds} on your changes with the devkit-review "
            f"skill. Start with `devkit review plan --session {shlex.quote(sid)}`.")


def on_stop(data: State, sid: str, cur: str, *, level: Literal["warn", "block"], max_rounds: int,
            skip_paths: Iterable[str], meta: Mapping[str, Any], changed: Callable[[str, str], list[str]],
            now: datetime) -> tuple[State, Decision]:
    """`changed(a, b)`: the paths that differ between trees a and b."""
    if never_blocks(meta):
        return data, ALLOW
    data = copy.deepcopy(data)
    s = _session(data, sid, cur, now)
    loop = s.get("loop")
    if loop:
        rec, n = loop.get("record"), loop["round"]
        if rec is None:
            _end(data, s, cur)
            return data, Decision("notice", f"review round {n} of {loop['max']} was not completed")
        if "skipped" in rec:
            _end(data, s, cur)
            return data, Decision("notice", f"review skipped: {rec['skipped']}")
        totals = loop["totals"] = {k: loop["totals"].get(k, 0) + rec.get(k, 0) for k in ("fixed", "open")}
        if rec.get("fixed", 0) == 0 or n >= loop["max"]:
            _end(data, s, cur)
            data["last_review"] = {"at": now.isoformat(), "session": sid}  # repo-level: outlives the session
            return data, Decision("notice", f"review: {_plural(n, 'round')}, {totals['fixed']} fixed, "
                                            f"{totals['open']} to discuss")
        s["loop"] = {**loop, "round": n + 1, "from": loop["at"], "at": cur, "record": None}
        return data, Decision("block", block_reason(n + 1, loop["max"], sid))
    if cur == s["turn_start"] or cur in data["reviewed"]:
        if cur != s["turn_start"]:
            s["last_reviewed"] = cur
        return data, ALLOW
    base = s.get("last_reviewed") or s["baseline"]
    patterns = list(skip_paths)
    if all(skipped(p, patterns) for p in changed(base, cur)):
        _end(data, s, cur)
        return data, ALLOW
    if level == "warn":
        if s.get("warned") == cur:
            return data, ALLOW
        s["warned"] = cur
        return data, Decision("notice", "there are unreviewed changes; the devkit-review skill (/devkit-review) "
                                        "can review them")
    if any(o.get("loop") for k, o in data["sessions"].items() if k != sid):
        return data, ALLOW  # another session's loop (maybe the one reviewing us) runs here; this one waits
    s["loop"] = {"round": 1, "max": max_rounds, "from": base, "at": cur, "record": None,
                 "totals": {"fixed": 0, "open": 0}}
    return data, Decision("block", block_reason(1, max_rounds, sid))


def skipped(path: str, patterns: Iterable[str]) -> bool:
    """Whether a repo-relative path, or a directory it is in, matches a `skip_paths` glob. A pattern
    without an inner `/` matches a name at any depth; `**` spans directories, `*` and `?` stay within one."""
    parts = path.split("/")
    for p in patterns:
        p = p.strip("/")
        anchored = "/" in p
        for i in range(1, len(parts) + 1):
            if _glob(p).fullmatch("/".join(parts[:i]) if anchored else parts[i - 1]):
                return True
    return False


@lru_cache(maxsize=256)
def _glob(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out), re.DOTALL)
