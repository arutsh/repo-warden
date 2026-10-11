"""The repository and session a broker process is bound to for its whole lifetime."""

from __future__ import annotations

import os
import pwd
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .execution import ExecutionBackend
from .gitcmd import git_argv, git_env


class RepoError(Exception):
    """The `--repo` path is not usable; the broker must not start."""


@dataclass(frozen=True)
class RepoContext:
    root: str                       # realpath of the git top level
    root_fd: int                    # O_DIRECTORY fd on root, the anchor for every file open
    session_id: str
    principal: str                  # OS user running the broker
    _closed: list[bool] = field(default_factory=lambda: [False], repr=False, compare=False)

    def close(self) -> None:
        if not self._closed[0]:
            os.close(self.root_fd)
            self._closed[0] = True


def os_principal() -> str:
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return f"uid:{os.geteuid()}"


async def open_repo(path: str | os.PathLike[str], backend: ExecutionBackend) -> RepoContext:
    """Resolve `path`, require it to be a git top level, and bind a new session to it."""
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        raise RepoError(f"repository path does not exist or is not a directory: {real}")
    try:
        res = await backend.run(git_argv("rev-parse", "--show-toplevel"), cwd=Path(real),
                                timeout_s=10, max_output=65536, env=git_env())
    except ValueError as e:
        raise RepoError(str(e)) from e
    if res.timed_out or res.returncode != 0:
        raise RepoError(f"not inside a git working tree: {real}")
    top = os.path.realpath(res.stdout.decode(errors="replace").strip())
    if top != real:
        raise RepoError(f"{real} is not the top level of its git working tree; use --repo {top}")
    root_fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    return RepoContext(root=real, root_fd=root_fd, session_id=secrets.token_hex(16),
                       principal=os_principal())
