"""`devkit check`: the rules over staged changes or a commit range, outside any agent. Fails closed (exit 2)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from . import config as config_mod
from .adapters import MAX_FILE_BYTES
from .engine import evaluate, load_config, needs_content, one_run
from .events import Event, Finding, Level, Outcome
from .rules import branch_name, get_rule

SUBMODULE_MODE = "160000"


class CheckError(Exception):
    """git failed or the range can't be used."""


def git(top: Path | str, *args: str, input: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(["git", *args], cwd=top, input=input, capture_output=True, check=False)
    except OSError as e:
        raise CheckError(f"cannot run git: {e}") from None
    if proc.returncode != 0:
        raise CheckError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _line(out: bytes) -> str:
    return os.fsdecode(out).rstrip("\n")


@dataclass(frozen=True)
class Change:
    path: str  # repo-relative, after the change
    old_blob: str | None  # None for an added file
    new_blob: str


def changes(top: Path, *diff_args: str) -> list[Change]:
    """Added, modified, renamed and retyped files; renames keep their old content as the before side."""
    out = git(top, "diff", "--raw", "-z", "-M", "--no-abbrev", "--diff-filter=AMRT", *diff_args)
    tokens = iter(out.split(b"\0"))
    found = []
    for meta in tokens:
        if not meta:
            continue
        old_mode, new_mode, old_sha, new_sha, status = meta[1:].decode().split()
        path = os.fsdecode(next(tokens))
        if status.startswith("R"):
            path = os.fsdecode(next(tokens))
        if new_mode == SUBMODULE_MODE:
            continue
        old = None if status == "A" or old_mode == SUBMODULE_MODE else old_sha
        found.append(Change(path, old, new_sha))
    return found


def blobs(top: Path, shas: set[str]) -> dict[str, str]:
    """Each blob's text; empty above MAX_FILE_BYTES, as in the hooks."""
    if not shas:
        return {}
    request = "".join(f"{s}\n" for s in sorted(shas)).encode()
    sizes = {}
    for line in git(top, "cat-file", "--batch-check", input=request).decode().splitlines():
        sha, *rest = line.split()
        if len(rest) != 2:
            raise CheckError(f"git object {sha} is missing")
        sizes[sha] = int(rest[1])
    texts = {s: "" for s in shas}
    small = [s for s in sorted(shas) if sizes[s] <= MAX_FILE_BYTES]
    if not small:
        return texts
    out = git(top, "cat-file", "--batch", input="".join(f"{s}\n" for s in small).encode())
    pos = 0
    for _ in small:
        end = out.index(b"\n", pos)
        sha, _kind, size = out[pos:end].decode().split()
        texts[sha] = out[end + 1:end + 1 + int(size)].decode("utf-8", errors="replace")
        pos = end + 1 + int(size) + 1
    return texts


def _commit(top: Path, rev: str) -> str:
    return _line(git(top, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}"))


def range_changes(top: Path, spec: str) -> list[Change]:
    """`BASE..HEAD` (or `...`): the files changed on HEAD since it left BASE, as in a pull request."""
    base, sep, head = spec.partition("...") if "..." in spec else spec.partition("..")
    if not sep or not base or not head:
        raise CheckError(f"invalid range {spec!r}: use BASE..HEAD")
    base, head = _commit(top, base), _commit(top, head)
    return changes(top, _line(git(top, "merge-base", base, head)), head)


def staged_changes(top: Path) -> list[Change]:
    try:
        base = _commit(top, "HEAD")
    except CheckError:  # no commits yet: compare with the empty tree
        base = _line(git(top, "hash-object", "-t", "tree", "--stdin", input=b""))
    return changes(top, "--cached", base)


def merging(top: Path) -> bool:
    try:
        git(top, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    except CheckError:
        return False
    return True


def soften(findings: list[tuple[Level, Finding]]) -> tuple[list[tuple[Level, Finding]], bool]:
    """Style findings at `block` become warnings; guard rules still block. Also: whether any changed."""
    out = [(Level.WARN if level is Level.BLOCK and not get_rule(f.rule).guard else level, f) for level, f in findings]
    return out, out != findings


def current_branch(top: Path) -> str | None:
    """The checked-out branch; on a detached HEAD (a CI pull request checkout), GitHub's head branch if set."""
    try:
        return _line(git(top, "symbolic-ref", "--short", "-q", "HEAD")) or None
    except CheckError:
        return os.environ.get("GITHUB_HEAD_REF") or None


def branch_findings(top: Path, cfg: config_mod.Config) -> list[tuple[Level, Finding]]:
    rc = cfg.rules["branch_name"]
    if rc.level is Level.OFF or not cfg.in_scope:
        return []
    name = current_branch(top)
    problem = branch_name.problem(name, rc.settings) if name else None
    if not problem:
        return []
    level = min(rc.level, get_rule("branch_name").max_level)
    return [(level, Finding("branch_name", f"the current {problem} Rename it with `git branch -m <name>`."))]


def judge(top: Path, found: list[Change]) -> Outcome:
    bare = [Event("edit" if c.old_blob else "write", "check", path=str(top / c.path), cwd=str(top))
            for c in found]
    wanted = [i for i, e in enumerate(bare) if needs_content(e, strict=True)]
    texts = blobs(top, {s for i in wanted for s in (found[i].old_blob, found[i].new_blob) if s})
    events = list(bare)
    for i in wanted:
        c = found[i]
        events[i] = Event(bare[i].kind, "check", path=bare[i].path, cwd=str(top),
                          before=texts[c.old_blob] if c.old_blob else "", after=texts[c.new_blob])
    return evaluate(events, strict=True)


def check(cwd: str, *, staged: bool = False, spec: str | None = None,
          out: TextIO, err: TextIO) -> int:
    """0: nothing blocks; 1: a finding at `block`; 2: invalid config or git failed."""
    softened = False
    try:
        top = Path(_line(git(cwd, "rev-parse", "--show-toplevel")))
        with one_run():  # the config is parsed once for the whole check
            cfg = load_config(top, strict=True)
            outcome = judge(top, staged_changes(top) if staged else range_changes(top, spec or ""))
        outcome.findings += branch_findings(top, cfg)
        if staged and merging(top):  # HEAD is one parent: the merged branch's changes look new
            outcome.findings, softened = soften(outcome.findings)
    except config_mod.ConfigError as e:
        err.write(f"devkit check: invalid config: {e}\n")
        return 2
    except CheckError as e:
        err.write(f"devkit check: {e}\n")
        return 2
    for level, f in outcome.findings:
        where = os.path.relpath(f.path, top) if f.path else "(branch)"
        out.write(f"{str(level):<5}  {where}{f':{f.line}' if f.line else ''}  [{f.rule}] {f.message}\n")
    out.flush()
    if softened:
        err.write("devkit check: merge in progress, so style findings are warnings (guard rules still block)\n")
    if outcome.blocked:
        n = len(outcome.blocks)
        err.write(f"devkit check: {n} blocking finding{'s' if n != 1 else ''}\n")
        return 1
    return 0
