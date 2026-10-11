"""Hardened git invocations: no system/global config, no pager, hooks, fsmonitor, drivers or network.

Every git call in repo-warden (the top-level check in repo.py and the git capabilities) builds its
argv and environment here, so the hardening is applied in one place.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from .execution import ExecutionBackend

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


class GitConfigError(Exception):
    """The repository's config could not be enumerated safely; the git call must not run."""


def git_env() -> dict[str, str]:
    """Environment variables to add on top of the backend's allowlisted environment."""
    return dict(GIT_ENV)


def git_argv(*args: str, extra_overrides: Sequence[str] = ()) -> list[str]:
    """`git --no-pager -c ... <args>` with the base overrides and any extra `key=value` overrides."""
    argv = ["git", "--no-pager"]
    for kv in (*BASE_OVERRIDES, *extra_overrides):
        argv += ["-c", kv]
    argv += list(args)
    return argv


def driver_overrides(names: Iterable[str]) -> list[str]:
    """`-c` values blanking every executing driver key in `names` (as listed by `git config --list --name-only`).

    An empty filter command is skipped by git, so each filter also gets `required=false`; otherwise
    a required filter would fail the call instead of being bypassed.
    """
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


async def repo_overrides(backend: ExecutionBackend, root: str) -> list[str]:
    """List the repository's config keys (which executes nothing) and return the driver overrides.

    Run before every git call: the config is writable from inside the sandbox and can change
    between calls. Includes and a linked worktree's `config.worktree` are resolved by git.
    """
    res = await backend.run(git_argv("config", "--list", "--name-only", "-z"), cwd=Path(root),
                            timeout_s=CONFIG_TIMEOUT_S, max_output=CONFIG_MAX_OUTPUT, env=git_env())
    if res.timed_out or res.truncated or res.returncode != 0:
        raise GitConfigError("refusing to run git: the repository config could not be listed")
    names = [n for n in res.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
    return driver_overrides(names)
