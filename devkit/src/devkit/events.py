"""Agent-neutral events, findings and outcomes shared by every rule, adapter and `devkit check`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from types import MappingProxyType
from typing import Any, Literal

Kind = Literal["shell", "read", "write", "edit", "turn"]
# "turn" events come in the turn phases only: session-start, prompt (a user prompt) and stop
Phase = Literal["pre", "post", "check", "session-start", "prompt", "stop"]


class Level(IntEnum):
    OFF = 0
    WARN = 1
    BLOCK = 2

    @classmethod
    def parse(cls, value: object) -> "Level":
        if value is False:  # YAML 1.1 reads a bare `off` as false
            return cls.OFF
        if isinstance(value, str) and value.lower() in ("off", "warn", "block"):
            return cls[value.upper()]
        raise ValueError(f"invalid level {value!r}: use off, warn or block")

    def __str__(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Event:
    kind: Kind
    phase: Phase = "pre"
    path: str | None = None  # absolute where known; a glob for search tools
    command: str | None = None
    before: str | None = None
    after: str | None = None
    cwd: str | None = None
    fragment: bool = False  # before/after are loose snippets (an edit that couldn't be placed), not the file
    session_id: str | None = None
    # turn events: permission_mode, background_task_count, stop_hook_active
    meta: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", MappingProxyType(dict(self.meta)))


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    path: str | None = None
    line: int | None = None


@dataclass
class Outcome:
    findings: list[tuple[Level, Finding]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # config problems a hook reports but survives

    @property
    def blocked(self) -> bool:
        return any(level is Level.BLOCK for level, _ in self.findings)

    @property
    def warnings(self) -> list[Finding]:
        return [f for level, f in self.findings if level is Level.WARN]

    @property
    def blocks(self) -> list[Finding]:
        return [f for level, f in self.findings if level is Level.BLOCK]
