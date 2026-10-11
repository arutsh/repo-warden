"""Git capabilities: git.status, git.diff, git.log, all through the hardened argv/env of gitcmd."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..capabilities import Capability, HandlerCtx, HandlerError, Risk
from ..execution import ExecResult
from ..gitcmd import GitConfigError, git_argv, git_env, repo_overrides
from ..paths import SensitiveMatcher, is_git_internal, resolve

GIT_TIMEOUT_S = 20.0
GIT_MAX_OUTPUT = 8 * 1024 * 1024
STATUS_DEFAULT_ENTRIES = 500
STATUS_MAX_ENTRIES = 5000
DIFF_DEFAULT_BYTES = 256 * 1024
DIFF_MAX_BYTES = 1024 * 1024
DIFF_MAX_PATHS = 32
LOG_DEFAULT_COUNT = 20
LOG_MAX_COUNT = 200
_ENTRY_OVERHEAD = 32  # keys and status of one entry, for the output-cap budget


async def _git(ctx: HandlerCtx, *args: str, max_output: int = GIT_MAX_OUTPUT) -> ExecResult:
    try:
        overrides = await repo_overrides(ctx.backend, ctx.repo.root)
    except GitConfigError as e:
        raise HandlerError(str(e)) from None
    res = await ctx.backend.run(git_argv(*args, extra_overrides=overrides), cwd=Path(ctx.repo.root),
                                timeout_s=GIT_TIMEOUT_S, max_output=max_output, env=git_env())
    if res.timed_out:
        raise HandlerError("git timed out")
    return res


def _failed(res: ExecResult, what: str) -> HandlerError:
    lines = res.stderr.decode(errors="replace").strip().splitlines()
    return HandlerError(f"{what} failed: {lines[-1][:300] if lines else f'git exit {res.returncode}'}")


def _hidden(rel: str, sensitive: SensitiveMatcher) -> bool:
    rel = rel.rstrip("/")
    return is_git_internal(rel) or sensitive.matches(rel)


def _pathspec(rel: str) -> str:
    # literal: an agent-chosen name like ":(glob)*" must not be read as pathspec magic.
    return f":(top,literal){rel}"


def exclude_pathspecs(sensitive: SensitiveMatcher) -> list[str]:
    """`:(exclude...)` pathspecs for `.git` and every sensitive pattern, matching SensitiveMatcher's semantics."""
    out = [":(exclude,top,literal).git"]
    for p in sensitive.patterns:
        if "/" in p:
            out.append(f":(exclude,top,glob,icase){p}")
        else:  # a basename pattern hides any component: a file of that name or a directory's contents
            out += [f":(exclude,top,glob,icase)**/{p}", f":(exclude,top,glob,icase)**/{p}/**"]
    return out


# --- git.status ---------------------------------------------------------------------------------

class StatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_entries: int = Field(STATUS_DEFAULT_ENTRIES, ge=1, le=STATUS_MAX_ENTRIES,
                             description="Stop after this many changed, staged or untracked paths.")


_KINDS = {"1": "changed", "2": "renamed", "u": "unmerged", "?": "untracked"}


