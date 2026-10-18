"""Guard rule: refuse shell commands, reads and edits that reference a secret-bearing file.

Early feedback only: string matching on a shell command is evadable. cplt and repo-warden enforce.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
from collections.abc import Iterable, Mapping
from typing import Any

from ..events import Event, Finding, Level
from . import Rule

# Kept equal to repo_warden.paths.DEFAULT_SENSITIVE by a parity test.
DEFAULT_PATTERNS: tuple[str, ...] = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_*")

# Whitespace and quotes too: a shell word can hold a whole sub-command (`bash -c "cat .env"`).
_SEPARATORS = re.compile(r"[\s\"'/\\=:<>|;&()$`{},]+")
_FALLBACK_SPLIT = re.compile(r"[\s\"']+")
_GLOB_CHARS = set("*?[]")


def components(text: str) -> list[str]:
    """Candidate file names in a shell word or path: every component between separators."""
    return [c for c in _SEPARATORS.split(text) if c and c not in (".", "..")]


def shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command, comments=False)
    except ValueError:  # unbalanced quotes: still look at every word
        return [w for w in _FALLBACK_SPLIT.split(command) if w]


def _sample(pattern: str) -> str:
    return pattern.replace("*", "x").replace("?", "x")


def match(name: str, patterns: Iterable[str], exempt: Iterable[str]) -> str | None:
    """The pattern `name` matches, case-insensitively, unless an exemption covers it.

    A glob `name` (from a search tool) matches when it could select a secret file, but only if it
    has some literal text, so `*` or `*.*` on their own are not treated as secret references.
    """
    low = name.lower()
    if any(fnmatch.fnmatchcase(low, e.lower()) for e in exempt):
        return None
    if _GLOB_CHARS & set(low):
        if not any(c.isalnum() for c in low):
            return None
        return next((p for p in patterns if fnmatch.fnmatchcase(_sample(p.lower()), low)), None)
    return next((p for p in patterns if fnmatch.fnmatchcase(low, p.lower())), None)


class Secrets(Rule):
    name = "secrets"
    guard = True
    default_level = Level.BLOCK
    settings = {"patterns": [], "exempt": []}
    extend = frozenset({"patterns"})
    repo_settings = frozenset({"patterns"})

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        patterns = (*DEFAULT_PATTERNS, *settings["patterns"])
        if event.kind == "shell":
            names = [c for w in shell_words(event.command or "") for c in components(w)]
            what = "this command"
        else:
            names = components(event.path or "")
            what = {"read": "this read", "write": "this write", "edit": "this edit"}[event.kind]
        for name in names:
            pattern = match(name, patterns, settings["exempt"])
            if pattern:
                return [Finding(self.name, (
                    f"{what} references '{name}', which matches the secret pattern '{pattern}'. "
                    "Do not read, print or change secret files, not even with a presence check; "
                    "ask the user for what you need instead."), path=event.path)]
        return []


RULE = Secrets()
