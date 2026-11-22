"""Guard rule: refuse the agent committing on, or pushing to, a protected branch such as `main`.
Early feedback only, like `secrets`: GitHub branch protection with `enforce_admins` is the real control."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..events import Event, Finding, Level
from ..repo import find_top
from . import Rule
from .secrets import shell_words

DEFAULT_BRANCHES = ("main", "master")

_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = frozenset({"command", "builtin", "exec", "time", "nohup", "env"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
# 2>&1, >/dev/null, 2> (target in the next word), &>log; group 1 holds an attached target
_REDIRECT = re.compile(r"^(?:\d*|&)[<>]{1,2}&?(\S*)$")
# git's global options that take the next word as their value
_GIT_VALUE_OPTS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                             "--config-env", "--super-prefix"})
_PUSH_VALUE_OPTS = frozenset({"-o", "--push-option", "--receive-pack", "--exec", "--repo"})
_ALL_BRANCHES = frozenset({"--all", "--branches", "--mirror"})


def strip_heredocs(command: str) -> str:
    """The command without heredoc bodies, which are data: a commit message can say `git push origin main`."""
    out, pending = [], []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending.extend(m.group(2) for m in _HEREDOC.finditer(line))
    return "\n".join(out)


def split_parts(command: str) -> list[str]:
    """Split on `&&`, `||`, `;`, `|`, `&` and newlines that are outside quotes."""
    parts, cur, quote, i = [], [], None, 0
    while i < len(command):
        c = command[i]
        if quote:
            cur.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(command):
                cur.append(command[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c == "\\" and i + 1 < len(command):
            cur.append(c + command[i + 1])
            i += 1
        elif c in "'\"":
            quote = c
            cur.append(c)
        elif c == "&" and (command[i - 1:i] in "<>" and i or command[i + 1:i + 2] == ">"):
            cur.append(c)  # a redirection such as 2>&1 or &>file, not a separator
        elif c in ";|&\n":
            parts.append("".join(cur))
            cur = []
            if command[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return [p for p in (p.strip() for p in parts) if p]


@dataclass(frozen=True)
class Git:
    dir: str | None  # None after `cd -`: the directory is unknown, so the call isn't judged
    sub: str
    args: tuple[str, ...]


def _resolve(base: str | None, path: str) -> str | None:
    return None if base is None else os.path.normpath(os.path.join(base, os.path.expanduser(path)))


def _words(part: str) -> list[str]:
    words, skip = [], False
    for w in (w.strip("(){}") for w in shell_words(part)):
        if skip or not w:
            skip = False
            continue
        if m := _REDIRECT.match(w):
            skip = not m.group(1)  # `> file`: the target is the next word
            continue
        words.append(w)
    while words and (_ASSIGNMENT.match(words[0]) or words[0] in _WRAPPERS):
        words = words[1:]
    return words


def git_calls(command: str, cwd: str | None, depth: int = 0) -> Iterator[Git]:
    """Each `git <sub> ...` in the command, in order, with the directory it runs in."""
    here = cwd
    for part in split_parts(strip_heredocs(command)):
        words = _words(part)
        if not words:
            continue
        if os.path.basename(words[0]) in _SHELLS and depth < 3:  # Codex runs every command as `bash -lc '...'`
            flag = next((i for i, w in enumerate(words[1:], 1) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
            if flag is not None and flag + 1 < len(words):
                yield from git_calls(words[flag + 1], here, depth + 1)
            continue
        if words[0] == "cd":
            target = words[1] if len(words) > 1 else "~"
            here = None if target == "-" else _resolve(here, target)
            continue
        if os.path.basename(words[0]) != "git":
            continue
        d, rest = here, words[1:]
        while rest and rest[0].startswith("-"):
            opt = rest.pop(0)
            if opt in _GIT_VALUE_OPTS and rest:
                value = rest.pop(0)
                if opt == "-C":
                    d = _resolve(d, value)
        if rest:
            yield Git(d, rest[0], tuple(rest[1:]))


def _gitdir(top: Path) -> Path | None:
    dot = top / ".git"
    if dot.is_dir():
        return dot
    try:
        text = dot.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return (top / text[len("gitdir:"):].strip()).resolve() if text.startswith("gitdir:") else None


def current_branch(top: Path) -> str | None:
    """The checked-out branch read from HEAD, without running git; None when detached or unreadable."""
    gd = _gitdir(top)
    try:
        head = (gd / "HEAD").read_text(encoding="utf-8", errors="replace").strip() if gd else ""
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def is_local_branch(top: Path, name: str) -> bool:
    gd = _gitdir(top)
    if gd is None:
        return False
    try:  # a linked worktree keeps its refs in the main repo's git dir
        gd = (gd / (gd / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        pass
    if (gd / "refs" / "heads" / name).is_file():
        return True
    try:
        packed = (gd / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return any(line.endswith(" refs/heads/" + name) for line in packed.splitlines())


def switched_to(top: Path, sub: str, args: Sequence[str]) -> tuple[bool, str | None]:
    """(changes the branch?, the new branch, or None when detached) for `git switch` and `git checkout`."""
    if sub not in ("switch", "checkout"):
        return False, None
    create = ("-c", "-C", "--create", "--force-create") if sub == "switch" else ("-b", "-B", "--orphan")
    for i, a in enumerate(args):
        if a == "--":
            return False, None  # `checkout -- file` restores files
        if a in create:
            return (True, args[i + 1]) if i + 1 < len(args) else (False, None)
        if a in ("-d", "--detach"):
            return True, None
    names = [a for a in args if not a.startswith("-")]
    if names and (sub == "switch" or is_local_branch(top, names[0])):
        return True, names[0]
    return False, None


def pushed_branches(args: Sequence[str], current: str | None) -> tuple[bool, list[str]]:
    """(updates every branch?, the branch names this push would update or delete)."""
    positional, delete, tags, i = [], False, False, 0
    while i < len(args):
        a = args[i]
        if a == "--":
            positional.extend(args[i + 1:])
            break
        if a in _ALL_BRANCHES:
            return True, []
        if a in ("-d", "--delete"):
            delete = True
        elif a == "--tags":
            tags = True
        elif a in _PUSH_VALUE_OPTS:
            i += 1
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    refspecs = positional[1:]
    if not refspecs:  # the current branch to its upstream, as push.default=simple does
        return False, [] if tags or delete or current is None else [current]
    names = []
    for spec in refspecs:
        src, sep, dst = spec.lstrip("+").partition(":")
        target = dst if sep and dst else src
        if "*" in target:
            return True, []
        if target == "HEAD":
            target = current
        elif target.startswith("refs/heads/"):
            target = target[len("refs/heads/"):]
        elif target.startswith("refs/"):
            target = None  # a tag or another namespace
        if target:
            names.append(target)
    return False, names


def _message(verb: str, branch: str) -> str:
    return (f"this command would {verb} the protected branch '{branch}'. Create a branch (git switch -c "
            f"<name>, following the repo's branch naming), commit and push there, then open a pull request. "
            f"If this must go to '{branch}' directly, ask the user to do it from their own terminal.")


class ProtectedBranches(Rule):
    name = "protected_branches"
    guard = True
    default_level = Level.BLOCK
    phases = frozenset({"pre"})
    kinds = frozenset({"shell"})
    settings = {"branches": list(DEFAULT_BRANCHES), "exempt_repos": []}
    extend = frozenset({"branches"})
    repo_settings = frozenset({"branches"})

    def validate(self, key: str, value: Any) -> str | None:
        for v in value:
            if not isinstance(v, str) or not v.strip():
                return "each entry must be a non-empty string"
            if key == "branches" and (re.search(r"[\s:]", v) or ".." in v):
                return f"invalid branch name {v!r}"
        return None

    def check(self, event: Event, settings: Mapping[str, Any]) -> list[Finding]:
        command = event.command or ""
        if not re.search(r"\b(?:commit|push)\b", command):
            return []
        protected = set(settings["branches"])
        exempt = {os.path.realpath(os.path.expanduser(p)) for p in settings["exempt_repos"]}
        assumed: dict[Path, str | None] = {}  # branch switches made earlier in this command
        for call in git_calls(command, event.cwd or os.getcwd()):
            top = find_top(call.dir) if call.dir else None
            if top is None or os.path.realpath(top) in exempt:
                continue
            branch = assumed[top] if top in assumed else current_branch(top)
            changed, new = switched_to(top, call.sub, call.args)
            if changed:
                assumed[top] = new
            elif call.sub == "commit" and branch in protected:
                return [Finding(self.name, _message("commit on", branch))]
            elif call.sub == "push":
                every, names = pushed_branches(call.args, branch)
                hit = min(protected) if every else next((n for n in names if n in protected), None)
                if hit:
                    return [Finding(self.name, _message("push to", hit))]
        return []


RULE = ProtectedBranches()
