"""Filesystem capabilities."""

from __future__ import annotations

import io
import os
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..capabilities import Capability, HandlerCtx, HandlerError, Risk
from ..paths import PathDenied, SafePath, has_git_entry, open_nofollow, resolve

READ_DEFAULT_BYTES = 256 * 1024
READ_MAX_BYTES = 1024 * 1024
BINARY_SNIFF = 8192
LIST_DEFAULT_ENTRIES = 500
LIST_MAX_ENTRIES = 5000
LIST_MAX_DEPTH = 8
_ENTRY_OVERHEAD = 32  # keys, type and size of one listed entry, for the output-cap budget


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=4096, description="File path relative to the repository root.")
    max_bytes: int = Field(READ_DEFAULT_BYTES, ge=1, le=READ_MAX_BYTES,
                           description="Read at most this many bytes from the start of the file.")
    start_line: int | None = Field(None, ge=1, description="First line to return (1-based, inclusive).")
    end_line: int | None = Field(None, ge=1, description="Last line to return (1-based, inclusive).")

    @model_validator(mode="after")
    def _range(self) -> ReadInput:
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        return self


def _read_upto(fd: int, n: int) -> bytes:
    chunks, total = [], 0
    while total < n:
        chunk = os.read(fd, min(65536, n - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


async def read(ctx: HandlerCtx, inp: ReadInput) -> dict[str, Any]:
    safe = resolve(ctx.repo, inp.path, ctx.sensitive)
    try:
        fd = open_nofollow(ctx.repo, safe)
    except FileNotFoundError:
        raise HandlerError(f"file not found: {inp.path}") from None
    except PermissionError:
        raise HandlerError(f"permission denied: {inp.path}") from None
    try:
        size = os.fstat(fd).st_size
        data = _read_upto(fd, inp.max_bytes + 1)
    finally:
        os.close(fd)
    truncated = len(data) > inp.max_bytes
    data = data[:inp.max_bytes]
    result: dict[str, Any] = {"path": safe.rel, "size": size, "bytes": len(data)}
    if b"\x00" in data[:BINARY_SNIFF]:
        return {**result, "binary": True, "truncated": truncated, "content": None}

    text = data.decode("utf-8", errors="replace")
    if inp.start_line or inp.end_line:
        lines = io.StringIO(text, newline="\n").readlines()  # only \n ends a line, as in git and rg
        start = (inp.start_line or 1) - 1
        text = "".join(lines[start:inp.end_line])
        result["start_line"] = start + 1
    if len(text) > ctx.output_cap:
        text = text[:ctx.output_cap]
        truncated = True
    return {**result, "binary": False, "truncated": truncated, "content": text}


READ = Capability(
    name="filesystem.read",
    input=ReadInput,
    handler=read,
    risk=Risk.READ,
    description="Read a text file in the repository (UTF-8, bounded, optional line range).",
    audit_fields=("path", "max_bytes", "start_line", "end_line"),
)


class ListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field("", max_length=4096,
                      description="Directory relative to the repository root; empty for the root.")
    depth: int = Field(1, ge=1, le=LIST_MAX_DEPTH,
                       description="1 lists the directory's own entries; each extra level descends once more.")
    max_entries: int = Field(LIST_DEFAULT_ENTRIES, ge=1, le=LIST_MAX_ENTRIES,
                             description="Stop after this many entries.")


def _kind(entry: os.DirEntry[str]) -> str:
    if entry.is_symlink():
        return "symlink"
    if entry.is_dir(follow_symlinks=False):
        return "dir"
    if entry.is_file(follow_symlinks=False):
        return "file"
    return "other"


async def list_dir(ctx: HandlerCtx, inp: ListInput) -> dict[str, Any]:
    """Breadth-first, sorted by name within each directory, so a cut keeps the shallowest entries.

    Symlinks are reported, never followed. `.git` and sensitive entries are left out. A directory
    holding a `.git` entry (nested repo, worktree or submodule) is reported but never descended.
    """
    safe = resolve(ctx.repo, inp.path, ctx.sensitive)
    try:
        top = open_nofollow(ctx.repo, safe, directory=True)
    except FileNotFoundError:
        raise HandlerError(f"directory not found: {inp.path}") from None
    except PermissionError:
        raise HandlerError(f"permission denied: {inp.path}") from None
    entries: list[dict[str, Any]] = []
    budget = ctx.output_cap
    truncated = False
    # Queued directories are kept as paths, not fds, and reopened from the root fd one at a time.
    queue: deque[tuple[str, int]] = deque()
    fd: int | None = top
    rel_dir, level = safe.rel, 1
    while fd is not None:
        try:
            with os.scandir(fd) as it:
                items = sorted(it, key=lambda e: e.name)
            for e in items:
                rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
                if e.name.lower() == ".git" or ctx.sensitive.matches(rel):
                    continue
                item: dict[str, Any] = {"path": rel, "type": _kind(e)}
                if item["type"] == "file":
                    try:
                        item["size"] = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue  # removed (or made unreachable) since scandir: skipped
                cost = len(rel) + _ENTRY_OVERHEAD
                if len(entries) >= inp.max_entries or cost > budget:
                    truncated = True
                    break
                budget -= cost
                entries.append(item)
                if item["type"] == "dir":
                    if has_git_entry(fd, e.name):
                        item["nested_repo"] = True
                    elif level < inp.depth:
                        queue.append((rel, level + 1))
        finally:
            os.close(fd)
        fd = None
        while queue and fd is None and not truncated:
            rel_dir, level = queue.popleft()
            try:
                fd = open_nofollow(ctx.repo, SafePath(rel_dir), directory=True)
            except (OSError, PathDenied):
                pass  # unreadable, or swapped for a symlink since it was listed: listed, not descended
    return {"path": safe.rel, "entries": entries, "truncated": truncated}


LIST = Capability(
    name="filesystem.list",
    input=ListInput,
    handler=list_dir,
    risk=Risk.READ,
    description=("List a directory in the repository (bounded depth and entries). Symlinks are reported, "
                 "not followed; .git, sensitive files and the contents of nested repositories are hidden."),
    audit_fields=("path", "depth", "max_entries"),
)
