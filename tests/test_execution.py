import os
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from repo_warden.execution import (
    CPLT_MARKER,
    ContainmentError,
    CpltBackend,
    DirectBackend,
    require_containment,
    verify_containment,
)
from repo_warden.gitcmd import git_argv, git_env

async def test_git_version_through_backend(tmp_path: Path):
    res = await DirectBackend(tmp_path).run(git_argv("--version"), cwd=tmp_path, timeout_s=10,
                                            max_output=4096, env=git_env())
    assert res.returncode == 0
    assert res.stdout.startswith(b"git version")
    assert not res.timed_out and not res.truncated


async def test_output_is_capped(tmp_path: Path):
    res = await DirectBackend(tmp_path).run(["python3", "-c", "print('x' * 100000)"], cwd=tmp_path,
                                            timeout_s=10, max_output=1000, env={})
    assert len(res.stdout) == 1000
    assert res.truncated


async def test_timeout(tmp_path: Path):
    res = await DirectBackend(tmp_path).run(["sleep", "5"], cwd=tmp_path, timeout_s=0.2,
                                            max_output=1000, env={})
    assert res.timed_out and res.returncode is None


def _alive(pid: int) -> bool:
    """True while `pid` exists and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="uses /proc")
async def test_timeout_kills_child_and_grandchild(tmp_path: Path):
    pids = tmp_path / "pids"
    script = f"sleep 60 & echo $! > {pids}; echo $$ >> {pids}; sleep 60"
    res = await DirectBackend(tmp_path).run(["sh", "-c", script], cwd=tmp_path, timeout_s=0.5,
                                            max_output=1000, env={})
    assert res.timed_out
    grandchild, child = (int(x) for x in pids.read_text().split())
    deadline = time.monotonic() + 5
    while (_alive(child) or _alive(grandchild)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(child) and not _alive(grandchild)


async def test_huge_output_is_capped_without_buffering_it(tmp_path: Path):
    fifty_mb = "import sys\nfor _ in range(800): sys.stdout.buffer.write(b'x' * 65536)"
    tracemalloc.start()
    try:
        res = await DirectBackend(tmp_path).run(["python3", "-c", fifty_mb], cwd=tmp_path,
                                                timeout_s=30, max_output=64 * 1024, env={})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res.returncode == 0 and not res.timed_out
    assert len(res.stdout) == 64 * 1024 and res.truncated
    assert peak < 8 * 1024 * 1024


async def test_env_is_allowlisted(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/ssh-agent.sock")
    res = await DirectBackend(tmp_path).run(["env"], cwd=tmp_path, timeout_s=10, max_output=65536,
                                            env={"EXTRA": "1"})
    env = dict(line.split("=", 1) for line in res.stdout.decode().splitlines())
    assert not {"AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK"} & env.keys()
    assert env["EXTRA"] == "1"
    assert env["HOME"] != os.path.expanduser("~")


async def test_home_is_empty_temp_dir(tmp_path: Path):
    res = await DirectBackend(tmp_path).run(["sh", "-c", 'ls -A "$HOME"; echo "$HOME"'], cwd=tmp_path,
                                            timeout_s=10, max_output=4096, env={})
    lines = res.stdout.decode().splitlines()
    assert len(lines) == 1  # only the echoed path: the directory is empty
    assert not os.path.exists(lines[0])  # and removed after the call


async def test_stdin_is_devnull(tmp_path: Path):
    res = await DirectBackend(tmp_path).run(["cat"], cwd=tmp_path, timeout_s=5, max_output=100, env={})
    assert res.returncode == 0 and res.stdout == b"" and not res.timed_out


async def test_shell_metacharacters_are_literal(tmp_path: Path):
    arg = "; rm -rf ~ $(touch pwned) `touch pwned2` | true && touch pwned3 > out"
    res = await DirectBackend(tmp_path).run(["printf", "%s", arg], cwd=tmp_path, timeout_s=5,
                                            max_output=4096, env={})
    assert res.stdout.decode() == arg
    assert sorted(os.listdir(tmp_path)) == []


async def test_cwd_must_be_inside_root(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "sub").mkdir(parents=True)
    (root / "out").symlink_to(tmp_path)
    backend = DirectBackend(root)
    res = await backend.run(["true"], cwd=root / "sub", timeout_s=5, max_output=10, env={})
    assert res.returncode == 0
    for bad in (tmp_path, root / "out", root / ".."):
        with pytest.raises(ValueError, match="outside the repository"):
            await backend.run(["true"], cwd=bad, timeout_s=5, max_output=10, env={})


# --- containment probes ------------------------------------------------------------------------

@pytest.fixture
def contained(tmp_path: Path):
    """A repo whose parent is read-only and a home whose ~/.ssh is unreadable: every probe can pass."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    parent = tmp_path / "parent"
    repo = parent / "repo"
    repo.mkdir(parents=True)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh").chmod(0o000)
    parent.chmod(0o555)
    yield {"root": repo, "environ": {CPLT_MARKER: "1"}, "seccomp_mode": 2,
           "platform": "linux", "home": str(home)}
    parent.chmod(0o755)
    (home / ".ssh").chmod(0o755)


