"""Guard rule: refuse the agent switching devkit off (`devkit uninstall`, editing its config or hook files).

Early feedback only, like `secrets`: a command that doesn't spell out `devkit uninstall` gets past it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from ..events import Event, Finding, Level
from . import Rule

# `devkit` (or devkit.exe, any path, optionally quoted) as a word, then `uninstall`; like install._MANAGED.
UNINSTALL_RE = re.compile(r"(?:^|[/\\'\"\s;&|(`])devkit(?:\.exe)?['\"]?\s+uninstall\b", re.IGNORECASE)

ASK = "Ask the user to make this change themselves, from their own terminal."


def protected_paths() -> dict[str, str]:
    """Resolved path -> what it is: the devkit user config and each agent's hook file."""
    from .. import config, install

    named = {str(config.user_config_path()): "the devkit user config"}
    named.update({str(a.config_path()): f"{name}'s hook config" for name, a in install.AGENTS.items()})
    return {os.path.realpath(path): what for path, what in named.items()}


class SelfGuard(Rule):
    name = "self_guard"
    guard = True
    default_level = Level.BLOCK
    phases = frozenset({"pre"})
    kinds = frozenset({"shell", "write", "edit"})
    repo_settings = frozenset()

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        if event.kind == "shell":
            if UNINSTALL_RE.search(event.command or ""):
                return [Finding(self.name, f"this command would remove devkit's hooks. {ASK}")]
            return []
        if not event.path:
            return []
        what = protected_paths().get(os.path.realpath(os.path.expanduser(event.path)))
        if what:
            return [Finding(self.name, f"this {event.kind} changes {what} ({event.path}), which could switch "
                                       f"devkit off. {ASK}", path=event.path)]
        return []


RULE = SelfGuard()
