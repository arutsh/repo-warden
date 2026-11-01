"""Working-tree snapshots as git tree ids, written through a copy of the index; secret paths left out.
Never changes a ref, the user's index or the working tree."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterable
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

    def git(*args: str, stdin: bytes = b"") -> bytes:
        return gitsafe.run(top, *args, env=env, stdin=stdin, timeout_s=deadline - time.monotonic())

    index_path, algo = os.fsdecode(git("rev-parse", "--git-path", "index", "--show-object-format")).splitlines()
    index = Path(top, index_path)
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="index-", dir=d))
    try:
        if index.is_file():  # a fresh repo has no index yet; the copy keeps the stat cache warm
            shutil.copyfile(index, tmp / "index")
        env["GIT_INDEX_FILE"] = str(tmp / "index")
        if patterns:  # a tracked secret stays in the copied index unless removed; `add` alone would keep it
            git("rm", "--cached", "-r", "-f", "-q", "--ignore-unmatch", "--",
                *secret_pathspecs(patterns, exclude=False))
        git("add", "-A", "--", ".", *secret_pathspecs(patterns))
        return _write_trees(git, algo, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_trees(git: Callable[..., bytes], algo: str, tmp: Path) -> str:
    """What `git write-tree` does, which cplt's git guard refuses: ids computed here, objects
    written in one `hash-object` call, and git's ids checked against ours."""
    root: dict = {}
    for rec in git("ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, oid, stage = meta.split(b" ")
        if stage != b"0":
            raise gitsafe.GitError(f"unmerged entry in the snapshot index: {os.fsdecode(path)}")
        *dirs, name = path.split(b"/")
        node = root
        for d in dirs:
            node = node.setdefault(d, {})
        node[name] = (mode, bytes.fromhex(oid.decode()))

    trees: list[tuple[str, bytes]] = []

    def build(node: dict) -> bytes:
        entries = []
        for name, v in node.items():
            if isinstance(v, dict):
                entries.append((name + b"/", b"40000 " + name + b"\0" + build(v)))
            else:
                entries.append((name, v[0] + b" " + name + b"\0" + v[1]))
        body = b"".join(e for _, e in sorted(entries))  # git orders a directory as if named "name/"
        oid = hashlib.new(algo, b"tree %d\0" % len(body) + body).digest()
        trees.append((oid.hex(), body))
        return oid

    root_id = build(root).hex()
    paths = []
    for i, (_, body) in enumerate(trees):
        (tmp / f"t{i}").write_bytes(body)
        paths.append(str(tmp / f"t{i}"))
    written = git("hash-object", "-w", "-t", "tree", "--no-filters", "--stdin-paths",
                  stdin=("\n".join(paths) + "\n").encode()).decode().split()
    if written != [oid for oid, _ in trees]:
        raise gitsafe.GitError("git and devkit disagree on a snapshot tree id")
    return root_id


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
