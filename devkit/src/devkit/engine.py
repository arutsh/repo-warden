"""Run the configured rules over events and combine their findings; strictest outcome wins."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import config as config_mod
from .events import Event, Level, Outcome
from .repo import find_top
from .rules import Rule, get_rule

_GLOB = set("*?")  # not `[`: real paths have brackets (Next.js `app/[id]/`)


def event_top(event: Event) -> Path | None:
    """The repo an event belongs to: its file's repo when the path is a concrete absolute one, else the cwd's."""
    if event.path and os.path.isabs(event.path) and not _GLOB & set(event.path):
        return find_top(event.path)
    return find_top(event.cwd)


def _active(cfg: config_mod.Config, event: Event) -> Iterator[tuple[Rule, config_mod.RuleConfig]]:
    """The rules that run on `event` under `cfg`: on, in scope, and for this phase and kind."""
    for name, rc in cfg.rules.items():
        if rc.level is Level.OFF:
            continue
        rule = get_rule(name)
        if not rule.guard and not cfg.in_scope:
            continue
        if event.phase not in rule.phases or event.kind not in rule.kinds:
            continue
        yield rule, rc


def needs_content(event: Event) -> bool:
    """Whether a rule enabled for this event's repo reads its before/after text, so the adapter must load it."""
    return any(rule.content for rule, _ in _active(config_mod.load(event_top(event), strict=False), event))


def evaluate(events: Iterable[Event], *, strict: bool = False, user_path: Path | None = None) -> Outcome:
    out = Outcome()
    configs: dict[Path | None, config_mod.Config] = {}
    for event in events:
        top = event_top(event)
        if top not in configs:
            configs[top] = cfg = config_mod.load(top, strict=strict, user_path=user_path)
            out.notes += [f"devkit: ignored invalid config {e}" for e in cfg.errors
                          if f"devkit: ignored invalid config {e}" not in out.notes]
        for rule, rc in _active(configs[top], event):
            level = min(rc.level, rule.max_level)
            out.findings += [(level, f) for f in rule.check(event, rc.settings)]
    return out
