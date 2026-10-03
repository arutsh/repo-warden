import os
from pathlib import Path

import pytest

from repo_warden.execution import DirectBackend
from repo_warden.repo import RepoError, open_repo


async def test_valid_top_level(repo_dir: Path):
    ctx = await open_repo(repo_dir, DirectBackend())
    try:
        assert ctx.root == str(repo_dir)
        assert os.path.samestat(os.fstat(ctx.root_fd), os.stat(repo_dir))
        assert len(ctx.session_id) == 32
        assert ctx.principal
    finally:
        ctx.close()


async def test_session_ids_differ(repo_dir: Path):
    a = await open_repo(repo_dir, DirectBackend())
    b = await open_repo(repo_dir, DirectBackend())
    try:
        assert a.session_id != b.session_id
    finally:
        a.close()
        b.close()


async def test_subdirectory_refused(repo_dir: Path):
    sub = repo_dir / "src"
    sub.mkdir()
    with pytest.raises(RepoError, match=f"--repo {repo_dir}"):
        await open_repo(sub, DirectBackend())


async def test_non_repo_refused(tmp_path: Path):
    d = tmp_path / "plain"
    d.mkdir()
    with pytest.raises(RepoError, match="not inside a git working tree"):
        await open_repo(d, DirectBackend())


async def test_missing_path_refused(tmp_path: Path):
    with pytest.raises(RepoError, match="does not exist"):
        await open_repo(tmp_path / "nope", DirectBackend())


async def test_symlinked_repo_binds_real_path(repo_dir: Path, tmp_path: Path):
    link = tmp_path / "link"
    link.symlink_to(repo_dir)
    ctx = await open_repo(link, DirectBackend())
    try:
        assert ctx.root == str(repo_dir)
    finally:
        ctx.close()
