"""The `review` rule's turn hooks: snapshot the tree, then run the state machine on the locked state."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .. import engine, gitsafe
from ..events import Event, Finding, Level
from ..rules.secrets import DEFAULT_PATTERNS
from . import machine, snapshot, state


def secret_patterns(cfg) -> list[str]:
    return [*DEFAULT_PATTERNS, *cfg.rules["secrets"].settings["patterns"]]


def handle(event: Event, settings: Mapping[str, Any], *, now: datetime | None = None) -> list[Finding]:
    sid = event.session_id
    if not sid or (event.phase == "stop" and machine.never_blocks(event.meta)):
        return []
    top = engine.event_top(event)
    if top is None:
        return []
    deadline = time.monotonic() + gitsafe.TIMEOUT_S  # one budget for the lock and every git call

    def left() -> float:
        return deadline - time.monotonic()

    cfg = engine.load_config(top, strict=False)
    secrets = secret_patterns(cfg)
    cur = snapshot.snapshot(top, secrets, timeout_s=left())
    now = now or datetime.now(timezone.utc)
    decision = machine.ALLOW
    with state.locked(top, now=now, timeout_s=left()) as data:
        if event.phase == "session-start":
            new = machine.on_session_start(data, sid, cur, event.meta.get("source"), now)
        elif event.phase == "prompt":
            new, decision = machine.on_prompt(data, sid, cur, now)
        elif snapshot.missing(top, machine.trees(data, sid), timeout_s=left()):
            new = machine.forget(data, sid, cur, now)
        else:
            new, decision = machine.on_stop(
                data, sid, cur, level=str(cfg.rules["review"].level), max_rounds=settings["max_rounds"],
                skip_paths=settings["skip_paths"], meta=event.meta, now=now,
                changed=lambda a, b: snapshot.changed_paths(top, a, b, secrets, timeout_s=left()))
        data.clear()
        data.update(new)
    if decision.action == "allow":
        return []
    return [Finding("review", decision.message, max_level=Level.BLOCK if decision.action == "block" else Level.WARN)]