def parse_status(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse `git status --porcelain=v2 -z --branch` into (branch, entries)."""
    branch: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    records = raw.decode("utf-8", errors="replace").split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# branch."):
            key, _, value = rec[len("# branch."):].partition(" ")
            if key == "oid":
                branch["oid"] = None if value == "(initial)" else value
            elif key == "head":
                branch["head"] = None if value == "(detached)" else value
            elif key == "upstream":
                branch["upstream"] = value
            elif key == "ab":
                ahead, _, behind = value.partition(" ")
                branch["ahead"], branch["behind"] = int(ahead.lstrip("+")), int(behind.lstrip("-"))
            continue
        kind = rec[0]
        if kind == "?":
            entries.append({"path": rec[2:], "status": "??", "kind": "untracked"})
        elif kind in ("1", "2", "u"):
            # 1 XY sub mH mI mW hH hI path / 2 ... Xscore path\0orig / u XY sub m1 m2 m3 mW h1 h2 h3 path
            nfields = {"1": 8, "2": 9, "u": 10}[kind]
            fields = rec.split(" ", nfields)
            if len(fields) <= nfields:
                continue  # cut short by the output cap
            entry: dict[str, Any] = {"path": fields[nfields], "status": fields[1], "kind": _KINDS[kind]}
            if kind == "2":
                if i >= len(records):
                    continue
                entry["orig_path"] = records[i]
                entry["kind"] = "copied" if fields[8].startswith("C") else "renamed"
                i += 1
            entries.append(entry)
    return branch, entries


async def status(ctx: HandlerCtx, inp: StatusInput) -> dict[str, Any]:
    res = await _git(ctx, "status", "--porcelain=v2", "-z", "--branch", "--untracked-files=normal",
                     "--ignored=no", "--ignore-submodules=all")
    if res.returncode != 0:
        raise _failed(res, "git status")
    branch, parsed = parse_status(res.stdout)
    if res.truncated and parsed:
        parsed.pop()  # the last record may be cut short
    entries: list[dict[str, Any]] = []
    hidden = 0
    truncated = res.truncated
    budget = ctx.output_cap
    for e in parsed:
        if _hidden(e["path"], ctx.sensitive) or _hidden(e.get("orig_path", ""), ctx.sensitive):
            hidden += 1
            continue
        cost = len(e["path"]) + len(e.get("orig_path", "")) + _ENTRY_OVERHEAD
        if len(entries) >= inp.max_entries or cost > budget:
            truncated = True
            break
        budget -= cost
        entries.append(e)
    return {"branch": branch, "entries": entries, "truncated": truncated, "_hidden": hidden}


STATUS = Capability(
    name="git.status",
    input=StatusInput,
    handler=status,
    risk=Risk.READ,
    description=("Show the current branch and the changed, staged and untracked paths (git status). "
                 "Sensitive paths are hidden."),
    audit_fields=("max_entries",),
    result_audit_fields=("_hidden",),
)


# --- git.diff -----------------------------------------------------------------------------------

class DiffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staged: bool = Field(False, description="Diff the staged changes (index vs HEAD) instead of the working tree.")
    paths: list[str] = Field(default_factory=list, max_length=DIFF_MAX_PATHS,
                             description="Limit the diff to these paths relative to the repository root.")
    max_bytes: int = Field(DIFF_DEFAULT_BYTES, ge=1, le=DIFF_MAX_BYTES,
                           description="Return at most this many bytes of diff text.")


_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, '"': 34, "\\": 92}


def _unquote(s: str) -> tuple[str, str]:
    """Undo git's C-style quoting of `s` (starting with '"'); returns (path, rest after the closing quote)."""
    out = bytearray()
    i = 1
    while i < len(s):
        c = s[i]
        if c == '"':
            return out.decode("utf-8", errors="replace"), s[i + 1:]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n in "01234567":
                out.append(int(s[i + 1:i + 4], 8) & 0xFF)
                i += 4
                continue
            if n in _ESCAPES:
                out.append(_ESCAPES[n])
                i += 2
                continue
        out += c.encode()
        i += 1
    return out.decode("utf-8", errors="replace"), ""


def _strip_prefix(p: str) -> str:
    return p[2:] if p[:2] in ("a/", "b/") else p


def header_paths(header: list[str]) -> set[str]:
    """Every path a diff block's header lines could name. Erring towards more candidates only hides more."""
    paths: set[str] = set()
    for line in header:
        if line.startswith("diff --git "):
            rest = line[len("diff --git "):]
            if rest.startswith('"'):
                first, rest = _unquote(rest)
                paths.add(_strip_prefix(first))
                rest = rest.lstrip(" ")
                paths.add(_strip_prefix(_unquote(rest)[0] if rest.startswith('"') else rest))
            elif ' "' in rest:
                first, _, second = rest.partition(' "')
                paths |= {_strip_prefix(first), _strip_prefix(_unquote('"' + second)[0])}
            else:
                # Unquoted names may contain " b/": try every split.
                start = 0
                while (i := rest.find(" b/", start)) != -1:
                    paths |= {_strip_prefix(rest[:i]), _strip_prefix(rest[i + 1:])}
                    start = i + 1
                paths.add(_strip_prefix(rest))
            continue
        for tag in ("--- ", "+++ ", "rename from ", "rename to ", "copy from ", "copy to "):
            if line.startswith(tag):
                name = line[len(tag):]
                name = _unquote(name)[0] if name.startswith('"') else name
                if name != "/dev/null":
                    paths.add(_strip_prefix(name) if tag in ("--- ", "+++ ") else name)
    return paths


def filter_diff(text: str, sensitive: SensitiveMatcher) -> tuple[str, int]:
    """Drop every file block whose header names a git-internal or sensitive path; returns (text, dropped)."""
    # Split on "\n" only: a "\r" inside a changed line must not start a fake block.
    lines = text.split("\n")
    out: list[str] = []
    dropped = 0
    i = 0
    while i < len(lines):
        if not lines[i].startswith("diff --git "):
            out.append(lines[i])  # nothing precedes the first block in practice
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].startswith("diff --git "):
            j += 1
        block = lines[i:j]
        header = []
        for line in block:
            if line.startswith("@@"):
                break
            header.append(line)
        if any(_hidden(p, sensitive) for p in header_paths(header)):
            dropped += 1
        else:
            out += block
        i = j
    return "\n".join(out), dropped


