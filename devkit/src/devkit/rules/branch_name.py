"""Style rule: branches the agent creates must match the configured naming pattern (ported from GrandFlow)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..events import Event, Finding
from . import Rule

# git checkout -b/-B <name>, git switch -c/-C <name>, or bare git branch <name>
# Gaps are spaces or tabs only: a newline ends the command (`git branch\necho done` lists branches).
CREATE_RE = re.compile(r"\bgit[ \t]+(?:checkout[ \t]+-[bB]|switch[ \t]+-[cC]|branch)[ \t]+(?:--[ \t]+)?(\S+)")
# a flag (`git branch -d old`), a shell operator (`git branch | cat`) or a redirection (`git branch 2>/dev/null`)
_NOT_A_NAME = re.compile(r"^(?:[-|;&<>()]|\d+[<>])")


def created_branches(command: str) -> list[str]:
    names = (m.group(1).strip("'\"") for m in CREATE_RE.finditer(command))
    return [n for n in names if n and not _NOT_A_NAME.match(n)]


def problem(name: str, settings: Mapping[str, Any]) -> str | None:
    """Why `name` breaks the convention, or None; the base branch is always accepted."""
    if name == settings["base"] or re.search(settings["pattern"], name):
        return None
    return f"branch name '{name}' does not match the pattern '{settings['pattern']}'."


class BranchName(Rule):
    name = "branch_name"
    kinds = frozenset({"shell"})
    settings = {"pattern": r"^(feat|fix|chore)/.+$", "base": "main"}

    def validate(self, key: str, value: Any) -> str | None:
        if key == "pattern":
            try:
                re.compile(value)
            except re.error as e:
                return f"invalid regular expression: {e}"
        return None

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        problems = [problem(n, settings) for n in created_branches(event.command or "")]
        return [Finding(self.name, p + " Use a name that follows the repo's branch convention.") for p in problems if p]


RULE = BranchName()
