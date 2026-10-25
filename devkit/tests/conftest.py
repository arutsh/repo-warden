import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from devkit import rules
from devkit.events import Level


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never read the real user config or agent settings."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return home


@pytest.fixture
def user_config(isolated):
    path = isolated / ".config" / "devkit" / "config.yaml"
    path.parent.mkdir(parents=True)

    def write(text: str) -> Path:
        path.write_text(text)
        return path

    return write


@pytest.fixture
def repo(tmp_path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    return d


@pytest.fixture
def secrets_only(monkeypatch):
    """Only the `secrets` rule registered: no rule reads file content or runs after the tool."""
    for name in [n for n in rules.REGISTRY if n != "secrets"]:  # in place: config.py holds the same dict
        monkeypatch.delitem(rules.REGISTRY, name)


@pytest.fixture
def stub_rules(monkeypatch):
    """Register stub rules: register(name, findings=..., guard=..., default=...) -> the Rule instance."""

    def register(name, *, findings=(), guard=False, default=Level.BLOCK, kinds=None, max_level=Level.BLOCK,
                 phases=None):
        mod = types.ModuleType(f"stub_rule_{name}")

        class Stub(rules.Rule):
            calls = 0

            def check(self, event, settings):
                type(self).calls += 1
                return list(findings)

        Stub.name, Stub.guard, Stub.default_level, Stub.max_level = name, guard, default, max_level
        if phases:
            Stub.phases = frozenset(phases)
        if kinds:
            Stub.kinds = frozenset(kinds)
        mod.RULE = Stub()
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
        monkeypatch.setitem(rules.REGISTRY, name, mod.__name__)
        return mod.RULE

    return register


def payload(tool: str, cwd: str | None = None, **tool_input) -> str:
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                       **({"cwd": cwd} if cwd else {})})
