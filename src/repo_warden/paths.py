"""Turn agent-supplied relative paths into safe in-repo targets, and open them race-safely."""

from __future__ import annotations

import errno
import fnmatch
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass

from .repo import RepoContext

DEFAULT_SENSITIVE: tuple[str, ...] = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_*")


class PathDenied(Exception):
    def __init__(self, rule_id: str, message: str):
        super().__init__(message)
        self.rule_id = rule_id


@dataclass(frozen=True)
class SafePath:
    rel: str  # symlink-free path relative to the repo root; "" is the root itself

    @property
    def parts(self) -> list[str]:
        return self.rel.split("/") if self.rel else []


def normalize_pattern(pattern: str) -> list[str]:
    """Gitignore-style spellings -> matcher patterns, erring towards hiding more.

    `./x` and `x/` drop the decoration, `/x` stays anchored to the root, and `**/x` also matches `x` at the root.
    """
    p = pattern.strip().lower()
    while p.startswith("./"):
        p = p[2:]
    anchored = p.startswith("/")
    p = p.strip("/")
    if not p:
        raise ValueError(f"empty sensitive pattern {pattern!r}")
    if anchored and "/" not in p:
        return [f"{p}/**"]
    out = [p]
    while out[-1].startswith("**/"):
        out.append(out[-1][3:])
    return out


class SensitiveMatcher:
    """Case-insensitive patterns. Without a `/` a pattern matches any component's basename;
    with one it matches the relative path, and `dir/**` also matches `dir` itself."""

    def __init__(self, extra: Iterable[str] = ()):
        pats = [n for p in (*DEFAULT_SENSITIVE, *extra) for n in normalize_pattern(p)]
        self.patterns = tuple(pats)
        self._names = [p for p in pats if "/" not in p]
        self._paths = [p for p in pats if "/" in p]

    def name_matches(self, name: str) -> bool:
        name = name.lower()
        return any(fnmatch.fnmatchcase(name, p) for p in self._names)

    def matches(self, rel: str) -> bool:
        parts = [p for p in rel.lower().split("/") if p]
        if any(self.name_matches(p) for p in parts):
            return True
        for i in range(1, len(parts) + 1):
            prefix = "/".join(parts[:i])
            for p in self._paths:
                if fnmatch.fnmatchcase(prefix, p) or (p.endswith("/**") and prefix == p[:-3]):
                    return True
        return False


def is_git_internal(rel: str) -> bool:
    return any(part.lower() == ".git" for part in rel.split("/"))


def _check_rel(rel: str, matcher: SensitiveMatcher) -> None:
    if is_git_internal(rel):
        raise PathDenied("path.git", "git internals are not accessible")
    if matcher.matches(rel):
        raise PathDenied("path.sensitive", "path matches a sensitive pattern")


def resolve(ctx: RepoContext, rel: str, matcher: SensitiveMatcher) -> SafePath:
    if not isinstance(rel, str) or "\x00" in rel:
        raise PathDenied("path.nul", "path contains a NUL byte")
    if rel.startswith("~"):
        raise PathDenied("path.home", "home-relative paths are not allowed")
    if os.path.isabs(rel):
        raise PathDenied("path.absolute", "absolute paths are not allowed")
    norm = os.path.normpath(rel) if rel else "."
    if norm == ".." or norm.startswith("../"):
        raise PathDenied("path.escape", "path leaves the repository")
    norm = "" if norm == "." else norm
    _check_rel(norm, matcher)

    real = os.path.realpath(os.path.join(ctx.root, norm))
    if os.path.commonpath([ctx.root, real]) != ctx.root:
        raise PathDenied("path.escape", "path resolves outside the repository")
    rel_real = os.path.relpath(real, ctx.root)
    rel_real = "" if rel_real == "." else rel_real
    _check_rel(rel_real, matcher)
    safe = SafePath(rel_real)
    if nested_boundary(ctx, safe.parts) is not None:
        raise PathDenied("path.nested_repo", "path is inside a nested repository or worktree")
    return safe


def has_git_entry(dir_fd: int, rel_dir: str) -> bool:
    """True if `rel_dir` (relative to `dir_fd`) holds a `.git` file, directory or link: a nested repo or worktree."""
    try:
        os.stat(f"{rel_dir}/.git", dir_fd=dir_fd, follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except PermissionError:
        return False  # `rel_dir` can't be traversed, so nothing under it is reachable either
    return True


def nested_boundary(ctx: RepoContext, parts: list[str]) -> str | None:
    """The first directory along `parts` (the target included, the root excluded) that is a nested repo."""
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if has_git_entry(ctx.root_fd, prefix):
            return prefix
    return None


DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _is_symlink(name: str, dir_fd: int) -> bool:
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False


def open_nofollow(ctx: RepoContext, safe: SafePath, *, directory: bool = False) -> int:
    """Open `safe` by walking from the root fd without following any symlink.

    `safe.rel` is symlink-free, so a symlink met here means the tree changed after `resolve`.
    Returns an fd on a regular file (or a directory when `directory=True`).
    """
    parts = safe.parts
    fd = os.dup(ctx.root_fd)
    try:
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            flags = DIR_FLAGS if (not last or directory) else FILE_FLAGS
            try:
                nfd = os.open(name, flags, dir_fd=fd)
            except OSError as e:
                if e.errno == errno.ENOTDIR and not _is_symlink(name, fd):
                    # O_DIRECTORY|O_NOFOLLOW reports a symlink as ENOTDIR too; anything else is just bad input.
                    raise FileNotFoundError(errno.ENOENT, "not a directory", name) from None
                if e.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise PathDenied("path.race", "path changed while it was being opened") from None
                raise
            os.close(fd)
            fd = nfd
        st = os.fstat(fd)
        if directory and not stat.S_ISDIR(st.st_mode):
            raise PathDenied("path.not_directory", "path is not a directory")
        if not directory and not stat.S_ISREG(st.st_mode):
            raise PathDenied("path.not_regular", "path is not a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise
