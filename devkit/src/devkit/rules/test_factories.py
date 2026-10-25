"""Style rule: nudge an edited test file toward its factories (ported from GrandFlow). Warns, never blocks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..adapters import read_text
from ..events import Event, Finding, Level
from ..repo import find_top
from . import Rule

FACTORY_DEF_RE = re.compile(r"class\s+(\w+)\(factory\.\w+\):\s*class Meta:\s*model\s*=\s*(\w+)", re.DOTALL)


def factories_dir(path: Path, top: Path | None) -> Path | None:
    """The nearest `tests/factories/` at or above the file, inside its repo when it has one."""
    for parent in path.parents:
        if top and not parent.is_relative_to(top):
            break
        if parent.name == "tests" and (parent / "factories").is_dir():
            return parent / "factories"
    return None


def model_factories(directory: Path) -> dict[str, str]:
    """Model name -> the first factory declaring it, from `class XFactory(factory.*): class Meta: model = X`."""
    mapping: dict[str, str] = {}
    for f in sorted(directory.glob("*.py")):
        for factory_name, model_name in FACTORY_DEF_RE.findall(read_text(str(f))):
            mapping.setdefault(model_name, factory_name)
    return mapping


def _line(text: str, m: re.Match) -> int:
    return text.count("\n", 0, m.start()) + 1


class TestFactories(Rule):
    name = "test_factories"
    max_level = Level.WARN
    phases = frozenset({"post", "check"})
    kinds = frozenset({"write", "edit"})
    content = True
    settings = {"discouraged_calls": []}

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        if not event.path or not event.after or not event.path.endswith(".py"):
            return []
        path = Path(event.path)
        top = find_top(event.path)
        parts = path.relative_to(top).parts if top and path.is_relative_to(top) else path.parts  # not ~/tests/...
        if "tests" not in parts or "factories" in parts:
            return []
        text, found = event.after, []
        for call in settings["discouraged_calls"]:
            m = re.search(rf"(?<!def )(?<![\w.]){re.escape(call)}\(", text)
            if m:
                found.append(Finding(self.name, (
                    f"{call}(...) is listed in discouraged_calls; prefer a factory in files you touch."),
                    path=event.path, line=_line(text, m)))
        directory = factories_dir(path, top)
        for model, factory in (model_factories(directory) if directory else {}).items():
            m = re.search(rf"(?<!class )(?<!\w){re.escape(model)}\(", text)
            if m:
                found.append(Finding(self.name, (
                    f"raw {model}(...) construction: {factory} already exists for this model; "
                    f"prefer {factory}.build(...)."), path=event.path, line=_line(text, m)))
        return found


RULE = TestFactories()
