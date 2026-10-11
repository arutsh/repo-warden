"""search.code: ripgrep when installed, a pure-Python walker otherwise, with the same exclusions and caps."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

import regex
from pydantic import BaseModel, ConfigDict, Field

from ..capabilities import Capability, HandlerCtx, HandlerError, Risk
from ..paths import FILE_FLAGS, SafePath, has_git_entry, is_git_internal, nested_boundary, open_nofollow, resolve

SEARCH_DEFAULT_MATCHES = 200
SEARCH_MAX_MATCHES = 2000
SEARCH_MAX_FILE = 1024 * 1024    # larger files are skipped by both engines
SEARCH_TIMEOUT_S = 20.0
EXCERPT_CHARS = 500
BINARY_SNIFF = 8192
RG_MAX_OUTPUT = 8 * 1024 * 1024  # one JSON line holds at most one SEARCH_MAX_FILE line, escaped
_MATCH_OVERHEAD = 32             # keys and line number of one match, for the output-cap budget


class SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pattern: str = Field(min_length=1, max_length=1000, description="Text to search for (literal unless regex).")
    regex: bool = Field(False, description="Treat the pattern as a regular expression.")
    ignore_case: bool = Field(False, description="Match case-insensitively.")
    path: str = Field("", max_length=4096,
                      description="Directory or file relative to the repository root; empty for the root.")
    glob: str | None = Field(None, min_length=1, max_length=256, pattern=r"^[^!\x00][^\x00]*$",
                             description="Only search files matching this glob, e.g. '*.py' or 'src/**/*.ts'.")
    max_matches: int = Field(SEARCH_DEFAULT_MATCHES, ge=1, le=SEARCH_MAX_MATCHES,
                             description="Stop after this many matching lines.")


class _Collector:
    """Applies the post-filter (git, sensitive, nested boundaries) and the caps to matches from either engine."""

    def __init__(self, ctx: HandlerCtx, max_matches: int):
        self.ctx = ctx
        self.max_matches = max_matches
        self.budget = ctx.output_cap
        self.matches: list[dict[str, Any]] = []
        self.truncated = False
        # Matches the post-filter dropped, for the audit record only: told to the agent, a count of
        # hidden matches would reveal whether a hidden file contains the pattern.
        self.filtered = {"excluded": 0, "nested": 0}
        self._dirs: dict[str, bool] = {}

    def _hidden(self, rel: str) -> str | None:
        """Why `rel` is filtered out ("excluded": git or sensitive, "nested": inside a nested repo), or None."""
        if is_git_internal(rel) or self.ctx.sensitive.matches(rel):
            return "excluded"
        parent = os.path.dirname(rel)
        if parent not in self._dirs:
            self._dirs[parent] = nested_boundary(self.ctx.repo, SafePath(parent).parts) is not None
        return "nested" if self._dirs[parent] else None

    def add(self, rel: str, line: int, text: str) -> bool:
        """Record one match; False once the result is full (and marked truncated)."""
        if reason := self._hidden(rel):
            self.filtered[reason] += 1
            return True
        text = text.rstrip("\n")[:EXCERPT_CHARS]
        cost = len(rel) + len(text) + _MATCH_OVERHEAD
        if len(self.matches) >= self.max_matches or cost > self.budget:
            self.truncated = True
            return False
        self.budget -= cost
        self.matches.append({"path": rel, "line": line, "text": text})
        return True


def _rg_argv(ctx: HandlerCtx, inp: SearchInput, target: str) -> list[str]:
    argv = ["rg", "--no-config", "--json", "--no-follow", "--hidden", "--max-columns", str(EXCERPT_CHARS),
            f"--max-filesize={SEARCH_MAX_FILE}", "--sort=path", f"--max-count={inp.max_matches + 1}"]
    if inp.glob:
        argv.append(f"--glob={inp.glob}")
    # After the agent's glob: when several globs match a file, rg lets the last one win.
    argv.append("--iglob=!.git")
    argv += [f"--iglob=!{p}" for p in ctx.sensitive.patterns]
    argv.append("--ignore-case" if inp.ignore_case else "--case-sensitive")
    if not inp.regex:
        argv.append("--fixed-strings")
    argv += [f"--regexp={inp.pattern}", "--", target]
    return argv


async def _search_rg(ctx: HandlerCtx, inp: SearchInput, safe: SafePath, out: _Collector) -> None:
    try:
        os.stat(safe.rel or ".", dir_fd=ctx.repo.root_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise HandlerError(f"path not found: {inp.path}") from None
    res = await ctx.backend.run(_rg_argv(ctx, inp, safe.rel or "."), cwd=Path(ctx.repo.root),
                                timeout_s=SEARCH_TIMEOUT_S, max_output=RG_MAX_OUTPUT, env={})
    if res.timed_out:
        raise HandlerError("search timed out")
    found = finished = False
    for raw in res.stdout.splitlines():
        try:
            msg = json.loads(raw)
        except ValueError:
            continue  # the last line of a capped output can be cut short
        if msg.get("type") == "summary":
            finished = True
        if msg.get("type") != "match":
            continue
        data = msg["data"]
        path, text = data["path"].get("text"), data["lines"].get("text")
        if path is None or text is None:
            continue  # non-UTF-8 path or line: rg sends base64 bytes, which we don't return
        found = True
        if not out.add(os.path.normpath(path), data["line_number"], text):
            return
    if res.truncated:
        out.truncated = True
    # rg exits 2 for unreadable files too, but then still ends with a summary; a fatal error (bad regex) has none.
    if res.returncode == 2 and not (found or finished or res.truncated):
        lines = res.stderr.decode(errors="replace").strip().splitlines()
        raise HandlerError(f"search failed: {lines[-1][:300] if lines else 'ripgrep error'}")


def _glob_matches(glob: str | None, rel: str) -> bool:
    """fnmatch, plus `**/` matching zero directories as in ripgrep, so `src/**/*.ts` matches `src/a.ts`.

    Still unlike ripgrep: `*` crosses `/`, and `{a,b}` is not expanded.
    """
    if glob is None:
        return True
    name = rel if "/" in glob else os.path.basename(rel)
    return fnmatch.fnmatchcase(name, glob) or fnmatch.fnmatchcase(name, glob.replace("**/", ""))


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise HandlerError("search timed out")
    return left


def _search_file(dir_fd: int, name: str, rel: str, rx: regex.Pattern[str], out: _Collector,
                 deadline: float) -> bool:
    try:
        fd = os.open(name, FILE_FLAGS, dir_fd=dir_fd)
    except OSError:
        return True  # a symlink (ELOOP), unreadable or gone: skipped, as rg does
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > SEARCH_MAX_FILE:
            return True
        data = os.read(fd, SEARCH_MAX_FILE + 1)
    finally:
        os.close(fd)
    if b"\x00" in data[:BINARY_SNIFF]:
        return True
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for n, line in enumerate(lines, 1):
        # A per-call timeout bounds a backtracking pattern; concurrent=True releases the GIL meanwhile.
        try:
            hit = rx.search(line, concurrent=True, timeout=_remaining(deadline))
        except TimeoutError:
            raise HandlerError("search timed out") from None
        if hit and not out.add(rel, n, line):
            return False
    return True


def _walk(ctx: HandlerCtx, inp: SearchInput, safe: SafePath, rx: regex.Pattern[str], out: _Collector) -> None:
    deadline = time.monotonic() + SEARCH_TIMEOUT_S
    try:
        st = os.stat(safe.rel or ".", dir_fd=ctx.repo.root_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise HandlerError(f"path not found: {inp.path}") from None
    if stat.S_ISREG(st.st_mode):
        parent, name = os.path.split(safe.rel)
        pfd = open_nofollow(ctx.repo, SafePath(parent), directory=True)
        try:
            if _glob_matches(inp.glob, safe.rel):
                _search_file(pfd, name, safe.rel, rx, out, deadline)
        finally:
            os.close(pfd)
        return
    top = open_nofollow(ctx.repo, safe, directory=True)
    try:
        for dirpath, dirnames, filenames, dir_fd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
            _remaining(deadline)
            rel_dir = os.path.normpath(os.path.join(safe.rel, dirpath))
            rel_dir = "" if rel_dir == "." else rel_dir

            def rel(name: str) -> str:
                return f"{rel_dir}/{name}" if rel_dir else name

            dirnames[:] = sorted(d for d in dirnames
                                 if d.lower() != ".git" and not ctx.sensitive.matches(rel(d))
                                 and not has_git_entry(dir_fd, d))
            for name in sorted(filenames):
                _remaining(deadline)
                r = rel(name)
                if name.lower() == ".git" or ctx.sensitive.matches(r) or not _glob_matches(inp.glob, r):
                    continue
                if not _search_file(dir_fd, name, r, rx, out, deadline):
                    return
    finally:
        os.close(top)


async def _search_python(ctx: HandlerCtx, inp: SearchInput, safe: SafePath, out: _Collector) -> None:
    try:
        rx = regex.compile(inp.pattern if inp.regex else regex.escape(inp.pattern),
                           regex.IGNORECASE if inp.ignore_case else 0)
    except regex.error as e:
        raise HandlerError(f"search failed: invalid regex: {e}") from None
    await asyncio.to_thread(_walk, ctx, inp, safe, rx, out)


_ENGINES = {"rg": _search_rg, "python": _search_python}


def make_search(engine: str | None = None) -> Capability:
    """search.code bound to an engine: ripgrep if it is on PATH when this runs (at startup), else the walker."""
    if engine is None:
        engine = "rg" if shutil.which("rg") else "python"
        if engine == "python":
            print("repo-warden: rg not found on PATH, search.code uses the built-in walker "
                  "(fnmatch globs, no .gitignore)", file=sys.stderr, flush=True)
    run = _ENGINES[engine]

    async def search(ctx: HandlerCtx, inp: SearchInput) -> dict[str, Any]:
        safe = resolve(ctx.repo, inp.path, ctx.sensitive)
        out = _Collector(ctx, inp.max_matches)
        await run(ctx, inp, safe, out)
        return {"matches": out.matches, "truncated": out.truncated, "engine": engine, "_filtered": out.filtered}

    return Capability(
        name="search.code",
        input=SearchInput,
        handler=search,
        risk=Risk.READ,
        description=("Search file contents in the repository (literal by default, regex optional), "
                     "returning path, line number and line excerpt. Skips .git, sensitive, binary and "
                     "large files, does not follow symlinks and does not search nested repositories."),
        audit_fields=("pattern", "regex", "ignore_case", "path", "glob", "max_matches"),
        result_audit_fields=("engine", "_filtered"),
    )
