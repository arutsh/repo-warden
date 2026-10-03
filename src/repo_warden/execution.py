"""Execution backends: run a subprocess from an argv with a timeout, capped output and an allowlisted env."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ENV_ALLOWLIST: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ")
_CHUNK = 64 * 1024


@dataclass(frozen=True)
class ExecResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool
    duration_ms: int


class ExecutionBackend(Protocol):
    name: str

    async def run(self, argv: Sequence[str], *, cwd: Path, timeout_s: float,
                  max_output: int, env: Mapping[str, str]) -> ExecResult: ...


def build_env(extra: Mapping[str, str]) -> dict[str, str]:
    """Allowlisted variables from the broker's environment plus the capability's explicit ones."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update(extra)
    return env


async def _read_capped(stream: asyncio.StreamReader, max_output: int) -> tuple[bytes, bool]:
    """Keep at most `max_output` bytes; keep draining past it so the child never blocks on a full pipe."""
    buf = bytearray()
    truncated = False
    while chunk := await stream.read(_CHUNK):
        room = max_output - len(buf)
        if room > 0:
            buf += chunk[:room]
        if len(chunk) > room:
            truncated = True
    return bytes(buf), truncated


class DirectBackend:
    """Runs processes directly, with no OS containment verified."""

    name = "direct"

    async def run(self, argv: Sequence[str], *, cwd: Path, timeout_s: float,
                  max_output: int, env: Mapping[str, str]) -> ExecResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty sequence of strings")
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=build_env(env),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None

        async def collect() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
            out, err = await asyncio.gather(_read_capped(proc.stdout, max_output),
                                            _read_capped(proc.stderr, max_output))
            await proc.wait()
            return out, err

        try:
            (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(collect(), timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(None, b"", b"", True, False, _ms(start))
        return ExecResult(proc.returncode, out, err, False, out_trunc or err_trunc, _ms(start))


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
