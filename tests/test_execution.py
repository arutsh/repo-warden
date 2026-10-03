from pathlib import Path

from repo_warden.execution import DirectBackend
from repo_warden.gitcmd import git_argv, git_env


async def test_git_version_through_backend(tmp_path: Path):
    res = await DirectBackend().run(git_argv("--version"), cwd=tmp_path, timeout_s=10,
                                    max_output=4096, env=git_env())
    assert res.returncode == 0
    assert res.stdout.startswith(b"git version")
    assert not res.timed_out and not res.truncated


async def test_output_is_capped(tmp_path: Path):
    res = await DirectBackend().run(["python3", "-c", "print('x' * 100000)"], cwd=tmp_path,
                                    timeout_s=10, max_output=1000, env={})
    assert len(res.stdout) == 1000
    assert res.truncated


async def test_timeout(tmp_path: Path):
    res = await DirectBackend().run(["sleep", "5"], cwd=tmp_path, timeout_s=0.2,
                                    max_output=1000, env={})
    assert res.timed_out and res.returncode is None


async def test_env_is_allowlisted(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    res = await DirectBackend().run(["env"], cwd=tmp_path, timeout_s=10, max_output=65536,
                                    env={"EXTRA": "1"})
    names = {line.split("=", 1)[0] for line in res.stdout.decode().splitlines()}
    assert "GITHUB_TOKEN" not in names
    assert "EXTRA" in names
