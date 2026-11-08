"""Working-tree snapshots as git tree ids, written through a copy of the index; secret paths left out.
Never changes a ref, the user's index or the working tree."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from .. import gitsafe

TREE_ID = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")  # sha1 or sha256, used with fullmatch
# Submodule bumps count as changes; `short` keeps git from reading the submodule's repo for a log.
_DIFF_SUBMODULES = ("--ignore-submodules=none", "--submodule=short")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "")
    if not os.path.isabs(base):  # the XDG spec says to ignore a relative value
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "devkit" / "review"


def secret_pathspecs(patterns: Iterable[str], *, exclude: bool = True) -> list[str]:
    """Pathspecs for each secret pattern at any depth, matching the file and anything under a matching dir."""
    magic = "exclude,icase,glob" if exclude else "icase,glob"
    return [spec for p in dict.fromkeys(patterns) for spec in (f":({magic})**/{p}", f":({magic})**/{p}/**")]


def snapshot(top: str | os.PathLike, secret_patterns: Iterable[str], timeout_s: float | None = None) -> str:
    """The tree id of the tracked and untracked, non-ignored files under `top`, minus secret paths."""
    patterns = list(secret_patterns)
    deadline = time.monotonic() + (gitsafe.TIMEOUT_S if timeout_s is None else timeout_s)  # one budget, under 30 s
    env: dict[str, str] = {}

    def git(*args: str) -> bytes:
        return gitsafe.run(top, *args, env=env, timeout_s=deadline - time.monotonic())

    index = Path(top, os.fsdecode(git("rev-parse", "--git-path", "index")).rstrip("\n"))
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="index-", dir=d))
    try:
        if index.is_file():  # a fresh repo has no index yet; the copy keeps the stat cache warm
            # copy2 keeps the index's mtime: git re-reads content only for files not older than the index,
            # so a fresh mtime would hide same-size edits made right after it was written.
            shutil.copy2(index, tmp / "index")
        env["GIT_INDEX_FILE"] = str(tmp / "index")
        if patterns:  # a tracked secret stays in the copied index unless removed; `add` alone would keep it
            git("rm", "--cached", "-r", "-f", "-q", "--ignore-unmatch", "--",
                *secret_pathspecs(patterns, exclude=False))
        git("add", "-A", "--", ".", *secret_pathspecs(patterns))
        return git("write-tree").decode().strip()  # cplt's git guard allows it from 2026.11.06
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def changed_paths(top: str | os.PathLike, a: str, b: str, secret_patterns: Iterable[str],
                  timeout_s: float | None = None) -> list[str]:
    """Paths that differ between trees `a` and `b`, both sides of a rename included."""
    out = gitsafe.run(top, "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv",
                      *_DIFF_SUBMODULES, _tree(a), _tree(b), "--", ".", *secret_pathspecs(secret_patterns),
                      timeout_s=gitsafe.TIMEOUT_S if timeout_s is None else timeout_s)
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def diff(top: str | os.PathLike, a: str, b: str, secret_patterns: Iterable[str]) -> str:
    """`--stat` followed by the full patch from tree `a` to tree `b`."""
    out = gitsafe.run(top, "diff", "--patch-with-stat", "--no-color", "--no-ext-diff", "--no-textconv",
                      *_DIFF_SUBMODULES, _tree(a), _tree(b), "--", ".", *secret_pathspecs(secret_patterns))
    return out.decode("utf-8", errors="replace")


def missing(top: str | os.PathLike, tree_ids: Iterable[str], timeout_s: float | None = None) -> set[str]:
    """The ids among `tree_ids` that are not trees in the repo, such as ones `git gc` pruned."""
    ids = [_tree(t) for t in dict.fromkeys(tree_ids)]
    if not ids:
        return set()
    out = gitsafe.run(top, "cat-file", "--batch-check=%(objecttype)", stdin=("\n".join(ids) + "\n").encode(),
                      timeout_s=gitsafe.TIMEOUT_S if timeout_s is None else timeout_s)
    found = out.decode().split("\n")
    return {t for t, kind in zip(ids, found) if not kind.endswith("tree")} | set(ids[len(found):])


def _tree(tree_id: str) -> str:
    # Ids come from the agent-writable state file: never let one pass as an option.
    if not TREE_ID.fullmatch(tree_id):
        raise gitsafe.GitError(f"not a tree id: {tree_id!r}")
    return tree_id
