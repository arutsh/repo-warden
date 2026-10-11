"""Execution backends: run a subprocess from an argv with a timeout, capped output and an allowlisted env."""

from __future__ import annotations

import asyncio
import errno
import os
import pwd
import signal
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ENV_ALLOWLIST: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ")
CPLT_MARKER = "__CPLT_WRAPPED"
_PR_GET_SECCOMP = 21
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


class ContainmentError(Exception):
    """The cplt backend could not verify that it runs inside a cplt sandbox."""


def build_env(extra: Mapping[str, str], home: str) -> dict[str, str]:
    """Allowlisted variables from the broker's environment, the capability's explicit ones, and an empty HOME."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update(extra)
    env["HOME"] = home
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


def _killpg(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _inside(root: str, path: str) -> bool:
    return os.path.commonpath([root, path]) == root


class DirectBackend:
    """Runs processes directly, with no OS containment verified.

    Each process gets its own session (so a timeout kills it and everything it started), stdin from
    /dev/null, an allowlisted environment with an empty temporary HOME, and a cwd inside `root`.
    """

    name = "direct"

    def __init__(self, root: str | os.PathLike[str]):
        self.root = os.path.realpath(root)

    async def run(self, argv: Sequence[str], *, cwd: Path, timeout_s: float,
                  max_output: int, env: Mapping[str, str]) -> ExecResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty sequence of strings")
        real_cwd = os.path.realpath(cwd)
        if not _inside(self.root, real_cwd):
            raise ValueError(f"working directory {real_cwd} is outside the repository {self.root}")
        with tempfile.TemporaryDirectory(prefix="repo-warden-home-") as home:
            return await self._run(argv, real_cwd, timeout_s, max_output, build_env(env, home))

    async def _run(self, argv: Sequence[str], cwd: str, timeout_s: float, max_output: int,
                   env: dict[str, str]) -> ExecResult:
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = proc.stdout, proc.stderr
        assert stdout is not None and stderr is not None

        async def collect() -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
            out, err = await asyncio.gather(_read_capped(stdout, max_output),
                                            _read_capped(stderr, max_output))
            await proc.wait()
            return out, err

        try:
            (out, out_trunc), (err, err_trunc) = await asyncio.wait_for(collect(), timeout_s)
        except TimeoutError:
            # The child leads its own process group (start_new_session), so this also kills grandchildren.
            _killpg(proc.pid)
            await proc.wait()
            return ExecResult(None, b"", b"", True, False, _ms(start))
        except BaseException:
            _killpg(proc.pid)
            raise
        return ExecResult(proc.returncode, out, err, False, out_trunc or err_trunc, _ms(start))


# --- containment probes (design D8) -------------------------------------------------------------

@dataclass(frozen=True)
class Probe:
    name: str
    ok: bool
    detail: str


def probe_marker(environ: Mapping[str, str]) -> Probe:
    present = CPLT_MARKER in environ
    return Probe("marker", present, f"{CPLT_MARKER} is {'set' if present else 'not set'}")


def probe_seccomp(mode: int | None, platform: str) -> Probe:
    if not platform.startswith("linux"):
        return Probe("seccomp", True, f"skipped on {platform}")
    if mode is None:
        return Probe("seccomp", False, "seccomp mode is unavailable (prctl failed)")
    return Probe("seccomp", mode == 2, f"seccomp mode {mode} (need 2, filter)")


def probe_parent_write(parent: str) -> Probe:
    """Creating a file next to the repository must be refused by the sandbox."""
    target = os.path.join(parent, f".repo-warden-probe-{os.getpid()}-{time.monotonic_ns()}")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return Probe("parent_write", True, f"write in {parent} refused ({errno.errorcode[e.errno]})")
        return Probe("parent_write", False, f"write in {parent} failed unexpectedly: {e.strerror}")
    os.close(fd)
    try:
        os.unlink(target)
    except OSError:
        pass
    return Probe("parent_write", False, f"write in {parent} succeeded")


def probe_ssh(home: str) -> Probe:
    ssh = os.path.join(home, ".ssh")
    if not os.path.lexists(ssh):
        return Probe("ssh", True, f"{ssh} does not exist")
    try:
        os.listdir(ssh)
    except PermissionError:
        return Probe("ssh", True, f"{ssh} is unreadable")
    except OSError as e:
        return Probe("ssh", False, f"{ssh} failed unexpectedly: {e.strerror}")
    return Probe("ssh", False, f"{ssh} is readable")


def _seccomp_mode() -> int | None:
    # prctl rather than /proc/self/status: cplt's Landlock ruleset denies /proc inside the sandbox.
    import ctypes

    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError:
        return None
    mode = libc.prctl(_PR_GET_SECCOMP, 0, 0, 0, 0)
    return None if mode < 0 else int(mode)


def _account_home() -> str:
    # The account's home from the password database, not $HOME, which the caller controls.
    try:
        return pwd.getpwuid(os.geteuid()).pw_dir
    except KeyError:
        return os.path.expanduser("~")


def verify_containment(root: str | os.PathLike[str], *, environ: Mapping[str, str] | None = None,
                       seccomp_mode: int | None = None, platform: str | None = None,
                       home: str | None = None) -> list[Probe]:
    """Run every probe; containment is verified only if all of them pass. Arguments override inputs for tests."""
    real = os.path.realpath(root)
    platform = platform or sys.platform
    if seccomp_mode is None and platform.startswith("linux"):
        seccomp_mode = _seccomp_mode()
    return [
        probe_marker(os.environ if environ is None else environ),
        probe_seccomp(seccomp_mode, platform),
        probe_parent_write(os.path.dirname(real)),
        probe_ssh(home or _account_home()),
    ]


def require_containment(probes: Sequence[Probe]) -> None:
    failed = [p for p in probes if not p.ok]
    if failed:
        reasons = "; ".join(f"{p.name}: {p.detail}" for p in failed)
        raise ContainmentError(
            f"cplt containment not verified (failed probe{'s' if len(failed) > 1 else ''}: {reasons}). "
            "Start repo-warden inside cplt, or run with --backend direct to start without "
            "verified containment. See the README sections \"Run\" and \"Containment: broker vs cplt\".")


class CpltBackend(DirectBackend):
    """Runs processes directly inside an already-verified cplt sandbox, which every child inherits."""

    name = "cplt"

    def __init__(self, root: str | os.PathLike[str]):
        super().__init__(root)
        require_containment(verify_containment(self.root))


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _probe_main(argv: Sequence[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m repo_warden.execution")
    p.add_argument("--probe", action="store_true", required=True, help="run the cplt containment probes")
    p.add_argument("--repo", default=os.getcwd(), help="repository whose parent is probed (default: cwd)")
    args = p.parse_args(argv)
    probes = verify_containment(args.repo)
    for pr in probes:
        print(f"{'PASS' if pr.ok else 'FAIL'} {pr.name}: {pr.detail}")
    ok = all(pr.ok for pr in probes)
    print("containment verified" if ok else "containment NOT verified")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_probe_main(sys.argv[1:]))
