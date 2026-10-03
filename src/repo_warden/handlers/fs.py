"""Filesystem capabilities."""

from __future__ import annotations

import io
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..capabilities import Capability, HandlerCtx, HandlerError, Risk
from ..paths import open_nofollow, resolve

READ_DEFAULT_BYTES = 256 * 1024
READ_MAX_BYTES = 1024 * 1024
BINARY_SNIFF = 8192


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
