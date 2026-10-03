"""Capability definitions and the registry the broker and the MCP adapter both read."""

from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from .execution import ExecutionBackend
from .paths import SensitiveMatcher
from .repo import RepoContext


class Risk(enum.Enum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"
    EXTERNAL = "external"


class HandlerError(Exception):
    """An expected handler failure (not found, timed out, ...). The message goes back to the agent, scrubbed."""


@dataclass(frozen=True)
class HandlerCtx:
    repo: RepoContext
    backend: ExecutionBackend
    sensitive: SensitiveMatcher
    output_cap: int  # characters, from the policy's output cap for this capability


Handler = Callable[[HandlerCtx, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Capability:
    name: str                       # canonical, dotted: "filesystem.read"
    input: type[BaseModel]          # extra="forbid", bounded fields
    handler: Handler
    risk: Risk
    description: str
    audit_fields: tuple[str, ...]   # validated args copied into the audit record


class Registry:
    def __init__(self, caps: tuple[Capability, ...] | list[Capability] = ()):
        self._caps: dict[str, Capability] = {}
        for cap in caps:
            self.register(cap)

    def register(self, cap: Capability) -> None:
        if cap.name in self._caps:
            raise ValueError(f"capability {cap.name!r} registered twice")
        if cap.input.model_config.get("extra") != "forbid":
            raise ValueError(f"capability {cap.name!r} input model must set extra='forbid'")
        self._caps[cap.name] = cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def __iter__(self) -> Iterator[Capability]:
        return iter(self._caps.values())

    def __len__(self) -> int:
        return len(self._caps)
