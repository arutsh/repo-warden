import os
from pathlib import Path

import pytest

from repo_warden.execution import DirectBackend
from repo_warden.paths import PathDenied, SafePath, SensitiveMatcher, resolve
from repo_warden.repo import RepoError, open_repo

from .conftest import git


async def test_valid_top_level(repo_dir: Path):
    ctx = await open_repo(repo_dir, DirectBackend(repo_dir))
    try:
        assert ctx.root == str(repo_dir)
        assert os.path.samestat(os.fstat(ctx.root_fd), os.stat(repo_dir))
        assert len(ctx.session_id) == 32
        assert ctx.principal
    finally:
        ctx.close()


async def test_session_ids_differ(repo_dir: Path):
    a = await open_repo(repo_dir, DirectBackend(repo_dir))
    b = await open_repo(repo_dir, DirectBackend(repo_dir))
    try:
        assert a.session_id != b.session_id
    finally:
        a.close()
        b.close()


async def test_subdirectory_refused(repo_dir: Path):
    sub = repo_dir / "src"
    sub.mkdir()
    with pytest.raises(RepoError, match=f"--repo {repo_dir}"):
        await open_repo(sub, DirectBackend(repo_dir))


async def test_non_repo_refused(tmp_path: Path):
    d = tmp_path / "plain"
    d.mkdir()
    with pytest.raises(RepoError, match="not inside a git working tree"):
        await open_repo(d, DirectBackend(d))


async def test_missing_path_refused(tmp_path: Path):
    with pytest.raises(RepoError, match="does not exist"):
        await open_repo(tmp_path / "nope", DirectBackend(tmp_path))


async def test_symlinked_repo_binds_real_path(repo_dir: Path, tmp_path: Path):
    link = tmp_path / "link"
    link.symlink_to(repo_dir)
    ctx = await open_repo(link, DirectBackend(repo_dir))
    try:
        assert ctx.root == str(repo_dir)
    finally:
        ctx.close()


async def test_linked_worktree_as_repo(repo_dir: Path, tmp_path: Path):
    git(repo_dir, "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt"
    git(repo_dir, "worktree", "add", "-q", str(wt))
    (wt / "a.txt").write_text("hi")
    ctx = await open_repo(wt, DirectBackend(wt))
    try:
        assert ctx.root == os.path.realpath(wt)
        assert (wt / ".git").is_file()
        with pytest.raises(PathDenied) as e:
            resolve(ctx, ".git", SensitiveMatcher())
        assert e.value.rule_id == "path.git"
        assert resolve(ctx, "a.txt", SensitiveMatcher()) == SafePath("a.txt")
    finally:
        ctx.close()
