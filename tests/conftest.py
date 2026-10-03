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
async def repo(repo_dir: Path):
    ctx = await open_repo(repo_dir, DirectBackend())
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
                        backend=DirectBackend(), sensitive=SensitiveMatcher(lp.config.sensitive_paths_extra))
        return broker, audit_path

    yield make
    for a in made:
        a.close()