def _failed(probes) -> set[str]:
    return {p.name for p in probes if not p.ok}


def test_all_probes_pass(contained):
    probes = verify_containment(**contained)
    assert _failed(probes) == set()
    require_containment(probes)


def test_marker_only_fails(tmp_path: Path):
    # Marker set, but nothing else is contained: no seccomp, writable parent, readable ~/.ssh.
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (tmp_path / "repo").mkdir()
    probes = verify_containment(tmp_path / "repo", environ={CPLT_MARKER: "1"}, platform="linux",
                                seccomp_mode=0, home=str(home))
    assert _failed(probes) == {"seccomp", "parent_write", "ssh"}
    assert os.listdir(tmp_path) == ["home", "repo"]  # the write probe cleaned up after itself
    with pytest.raises(ContainmentError, match="seccomp.*parent_write.*ssh"):
        require_containment(probes)


@pytest.mark.parametrize("probe, override", [
    ("marker", {"environ": {}}),
    ("seccomp", {"seccomp_mode": 0}),
])
def test_single_failing_probe_is_named(contained, probe, override):
    probes = verify_containment(**{**contained, **override})
    assert _failed(probes) == {probe}
    with pytest.raises(ContainmentError, match=f"failed probe: {probe}:"):
        require_containment(probes)


def test_parent_write_failure_is_named(contained):
    contained["root"].parent.chmod(0o755)
    probes = verify_containment(**contained)
    assert _failed(probes) == {"parent_write"}
    assert sorted(os.listdir(contained["root"].parent)) == ["repo"]


def test_readable_ssh_is_named(contained):
    (Path(contained["home"]) / ".ssh").chmod(0o755)
    probes = verify_containment(**contained)
    assert _failed(probes) == {"ssh"}


def test_seccomp_skipped_off_linux(contained):
    probes = verify_containment(**{**contained, "seccomp_mode": 0, "platform": "darwin"})
    assert _failed(probes) == set()


def test_missing_ssh_passes(contained, tmp_path: Path):
    probes = verify_containment(**{**contained, "home": str(tmp_path / "nohome")})
    assert _failed(probes) == set()


def test_refusal_points_to_direct_and_docs(contained):
    with pytest.raises(ContainmentError) as e:
        require_containment(verify_containment(**{**contained, "environ": {}}))
    assert "--backend direct" in str(e.value) and "README" in str(e.value)


@pytest.mark.skipif(CPLT_MARKER in os.environ, reason="checks behaviour outside cplt")
def test_cplt_backend_refuses_outside_cplt(tmp_path: Path):
    with pytest.raises(ContainmentError, match="marker"):
        CpltBackend(tmp_path)
