"""Claude Code hooks: tool and turn hook JSON on stdin -> events; outcome -> Claude's hook JSON."""

from __future__ import annotations

from typing import Any

from ..events import Event, Outcome, Phase
from . import needs_content, read_text
from . import resolve as _abs

PRE_MATCHER = "Bash|Read|Grep|Glob|Write|Edit|MultiEdit"
POST_MATCHER = "Write|Edit|MultiEdit"
HOOK_EVENTS = {"pre-tool-use": ("PreToolUse", "pre", PRE_MATCHER), "post-tool-use": ("PostToolUse", "post", POST_MATCHER),
               "session-start": ("SessionStart", "session-start", None),
               "user-prompt-submit": ("UserPromptSubmit", "prompt", None), "stop": ("Stop", "stop", None)}
TURN_PHASES = frozenset({"session-start", "prompt", "stop"})


def _replace(text: str, old: str, new: str, replace_all: bool) -> str | None:
    if not old or old not in text:
        return None
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def events(payload: dict[str, Any], phase: Phase, *, content: bool | None = None) -> list[Event]:
    """content: load file text for write/edit events; by default only when an enabled rule reads it."""
    if phase in TURN_PHASES:
        return [turn_event(payload, phase)]
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or None
    if tool == "Bash":
        return [Event("shell", phase, command=ti.get("command") or "", cwd=cwd)]
    if tool == "Read":
        return [Event("read", phase, path=_abs(ti.get("file_path"), cwd), cwd=cwd)]
    if tool in ("Grep", "Glob"):
        # Glob's `pattern` is a path glob; Grep's `pattern` is a regex over contents, so only its `glob`.
        globs = [ti.get("glob"), ti.get("pattern") if tool == "Glob" else None]
        return [Event("read", phase, path=p, cwd=cwd) for p in (ti.get("path"), *globs) if p]
    if tool not in ("Write", "Edit", "MultiEdit"):
        return []
    path = _abs(ti.get("file_path"), cwd)
    if not path:
        return []
    bare = Event("write" if tool == "Write" and phase != "post" else "edit", phase, path=path, cwd=cwd)
    if not (needs_content(bare) if content is None else content):
        return [bare]
    on_disk = read_text(path)
    if phase == "post":  # the tool has run: what's on disk is the result
        return [Event("edit", phase, path=path, after=on_disk, cwd=cwd)]
    if tool == "Write":
        return [Event("write", phase, path=path, before=on_disk, after=ti.get("content") or "", cwd=cwd)]
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    before, after = on_disk, on_disk
    for e in edits or []:
        replaced = _replace(after, e.get("old_string") or "", e.get("new_string") or "", bool(e.get("replace_all")))
        if replaced is None:  # can't place the edit: judge the new text on its own, as GrandFlow did
            before, after = "", "\n".join(x.get("new_string") or "" for x in edits)
            return [Event("edit", phase, path=path, before=before, after=after, cwd=cwd, fragment=True)]
        after = replaced
    return [Event("edit", phase, path=path, before=before, after=after, cwd=cwd)]


def turn_event(payload: dict[str, Any], phase: Phase) -> Event:
    tasks = payload.get("background_tasks")
    meta = {"permission_mode": payload.get("permission_mode"), "source": payload.get("source"),
            "background_task_count": len(tasks) if isinstance(tasks, list) else 0,
            "stop_hook_active": bool(payload.get("stop_hook_active"))}
    if phase == "prompt":  # a finished background task is delivered as a prompt; the turn goes on
        text = payload.get("prompt")
        meta["task_notification"] = isinstance(text, str) and text.lstrip().startswith("<task-notification>")
    sid = payload.get("session_id")
    return Event("turn", phase, cwd=payload.get("cwd") or None, session_id=sid if isinstance(sid, str) else None,
                 meta=meta)


def _lines(findings) -> str:
    return "\n".join(f"[devkit {f.rule}] {f.message}" for f in findings)


_TURN_HOOKS = frozenset(e for e, phase, _ in HOOK_EVENTS.values() if phase in TURN_PHASES)


def response(outcome: Outcome, hook_event: str) -> dict[str, Any] | None:
    """Claude's hook output, or None for "no opinion". Never an allow decision: that would skip the user's prompt."""
    notes = "\n".join(outcome.notes)
    if outcome.blocked:
        reason = _lines(outcome.blocks + outcome.warnings)
        if hook_event == "PreToolUse":
            out: dict[str, Any] = {"hookSpecificOutput": {
                "hookEventName": hook_event, "permissionDecision": "deny", "permissionDecisionReason": reason}}
        else:
            out = {"decision": "block", "reason": reason}
        if notes:
            out["systemMessage"] = notes
        return out
    if outcome.warnings or notes:
        text = "\n".join(t for t in (_lines(outcome.warnings), notes) if t)
        if hook_event in _TURN_HOOKS:  # additionalContext would continue the turn, not just tell the user
            return {"systemMessage": text}
        return {"systemMessage": text, "hookSpecificOutput": {"hookEventName": hook_event, "additionalContext": text}}
    return None
