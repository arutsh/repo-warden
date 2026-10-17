"""Rule base class and the registry; rule modules are imported only when a config or event needs them."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, ClassVar

from ..events import Event, Finding, Level

REGISTRY: dict[str, str] = {
    "secrets": "devkit.rules.secrets",
}


class Rule:
    name: ClassVar[str]
    guard: ClassVar[bool] = False  # the repo layer may only tighten a guard rule
    default_level: ClassVar[Level] = Level.OFF
    max_level: ClassVar[Level] = Level.BLOCK
    phases: ClassVar[frozenset[str]] = frozenset({"pre", "check"})
    kinds: ClassVar[frozenset[str]] = frozenset({"shell", "read", "write", "edit"})
    content: ClassVar[bool] = False  # reads an event's before/after text, so adapters must load it
    settings: ClassVar[Mapping[str, Any]] = {}  # setting -> default; the default's type is the setting's type
    extend: ClassVar[frozenset[str]] = frozenset()  # list settings each layer adds to instead of replacing
    repo_settings: ClassVar[frozenset[str] | None] = None  # for guard rules: settings the repo may set

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        raise NotImplementedError


def get_rule(name: str) -> Rule:
    return importlib.import_module(REGISTRY[name]).RULE


def any_rule(*, phases: str | None = None, content: bool | None = None) -> bool:
    """Whether some registered rule runs in `phases` and/or needs file content."""
    return any((phases is None or phases in r.phases) and (content is None or r.content == content)
               for r in map(get_rule, REGISTRY))
