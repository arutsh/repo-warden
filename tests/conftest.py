import json
import os
import subprocess
from pathlib import Path

import pytest

from repo_warden.audit import Auditor
from repo_warden.broker import Broker
from repo_warden.capabilities import Registry
from repo_warden.execution import DirectBackend
from repo_warden.paths import SensitiveMatcher
from repo_warden.policy import PolicyGate, load_policy
from repo_warden.repo import open_repo


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q")
    return Path(os.path.realpath(d))


@pytest.fixture
def nested(repo_dir: Path) -> dict[str, str]:
    """Nested boundaries inside the repo, each holding a `src/app.py` that contains `NEEDLE`.

    Returns {kind: repo-relative directory}: a linked worktree, a submodule-style `.git` file and a
    nested repository, plus `lib -> vendor/lib`, a symlink into the nested repository.
    """
    git(repo_dir, "commit", "-q", "--allow-empty", "-m", "init")
    layout = {"worktree": ".claude/worktrees/feature", "submodule": "mods/sub", "nested": "vendor/lib"}
    git(repo_dir, "worktree", "add", "-q", layout["worktree"])
    (repo_dir / layout["submodule"]).mkdir(parents=True)
    (repo_dir / layout["submodule"] / ".git").write_text("gitdir: ../../.git/modules/sub\n")
    (repo_dir / layout["nested"]).mkdir(parents=True)
    git(repo_dir / layout["nested"], "init", "-q")
    for d in layout.values():
        (repo_dir / d / "src").mkdir(exist_ok=True)
        (repo_dir / d / "src" / "app.py").write_text("NEEDLE = 1\n")
    (repo_dir / "lib").symlink_to("vendor/lib")
    return layout


@pytest.fixture
def locked(repo_dir: Path):
    """`locked/`, a directory nobody but root can enter, holding `secret.txt` that contains `NEEDLE`."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    d = repo_dir / "locked"
    d.mkdir()
    (d / "secret.txt").write_text("NEEDLE = 1\n")
    d.chmod(0)
    yield "locked"
    d.chmod(0o755)


@pytest.fixture
async def repo(repo_dir: Path):
    ctx = await open_repo(repo_dir, DirectBackend(repo_dir))
    yield ctx
    ctx.close()


def records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def make_broker(repo, tmp_path: Path):
    """make_broker(policy_yaml, caps) -> (broker, audit_path)"""
    made = []

    def make(policy_yaml: str, caps):
        pf = tmp_path / "policy.yaml"
        pf.write_text(policy_yaml)
        lp = load_policy(pf, repo.root)
        audit_path = tmp_path / "audit.jsonl"
        auditor = Auditor.open(audit_path, repo, "direct")
        made.append(auditor)
        broker = Broker(registry=Registry(caps), gate=PolicyGate(lp.policy), auditor=auditor, repo=repo,
                        backend=DirectBackend(repo.root), sensitive=SensitiveMatcher(lp.config.sensitive_paths_extra))
        return broker, audit_path

    yield make
    for a in made:
        a.close()
