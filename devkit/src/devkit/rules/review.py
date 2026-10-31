"""Style rule: a turn that changed code goes through review rounds (run by the agent's skill) before it stops."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..events import Event, Finding
from . import Rule

FOCUSES = ("correctness", "rules", "tests", "security", "history")
MODELS = ("opus", "sonnet", "haiku", "inherit")
MAX_ROUNDS = 5


class Review(Rule):
    name = "review"
    phases = frozenset({"session-start", "prompt", "stop"})
    kinds = frozenset({"turn"})
    settings = {
        "max_rounds": 3,
        "skip_paths": [],
        "reviewers": {"correctness": "opus", "rules": "sonnet", "tests": "sonnet", "security": "sonnet",
                      "history": "sonnet"},
        "followup_reviewers": ["correctness"],
        "checker": "opus",
        "log": True,
        "log_content": False,
        "log_retention_days": 30,
    }
    user_only = frozenset({"log_content"})  # an agent-writable .devkit.yaml must not widen what logs keep

    def validate(self, key: str, value: Any) -> str | None:
        if key == "max_rounds" and not 1 <= value <= MAX_ROUNDS:
            return f"expected 1 to {MAX_ROUNDS}"
        if key == "log_retention_days" and value < 1:
            return "expected at least 1"
        if key == "checker" and value not in MODELS:
            return f"unknown model {value!r} (use {', '.join(MODELS)})"
        if key == "followup_reviewers":
            return _focuses(value) or (None if value else "expected at least one focus")
        if key == "reviewers":
            if not value:
                return "expected at least one focus"
            bad = next((m for m in value.values() if m not in MODELS), None)
            return _focuses(value) or (f"unknown model {bad!r} (use {', '.join(MODELS)})" if bad else None)
        return None

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        return []


def _focuses(names: Any) -> str | None:
    bad = next((n for n in names if n not in FOCUSES), None)
    return f"unknown focus {bad!r} (use {', '.join(FOCUSES)})" if bad is not None else None


RULE = Review()
