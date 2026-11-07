"""Add or remove devkit's hooks (agent user configs, a repo's git pre-commit) without touching anything else."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from . import rules
from .adapters import claude, codex

HOOK_TIMEOUT_S = 15
TURN_HOOK_TIMEOUT_S = 30  # once per prompt or stop, and the review rule may snapshot the tree
BACKUP_SUFFIX = ".devkit-backup"
# `devkit` (or devkit.exe, any path, optionally quoted) as a word, then a subcommand; shared with self_guard.
DEVKIT_RE = r"(?:^|[/\\'\"\s;&|(`])devkit(?:\.exe)?['\"]?\s+"
_MANAGED = re.compile(DEVKIT_RE + r"hook\s+(\w+)\b", re.IGNORECASE)


class InstallError(Exception):
    pass


class PartialInstall(InstallError):
    """Some files couldn't be installed or removed; `done` says what was changed anyway."""

    def __init__(self, done: str, problems: list[str]):
        super().__init__("\n".join(problems))
        self.done, self.problems = done, problems


@dataclass(frozen=True)
class Agent:
    name: str
    config_path: Callable[[], Path]
    hooks: Callable[[str], dict[str, list[dict[str, Any]]]]  # devkit exe -> {event: [matcher group]}
    on_install: str | None = None  # what the user must do next
    warnings: Callable[[], list[str]] = field(default=lambda: [])  # problems that stop the hooks running
    assets_dir: Callable[[], Path] | None = None  # where the skill and agent files go


def _claude_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _codex_warnings() -> list[str]:
    """Codex skips hooks.json when `[features] hooks` (or its old name `codex_hooks`) is false."""
    path = _codex_home() / "config.toml"
    try:
        features = tomllib.loads(path.read_text(encoding="utf-8")).get("features", {})
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return [f"cannot read {path} to check whether hooks are enabled ({e})"]
    enabled = features.get("hooks", features.get("codex_hooks")) if isinstance(features, dict) else None
    if enabled is False:
        return [f"hooks are disabled in {path} ([features] hooks = false); Codex will not run devkit"]
    return []


def _command(exe: str, agent: str, event: str, timeout: int) -> dict[str, Any]:
    return {"type": "command", "command": f"{shlex.quote(exe)} hook {agent} {event}", "timeout": timeout}


def _group(exe: str, agent: str, name: str, matcher: str | None) -> dict[str, Any]:
    """Tool hooks match tool names; turn hooks (no matcher) run on every prompt or stop."""
    if matcher is None:
        return {"hooks": [_command(exe, agent, name, TURN_HOOK_TIMEOUT_S)]}
    return {"matcher": matcher, "hooks": [_command(exe, agent, name, HOOK_TIMEOUT_S)]}


def _hooks(agent: str, adapter: ModuleType) -> Callable[[str], dict[str, list[dict[str, Any]]]]:
    def hooks(exe: str) -> dict[str, list[dict[str, Any]]]:
        """Only the events some rule runs in: each registered hook is a process per tool call."""
        return {hook_event: [_group(exe, agent, name, matcher)]
                for name, (hook_event, phase, matcher) in adapter.HOOK_EVENTS.items() if rules.any_rule(phases=phase)}
    return hooks


