"""Runs only inside a real cplt sandbox: `cplt exec -- uv run pytest tests/test_cplt_integration.py`."""

import os
from pathlib import Path

import pytest

from repo_warden.execution import CPLT_MARKER, CpltBackend, verify_containment
from repo_warden.gitcmd import git_argv, git_env
from repo_warden.repo import open_repo

pytestmark = pytest.mark.skipif(CPLT_MARKER not in os.environ, reason="needs a real cplt sandbox")

# This checkout (a main checkout or a linked worktree), which is cplt's project directory.
CHECKOUT = Path(__file__).resolve().parents[1]


def test_every_probe_passes():
    failed = [f"{p.name}: {p.detail}" for p in verify_containment(CHECKOUT) if not p.ok]
    assert failed == []


async def test_cplt_backend_runs_git_in_the_checkout():
    backend = CpltBackend(CHECKOUT)
    repo = await open_repo(CHECKOUT, backend)
    try:
        res = await backend.run(git_argv("rev-parse", "--show-toplevel"), cwd=CHECKOUT, timeout_s=10,
                                max_output=4096, env=git_env())
        assert res.returncode == 0 and res.stdout.decode().strip() == repo.root
    finally:
        repo.close()
