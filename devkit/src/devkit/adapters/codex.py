"""Codex CLI hooks: PreToolUse/PostToolUse JSON on stdin -> events; outcome -> the same hook JSON as Claude's."""

from __future__ import annotations

import shlex
from typing import Any

from .. import patch
from ..events import Event, Phase
from ..rules import any_rule
from . import read_text
from . import resolve as _abs
from .claude import response  # Codex reads Claude's output shape: deny, decision/block, additionalContext

__all__ = ["HOOK_EVENTS", "events", "response"]

PRE_MATCHER = "^(Bash|apply_patch)$"
POST_MATCHER = "^apply_patch$"
HOOK_EVENTS = {"pre-tool-use": ("PreToolUse", "pre", PRE_MATCHER), "post-tool-use": ("PostToolUse", "post", POST_MATCHER)}


def _command(ti: dict[str, Any]) -> str:
    cmd = ti.get("command")
    if isinstance(cmd, list):  # an argv such as ["bash", "-lc", "cat .env"]
        return shlex.join(str(c) for c in cmd)
    return cmd if isinstance(cmd, str) else ""


def events(payload: dict[str, Any], phase: Phase, *, content: bool | None = None) -> list[Event]:
    """content: load file text for write/edit events; by default only when some rule reads it."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or None
    if tool == "Bash":
        return [Event("shell", phase, command=_command(ti), cwd=cwd)]
    if tool != "apply_patch":
        return []
    text = _command(ti)
    try:
        ops = patch.parse(text)
    except patch.PatchError:  # Codex refuses it too; still let `secrets` see the file names in it
        return [Event("shell", phase, command=text, cwd=cwd)]
    load = any_rule(content=True) if content is None else content
    out: list[Event] = []
    for op in ops:
        path = _abs(op.path, cwd)
        dest = _abs(op.move_to, cwd) or path
        if op.kind == "delete" or op.move_to:  # no content, but `secrets` judges the name
            out.append(Event("shell", phase, command=shlex.quote(path), cwd=cwd))
        if op.kind == "delete":
            continue
        if not load:
            out.append(Event("write" if op.kind == "add" and phase != "post" else "edit", phase, path=dest, cwd=cwd))
        elif phase == "post":  # the patch has been applied: what's on disk is the result
            out.append(Event("edit", phase, path=dest, after=read_text(dest), cwd=cwd))
        elif op.kind == "add":
            out.append(Event("write", phase, path=dest, before=read_text(dest), after=op.content, cwd=cwd))
        else:
            before = read_text(path)
            after = patch.apply(before, op.hunks)
            if after is None:  # stale context: judge the hunks' own old and new text
                before, after = patch.fallback(op.hunks)
            out.append(Event("edit", phase, path=dest, before=before, after=after, cwd=cwd))
    return out
