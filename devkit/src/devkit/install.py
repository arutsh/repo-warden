"""Add or remove devkit's hook entries in an agent's user config without touching anything else."""

from __future__ import annotations

import json
import os
import re
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import rules
from .adapters import claude

HOOK_TIMEOUT_S = 15
BACKUP_SUFFIX = ".devkit-backup"
_MANAGED = re.compile(r"(?:^|[/\\'\"\s])devkit(?:\.exe)?['\"]?\s+hook\s+(\w+)\b", re.IGNORECASE)


class InstallError(Exception):
    pass


@dataclass(frozen=True)
class Agent:
    name: str
    config_path: Callable[[], Path]
    hooks: Callable[[str], dict[str, list[dict[str, Any]]]]  # devkit exe -> {event: [matcher group]}


def _claude_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def _command(exe: str, agent: str, event: str) -> dict[str, Any]:
    return {"type": "command", "command": f"{shlex.quote(exe)} hook {agent} {event}", "timeout": HOOK_TIMEOUT_S}


def _claude_hooks(exe: str) -> dict[str, list[dict[str, Any]]]:
    """Only the events some rule runs in: each registered hook is a process per tool call."""
    return {hook_event: [{"matcher": matcher, "hooks": [_command(exe, "claude", name)]}]
            for name, (hook_event, phase, matcher) in claude.HOOK_EVENTS.items() if rules.any_rule(phases=phase)}


AGENTS: dict[str, Agent] = {"claude": Agent("claude", _claude_path, _claude_hooks)}


def is_managed(hook: Any, agent: str) -> bool:
    if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
        return False
    m = _MANAGED.search(hook["command"])
    return bool(m and m.group(1) == agent)


def load_json(path: Path) -> tuple[dict[str, Any], str | None]:
    """The parsed config and its original text (None if the file doesn't exist)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as e:
        raise InstallError(f"cannot read {path}: {e}") from None
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as e:
        raise InstallError(f"{path} is not valid JSON ({e}); fix it first, nothing was changed") from None
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise InstallError(f"{path}: unexpected structure (a top-level object with a 'hooks' object expected)")
    return data, text


def strip(data: dict[str, Any], agent: str) -> dict[str, Any]:
    """A copy of `data` without devkit's entries for `agent`; groups and events left empty by that are dropped."""
    data = json.loads(json.dumps(data))
    hooks = data.get("hooks")
    if not isinstance(hooks, dict) or not hooks:  # an empty `hooks` the user had stays
        return data
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            inner = g.get("hooks") if isinstance(g, dict) else None
            if isinstance(inner, list) and any(is_managed(h, agent) for h in inner):
                rest = [h for h in inner if not is_managed(h, agent)]
                if rest:
                    kept.append({**g, "hooks": rest})
            else:
                kept.append(g)
        if kept or not groups:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del data["hooks"]
    return data


def installed(agent: str) -> bool:
    try:
        data, _ = load_json(AGENTS[agent].config_path())
    except InstallError:
        return False
    return strip(data, agent) != data


def write_json(path: Path, data: dict[str, Any], original: str | None) -> None:
    """Atomic replace; the first time devkit changes an existing file, its original is kept as a backup.

    A symlinked config (dotfiles) is written through to its target; the backup sits next to the link.
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    path = Path(os.path.realpath(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if original is not None else 0o600
    if original is not None and not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)  # may hold tokens: keep the mode
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(original)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def install(agent: str, exe: str) -> str:
    target = AGENTS[agent]
    path = target.config_path()
    data, original = load_json(path)
    new = strip(data, agent)
    hooks = new.setdefault("hooks", {})
    for event, groups in target.hooks(exe).items():
        existing = hooks.setdefault(event, [])
        if not isinstance(existing, list):
            raise InstallError(f"{path}: hooks.{event} is not a list; fix it first, nothing was changed")
        existing.extend(groups)
    if new == data:
        return f"{agent}: already installed in {path}"
    write_json(path, new, original)
    return f"{agent}: installed in {path}"


def uninstall(agent: str) -> str:
    path = AGENTS[agent].config_path()
    data, original = load_json(path)
    new = strip(data, agent)
    if new == data:
        return f"{agent}: not installed in {path}"
    write_json(path, new, original)
    return f"{agent}: removed from {path}"
