"""Find the git top level without running git, so hooks stay fast."""

from __future__ import annotations

import os
from pathlib import Path

REPO_CONFIG = ".devkit.yaml"


def find_top(start: str | os.PathLike | None) -> Path | None:
    """The nearest directory at or above `start` holding `.git` (a dir, a worktree file or a link)."""
    if not start:
        return None
    p = Path(os.path.abspath(start))
    while not p.is_dir():  # a file, or a path that doesn't exist yet
        if p.parent == p:
            return None
        p = p.parent
    for d in (p, *p.parents):
        if os.path.lexists(d / ".git"):
            return d
    return None
