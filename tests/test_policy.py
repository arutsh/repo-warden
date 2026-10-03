from pathlib import Path

import pytest
from mcp_airlock.policy import Decision, Policy

from repo_warden.policy import (PolicyDecision, PolicyError, PolicyGate, Verdict, load_policy, map_decision,
                                strictest)

POLICY = """
repo_warden:
  sensitive_paths_extra: ["*.tfstate"]
environment: production
tools:
  filesystem.read: { tiers: { local: L0 } }
  git.status: { tiers: { local: L3 } }
  git.push: { tiers: { local: L2 } }
  git.log: { tiers: { staging: L0 } }
"""


def write(p: Path, text: str) -> Path:
    p.write_text(text)
    return p


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    return write(tmp_path / "policy.yaml", POLICY)


def test_load_pops_block_and_forces_local(policy_file, repo_dir):
    lp = load_policy(policy_file, str(repo_dir))
    assert lp.config.sensitive_paths_extra == ["*.tfstate"]
    assert lp.policy.environment == "local"
    assert set(lp.policy.tools) == {"filesystem.read", "git.status", "git.push", "git.log"}


def test_unknown_repo_warden_key_rejected(tmp_path, repo_dir):
    p = write(tmp_path / "p.yaml", "repo_warden: { nope: 1 }\ntools: {}\n")
    with pytest.raises(PolicyError, match="invalid policy"):
        load_policy(p, str(repo_dir))


def test_l1_rejected(tmp_path, repo_dir):
    p = write(tmp_path / "p.yaml", "tools:\n  filesystem.read: { tiers: { local: L1 } }\n")
    with pytest.raises(PolicyError, match="'filesystem.read'.*L1"):
        load_policy(p, str(repo_dir))


def test_l1_in_principal_override_rejected(tmp_path, repo_dir):
    p = write(tmp_path / "p.yaml",
              "tools:\n  git.diff:\n    tiers: { local: L0 }\n    principals: { alice: { local: L1 } }\n")
    with pytest.raises(PolicyError, match="'git.diff'.*L1"):
        load_policy(p, str(repo_dir))


def test_missing_policy_refused(tmp_path, repo_dir):
    with pytest.raises(PolicyError, match="not found"):
        load_policy(tmp_path / "absent.yaml", str(repo_dir))


def test_policy_inside_repo_refused(repo_dir):
    p = write(repo_dir / "policy.yaml", POLICY)
    with pytest.raises(PolicyError, match="inside the repository"):
        load_policy(p, str(repo_dir))


def test_policy_symlinked_into_repo_refused(repo_dir, tmp_path):
    write(repo_dir / "policy.yaml", POLICY)
    link = tmp_path / "policy.yaml"
    link.symlink_to(repo_dir / "policy.yaml")
    with pytest.raises(PolicyError, match="inside the repository"):
        load_policy(link, str(repo_dir))


@pytest.fixture
def gate(policy_file, repo_dir) -> PolicyGate:
    return PolicyGate(load_policy(policy_file, str(repo_dir)).policy)


async def test_l0_allows(gate):
    d = await gate.decide("filesystem.read", {"path": "x"}, "alice")
    assert d.verdict is Verdict.ALLOW and d.tier == "L0"


async def test_l3_allows(gate):
    d = await gate.decide("git.status", {}, "alice")
    assert d.verdict is Verdict.ALLOW and d.tier == "L3"


async def test_l2_requires_approval(gate):
    d = await gate.decide("git.push", {}, "alice")
    assert d.verdict is Verdict.APPROVAL


async def test_unlisted_denied(gate):
    d = await gate.decide("git.diff", {}, "alice")
    assert d.verdict is Verdict.DENY and d.rule_id == "allowlist.deny"


async def test_no_local_tier_denied(gate):
    d = await gate.decide("git.log", {}, "alice")
    assert d.verdict is Verdict.DENY and d.rule_id == "tier.unassigned"


def test_unexpected_decision_denied():
    assert map_decision(Decision("allow", "tier.L1.dry_run", "L1", True)).rule_id == "policy.unexpected"
    assert map_decision(Decision("allow", "tier.L2.confirmed", "L2")).verdict is Verdict.DENY


def test_output_cap(gate):
    assert gate.output_cap("filesystem.read") == Policy(environment="local").output.max_chars


ALLOW = PolicyDecision(Verdict.ALLOW, "a")
APPROVE = PolicyDecision(Verdict.APPROVAL, "b")
DENY = PolicyDecision(Verdict.DENY, "c")


def test_stricter_overlay_wins():
    assert strictest(ALLOW, APPROVE) is APPROVE
    assert strictest(APPROVE, DENY) is DENY


def test_looser_overlay_never_wins():
    assert strictest(DENY, ALLOW) is DENY
    assert strictest(DENY, APPROVE, ALLOW) is DENY
    assert strictest(APPROVE, ALLOW) is APPROVE


def test_empty_sensitive_pattern_rejected(tmp_path, repo_dir):
    pf = write(tmp_path / "p.yaml", 'repo_warden:\n  sensitive_paths_extra: ["/"]\ntools: {}\n')
    with pytest.raises(PolicyError, match="empty sensitive pattern"):
        load_policy(pf, str(repo_dir))
