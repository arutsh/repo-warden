"""Global policy: loaded from outside the repo, evaluated with mcp-airlock's Engine, default deny."""

from __future__ import annotations

import enum
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml
from mcp_airlock.policy import Engine, Policy
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .paths import normalize_pattern

ENVIRONMENT = "local"


class PolicyError(Exception):
    """The policy cannot be loaded; the broker must not start."""


class Verdict(enum.IntEnum):
    # Ordered by strictness so max() picks the most restrictive.
    ALLOW = 0
    APPROVAL = 1
    DENY = 2


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    rule_id: str
    tier: str | None = None
    message: str = ""


def strictest(*decisions: PolicyDecision) -> PolicyDecision:
    """Most restrictive decision (DENY > APPROVAL > ALLOW); the earliest wins a tie."""
    if not decisions:
        raise ValueError("strictest() needs at least one decision")
    return max(decisions, key=lambda d: d.verdict)  # max() keeps the first of equal keys


class Approver(Protocol):
    async def request(self, call: dict[str, Any]) -> bool: ...


class RepoWardenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensitive_paths_extra: list[str] = Field(default_factory=list)

    @field_validator("sensitive_paths_extra")
    @classmethod
    def _patterns(cls, v: list[str]) -> list[str]:
        for p in v:
            normalize_pattern(p)  # raises on a pattern that would match nothing
        return v


@dataclass(frozen=True)
class LoadedPolicy:
    path: str
    policy: Policy
    config: RepoWardenConfig


def default_policy_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "repo-warden" / "policy.yaml"


def inside(path: str, root: str) -> bool:
    return os.path.commonpath([root, path]) == root


def load_policy(path: str | os.PathLike[str], repo_root: str) -> LoadedPolicy:
    real = os.path.realpath(path)
    if inside(real, repo_root):
        raise PolicyError(f"policy file {real} is inside the repository; it must live outside it")
    if not os.path.isfile(real):
        raise PolicyError(f"policy file not found: {real}")
    try:
        data = yaml.safe_load(Path(real).read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        raise PolicyError(f"cannot read policy {real}: {e}") from None
    if not isinstance(data, dict):
        raise PolicyError(f"policy {real} must be a YAML mapping")
    try:
        config = RepoWardenConfig.model_validate(data.pop("repo_warden", None) or {})
        data["environment"] = ENVIRONMENT
        policy = Policy.model_validate(data)
    except ValidationError as e:
        raise PolicyError(f"invalid policy {real}: {e}") from None
    for name, rule in policy.tools.items():
        tiers = list(rule.tiers.values()) + [t for o in rule.principals.values() for t in o.values()]
        if "L1" in tiers:
            raise PolicyError(f"policy {real}: capability {name!r} uses tier L1, which repo-warden does not support")
    return LoadedPolicy(real, policy, config)


class PolicyGate:
    """Maps airlock decisions onto ALLOW / APPROVAL / DENY."""

    def __init__(self, policy: Policy):
        self.policy = policy
        self.engine = Engine(policy)

    def output_cap(self, capability: str) -> int:
        return self.policy.output_cap(capability).max_chars

    async def evaluate(self, capability: str, args: dict[str, Any], principal: str) -> tuple[PolicyDecision, Any]:
        """Decide without charging anything; pass the raw decision to `reserve` once every other check passed."""
        d = await self.engine.evaluate(capability, args, principal, confirmed=False, dry_run_supported=False)
        return map_decision(d), d

    async def reserve(self, capability: str, principal: str, raw: Any) -> PolicyDecision:
        """Charge an allowed L3 call to the blast-radius window; DENY when a concurrent call took the last slot."""
        if raw.verdict == "allow" and raw.tier in ("L0", "L3"):
            raw = await self.engine.reserve(principal, capability, raw)
        return map_decision(raw)

    async def decide(self, capability: str, args: dict[str, Any], principal: str) -> PolicyDecision:
        decision, raw = await self.evaluate(capability, args, principal)
        return await self.reserve(capability, principal, raw) if decision.verdict is Verdict.ALLOW else decision


def map_decision(d: Any) -> PolicyDecision:
    if d.verdict == "deny":
        return PolicyDecision(Verdict.DENY, d.rule_id, d.tier, d.message)
    if d.verdict == "confirm":
        return PolicyDecision(Verdict.APPROVAL, d.rule_id, d.tier, d.message)
    if d.verdict == "allow" and d.tier in ("L0", "L3"):
        return PolicyDecision(Verdict.ALLOW, d.rule_id, d.tier, d.message)
    return PolicyDecision(Verdict.DENY, "policy.unexpected", d.tier, f"unexpected policy decision {d.rule_id}")