async def diff(ctx: HandlerCtx, inp: DiffInput) -> dict[str, Any]:
    specs = [_pathspec(resolve(ctx.repo, p, ctx.sensitive).rel) for p in inp.paths]
    specs = [s for s in specs if s != _pathspec("")]  # the root: no positive pathspec needed
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color", "--ignore-submodules=all",
            "--src-prefix=a/", "--dst-prefix=b/", "-O/dev/null"]
    if inp.staged:
        args.append("--staged")
    res = await _git(ctx, *args, "--", *specs, *exclude_pathspecs(ctx.sensitive), max_output=inp.max_bytes)
    if res.returncode != 0:
        raise _failed(res, "git diff")
    text, dropped = filter_diff(res.stdout.decode("utf-8", errors="replace"), ctx.sensitive)
    truncated = res.truncated
    if len(text) > ctx.output_cap:
        text = text[:ctx.output_cap]
        truncated = True
    return {"staged": inp.staged, "diff": text, "bytes": len(text.encode()), "truncated": truncated,
            "_dropped": dropped}


DIFF = Capability(
    name="git.diff",
    input=DiffInput,
    handler=diff,
    risk=Risk.READ,
    description=("Show the unified diff of the working tree, or of the staged changes, optionally for "
                 "some paths (bounded). Changes to sensitive files are left out."),
    audit_fields=("staged", "paths", "max_bytes"),
    result_audit_fields=("_dropped",),
)


# --- git.log ------------------------------------------------------------------------------------

class LogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_count: int = Field(LOG_DEFAULT_COUNT, ge=1, le=LOG_MAX_COUNT, description="Return at most this many commits.")
    path: str | None = Field(None, min_length=1, max_length=4096,
                             description="Only commits touching this path relative to the repository root.")


_LOG_FIELDS = ("hash", "author", "date", "subject")


async def log(ctx: HandlerCtx, inp: LogInput) -> dict[str, Any]:
    specs = []
    if inp.path is not None:
        rel = resolve(ctx.repo, inp.path, ctx.sensitive).rel
        if rel:
            specs.append(_pathspec(rel))
    head = await _git(ctx, "rev-parse", "-q", "--verify", "HEAD^{commit}", max_output=4096)
    if head.returncode != 0:
        return {"commits": [], "truncated": False}  # no commits yet
    res = await _git(ctx, "log", "--no-patch", "--no-show-signature", "--no-color", "--no-decorate", "-z",
                     f"--max-count={inp.max_count}", "--format=%H%x00%an%x00%aI%x00%s", "--", *specs)
    if res.returncode != 0:
        raise _failed(res, "git log")
    fields = res.stdout.decode("utf-8", errors="replace").split("\0")
    if res.truncated:
        # Drop the cut-short tail: a partial record, or the last subject when the cut fell inside it.
        whole = len(fields) - len(fields) % len(_LOG_FIELDS)
        fields = fields[:whole if whole < len(fields) else whole - len(_LOG_FIELDS)]
    commits: list[dict[str, Any]] = []
    truncated = res.truncated
    budget = ctx.output_cap
    for k in range(0, len(fields) - len(_LOG_FIELDS) + 1, len(_LOG_FIELDS)):
        c = dict(zip(_LOG_FIELDS, fields[k:k + len(_LOG_FIELDS)], strict=True))
        cost = sum(map(len, c.values())) + _ENTRY_OVERHEAD
        if cost > budget:
            truncated = True
            break
        budget -= cost
        commits.append(c)
    return {"commits": commits, "truncated": truncated}


LOG = Capability(
    name="git.log",
    input=LogInput,
    handler=log,
    risk=Risk.READ,
    description="List recent commits (hash, author, date, subject; no patches), optionally for one path.",
    audit_fields=("max_count", "path"),
)
