"""Hardened git calls: no system/global config, pager, hooks, fsmonitor, drivers or network.
Copied from `repo_warden.gitcmd` (devkit can't import it); a parity test keeps the two equal."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

GIT_ENV: Mapping[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "GIT_ATTR_NOSYSTEM": "1",
}

BASE_OVERRIDES: tuple[str, ...] = (
    "core.fsmonitor=false",
    "core.hooksPath=/dev/null",
    "core.untrackedCache=false",
    "core.pager=cat",
    "core.sshCommand=false",
    "core.askPass=",
    "credential.helper=",
    "protocol.allow=never",
    "diff.external=",
    "core.attributesFile=/dev/null",
    # Signature verification runs gpg.program; submodule recursion runs git in a repo whose
    # drivers we have not enumerated.
    "log.showSignature=false",
    "diff.ignoreSubmodules=all",
    "status.submoduleSummary=false",
    "submodule.recurse=false",
)

# filter.<x>.(clean|smudge|process), diff.<x>.(command|textconv), merge.<x>.driver. The driver name
# (subsection) may itself contain dots; `git config --list` lowercases section and key only.
_DRIVER_KEY = re.compile(r"^(?:filter\.(?P<filter>.+)\.(?:clean|smudge|process)"
                         r"|diff\..+\.(?:command|textconv)"
                         r"|merge\..+\.driver)$", re.DOTALL)

CONFIG_TIMEOUT_S = 10.0
CONFIG_MAX_OUTPUT = 1024 * 1024
TIMEOUT_S = 20.0  # under the hooks' 30 s, so a slow repo fails open instead of being killed


class GitError(Exception):
    """A git call failed or timed out."""


class GitConfigError(GitError):
    """The repository's config could not be enumerated safely; the git call must not run."""


def git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """This process's env minus inherited `GIT_*` (so `GIT_DIR` etc. can't redirect us), plus `GIT_ENV`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return {**env, **GIT_ENV, **(extra or {})}


def git_argv(*args: str, extra_overrides: Sequence[str] = ()) -> list[str]:
    """`git --no-pager -c ... <args>` with the base overrides and any extra `key=value` overrides."""
    argv = ["git", "--no-pager"]
    for kv in (*BASE_OVERRIDES, *extra_overrides):
        argv += ["-c", kv]
    argv += list(args)
    return argv


def driver_overrides(names: Iterable[str]) -> list[str]:
    """`-c` values blanking every executing driver key in `names`, plus `required=false` per filter
    (git skips an empty filter command, but a required one would fail the call instead)."""
    out: list[str] = []
    filters: list[str] = []
    for name in dict.fromkeys(names):
        m = _DRIVER_KEY.match(name)
        if not m:
            continue
        if "=" in name or "\n" in name:
            # `-c` splits at the first "=": the override would silently miss this key.
            raise GitConfigError(f"refusing to run git: config key {name!r} cannot be overridden")
        out.append(f"{name}=")
        if m["filter"] is not None and m["filter"] not in filters:
            filters.append(m["filter"])
    out += [f"filter.{f}.required=false" for f in filters]
    return out


def repo_overrides(root: str | os.PathLike, timeout_s: float = CONFIG_TIMEOUT_S) -> list[str]:
    """List the repo's config keys (which executes nothing) and return the driver overrides.
    Run before every git call: the config is agent-writable and can change between calls."""
    try:
        res = subprocess.run(git_argv("config", "--list", "--name-only", "-z"), cwd=root, env=git_env(),
                             capture_output=True, timeout=timeout_s)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitConfigError(f"refusing to run git: the repository config could not be listed ({e})") from e
    if res.returncode != 0 or len(res.stdout) > CONFIG_MAX_OUTPUT:
        raise GitConfigError("refusing to run git: the repository config could not be listed")
    return driver_overrides(n for n in res.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n)


def run(root: str | os.PathLike, *args: str, env: Mapping[str, str] | None = None, stdin: bytes = b"",
        timeout_s: float = TIMEOUT_S) -> bytes:
    """Run hardened `git <args>` in `root` and return its stdout; raise `GitError` on failure.
    `timeout_s` covers the config listing and the call together."""
    if timeout_s <= 0:
        raise GitError(f"git {args[0]} not started: out of time")
    start = time.monotonic()
    argv = git_argv(*args, extra_overrides=repo_overrides(root, min(CONFIG_TIMEOUT_S, timeout_s)))
    timeout_s = max(timeout_s - (time.monotonic() - start), 0.001)
    try:
        res = subprocess.run(argv, cwd=Path(root), env=git_env(env), input=stdin, capture_output=True,
                             timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {timeout_s:g} s") from e
    except OSError as e:
        raise GitError(f"git {args[0]} could not run: {e}") from e
    if res.returncode != 0:
        msg = res.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {msg[-1] if msg else f'exit {res.returncode}'}")
    return res.stdout
