"""No capability may select or redefine the repository, workspace, root or session."""

import re

from pydantic import BaseModel, ConfigDict

from repo_warden.capabilities import Capability, Registry, Risk
from repo_warden.handlers import default_registry

SELECTOR = re.compile(r"(repo|repository|workspace|root|session|cwd|workdir|chdir)", re.I)


def _schema_names(schema: dict) -> list[str]:
    names = []
    for key, sub in schema.get("properties", {}).items():
        names.append(key)
        if isinstance(sub, dict):
            names += _schema_names(sub)
    for sub in schema.get("$defs", {}).values():
        names += _schema_names(sub)
    return names


def selector_violations(registry: Registry) -> list[str]:
    bad = []
    for cap in registry:
        if SELECTOR.search(cap.name):
            bad.append(cap.name)
        bad += [f"{cap.name}:{n}" for n in _schema_names(cap.input.model_json_schema()) if SELECTOR.search(n)]
    return bad


def test_registered_capabilities_have_no_selectors():
    assert len(default_registry()) > 0
    assert selector_violations(default_registry()) == []


def test_guard_catches_a_workspace_field():
    class Dummy(BaseModel):
        model_config = ConfigDict(extra="forbid")
        path: str
        workspace: str

    async def h(ctx, inp):
        return {}

    reg = Registry([Capability("dummy.read", Dummy, h, Risk.READ, "", ())])
    assert selector_violations(reg) == ["dummy.read:workspace"]
