"""Per-agent hook adapters: each turns its agent's hook input into events and the outcome back into its output."""

from __future__ import annotations

import os
import stat

MAX_FILE_BYTES = 2 * 1024 * 1024

AGENTS = {"claude": "devkit.adapters.claude", "codex": "devkit.adapters.codex"}


def resolve(path: str | None, cwd: str | None) -> str | None:
    """`path` made absolute against the hook's cwd, where it is relative."""
    if not path:
        return None
    return path if os.path.isabs(path) or not cwd else os.path.join(cwd, path)


def read_text(path: str) -> str:
    """A file's current text for before/after comparisons; empty if missing, unreadable or too large."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)  # a FIFO must not hang the hook
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_FILE_BYTES + 1)
    except OSError:
        return ""
    finally:
        os.close(fd)
    return "" if len(data) > MAX_FILE_BYTES else data.decode("utf-8", errors="replace")
