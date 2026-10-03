"""Hardened git invocations: no system/global config, no pager, hooks, fsmonitor or network.

Every git call in repo-warden (the top-level check in repo.py and the git capabilities) builds its
argv and environment here, so the hardening is applied in one place.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
)


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