AGENTS: dict[str, Agent] = {
    "claude": Agent("claude", _claude_path, _hooks("claude", claude), assets_dir=lambda: _claude_path().parent),
    "codex": Agent("codex", lambda: _codex_home() / "hooks.json", _hooks("codex", codex),
                   on_install="review and trust the new hooks in Codex with /hooks; they don't run until you do",
                   warnings=_codex_warnings),
}


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


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def write_json(path: Path, data: dict[str, Any], original: str | None) -> None:
    """Atomic replace; the first time devkit changes an existing file, its original is kept as a backup.

    A symlinked config (dotfiles) is written through to its target; the backup sits next to the link.
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    path = Path(os.path.realpath(path))
    mode =path.stat().st_mode & 0o777 if original is not None else 0o600
    if original is not None and not backup.exists():
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)  # may hold tokens: keep the mode
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(original)
    _atomic_write(path, json.dumps(data, indent=2) + "\n", mode)


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
    notes = [f"{agent}: warning: {w}" for w in target.warnings()]
    if new == data:
        lines = [f"{agent}: already installed in {path}"]
    else:
        write_json(path, new, original)
        lines = [f"{agent}: installed in {path}", *([f"{agent}: {target.on_install}"] if target.on_install else [])]
    problems = []
    if target.assets_dir:
        base = target.assets_dir()
        written, problems = install_assets(base)
        if problems:
            if written:
                lines.append(f"{agent}: installed {len(written)} of {len(assets())} {SKILL} skill and agent files "
                             f"in {base}")
        else:
            lines.append(f"{agent}: {'installed' if written else 'already installed'} the {SKILL} skill and "
                         f"reviewer agents in {base}")
    text = "\n".join([*lines, *notes])
    if problems:
        raise PartialInstall(text, [f"{agent}: {p}" for p in problems])
    return text


def uninstall(agent: str) -> str:
    target = AGENTS[agent]
    path = target.config_path()
    data, original = load_json(path)
    new = strip(data, agent)
    if new == data:
        lines = [f"{agent}: not installed in {path}"]
    else:
        write_json(path, new, original)
        lines = [f"{agent}: removed from {path}"]
    problems = []
    if target.assets_dir:
        removed, problems = uninstall_assets(base := target.assets_dir())
        if removed:
            lines.append(f"{agent}: removed {len(removed)} {SKILL} skill and agent files from {base}")
    text = "\n".join(lines)
    if problems:
        raise PartialInstall(text, [f"{agent}: {p}" for p in problems])
    return text


# The skill and reviewer agents (Claude). An agent file carries the marker line; the skill's directory
# holds a marker file, so SKILL.md stays as the skill format has it.
SKILL = "devkit-review"
AGENT_MARKER = "<!-- devkit-managed -->"
SKILL_MARKER = ".devkit-managed"
_SKILL_MARKER_TEXT = "devkit-managed: `devkit install` refreshes this skill and `devkit uninstall` removes it\n"


def assets() -> dict[str, str]:
    """The shipped files, by path under the agent's config directory."""
    root = resources.files("devkit") / "claude_assets"
    files = {f"agents/{p.name}": p.read_text(encoding="utf-8")
             for p in sorted((root / "agents").iterdir(), key=lambda p: p.name) if p.name.endswith(".md")}
    files[f"skills/{SKILL}/SKILL.md"] = (root / "skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
    return files


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # missing, a directory, unreadable: not a file devkit can use
        return None


def _is_skill(rel: str) -> bool:
    return rel.startswith("skills/")


def _ours(path: Path, skill: bool) -> bool:
    """devkit wrote it, or may: an agent file with the marker line, a skill file in a marked directory."""
    if skill:
        return (path.parent / SKILL_MARKER).is_file()
    text = _read_text(path)
    return text is not None and AGENT_MARKER in text.splitlines()


def _free(path: Path, skill: bool) -> bool:
    """Nothing of the user's would be overwritten or adopted by writing it."""
    if skill:
        return not path.parent.exists() or (path.parent.is_dir() and not any(path.parent.iterdir()))
    return not path.exists() and not path.is_symlink()


def install_assets(base: Path) -> tuple[list[str], list[str]]:
    """The files written, and why others were refused. An unchanged file isn't rewritten."""
    written, problems = [], []
    for rel, text in assets().items():
        path, skill = base / rel, _is_skill(rel)
        if not (_ours(path, skill) or _free(path, skill)):
            what = "its directory has no " + SKILL_MARKER if skill else "no devkit marker"
            problems.append(f"{path} was not written by devkit ({what}); left unchanged")
            continue
        try:
            if skill and not (path.parent / SKILL_MARKER).is_file():
                # First, so a SKILL.md devkit wrote is never left without its marker.
                _atomic_write(path.parent / SKILL_MARKER, _SKILL_MARKER_TEXT, 0o644)
            if _read_text(path) != text:
                _atomic_write(Path(os.path.realpath(path)), text, 0o644)
                written.append(rel)
        except OSError as e:
            problems.append(f"cannot write {path}: {e}")
    return written, problems


def uninstall_assets(base: Path) -> tuple[list[str], list[str]]:
    """The managed files removed, and those that couldn't be; a file of the same name that isn't devkit's stays."""
    removed, problems = [], []
    for rel in assets():
        path, skill = base / rel, _is_skill(rel)
        if not _ours(path, skill):
            continue
        for p in (path, path.parent / SKILL_MARKER) if skill else (path,):
            try:
                p.unlink()
                removed.append(str(p.relative_to(base)))
            except FileNotFoundError:
                pass
            except OSError as e:
                problems.append(f"cannot remove {p}: {e}")
        if skill:
            try:
                path.parent.rmdir()  # only when empty: the user's own files in it stay
            except OSError:
                pass
    return [r for r in removed if not r.endswith(SKILL_MARKER)], problems


GIT_HOOK_MARKER = "# devkit-managed"
_USE_CHECK = "call `devkit check --staged` from your existing pre-commit setup instead"


def _git_out(cwd: str, *args: str) -> str | None:
    """git's stdout, or None when it exits non-zero (an unset config key, not a repo)."""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as e:
        raise InstallError(f"cannot run git: {e}") from None
    return proc.stdout.rstrip("\n") if proc.returncode == 0 else None


def git_hook_path(cwd: str) -> Path:
    """The repo's pre-commit hook; `--git-path` resolves a linked worktree to the shared hooks directory."""
    hooks = _git_out(cwd, "rev-parse", "--path-format=absolute", "--git-path", "hooks")
    if hooks is None:
        raise InstallError(f"{cwd} is not in a git repository")
    return Path(hooks) / "pre-commit"


def _hooks_path_setting(cwd: str) -> str | None:
    return _git_out(cwd, "config", "--get", "core.hooksPath") or None


def _read_hook(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise InstallError(f"cannot read {path}: {e}") from None


_HOOK_HEAD = f"#!/bin/sh\n{GIT_HOOK_MARKER}: remove with `devkit uninstall --git-hooks`\n"
_HOOK_RE = re.compile(re.escape(_HOOK_HEAD) + r"exec [^\n]+ check --staged\n")


def git_hook_script(exe: str) -> str:
    return f"{_HOOK_HEAD}exec {shlex.quote(exe)} check --staged\n"


def is_managed_git_hook(text: str | None) -> bool:
    """Exactly the script devkit writes, for any devkit path: an edited hook holds lines devkit must not drop."""
    return text is not None and _HOOK_RE.fullmatch(text) is not None


def _not_ours(text: str) -> str:
    if any(line.startswith(GIT_HOOK_MARKER) for line in text.splitlines()):
        return "was edited since devkit wrote it"
    return "was not written by devkit"


def install_git_hook(cwd: str, exe: str) -> str:
    path = git_hook_path(cwd)
    setting = _hooks_path_setting(cwd)
    if setting:
        raise InstallError(f"core.hooksPath is set ({setting}), so devkit won't write a hook; {_USE_CHECK}")
    text = _read_hook(path)
    if text is not None and not is_managed_git_hook(text):
        raise InstallError(f"{path} {_not_ours(text)}; left unchanged. To run devkit, {_USE_CHECK}")
    script = git_hook_script(exe)
    if text == script:
        return f"git: already installed in {path}"
    try:
        _atomic_write(path, script, 0o755)
    except OSError as e:
        raise InstallError(f"cannot write {path}: {e}") from None
    return f"git: installed in {path}"


def uninstall_git_hook(cwd: str) -> str:
    path = git_hook_path(cwd)
    text = _read_hook(path)
    if text is None:
        return f"git: not installed in {path}"
    if not is_managed_git_hook(text):
        return f"git: {path} {_not_ours(text)}; left unchanged, remove it yourself if you want it gone"
    try:
        path.unlink()
    except OSError as e:
        raise InstallError(f"cannot remove {path}: {e}") from None
    return f"git: removed {path}"


def git_hook_state(cwd: str) -> str:
    """One line for `devkit status`."""
    path = git_hook_path(cwd)
    setting = _hooks_path_setting(cwd)
    text = _read_hook(path)
    if is_managed_git_hook(text):
        return f"installed  ({path})"
    if setting:
        return f"not installed  (core.hooksPath is set: {setting})"
    if text is not None:
        return f"not installed  (the pre-commit hook {_not_ours(text)}: {path})"
    return f"not installed  ({path})"
