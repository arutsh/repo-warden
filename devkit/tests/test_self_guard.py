import io
import json

import pytest

from devkit.cli import run_hook
from devkit.engine import evaluate
from devkit.events import Event
from devkit.rules.self_guard import RULE


def check(event):
    return RULE.check(event, {})


@pytest.mark.parametrize("command", [
    "devkit uninstall --agent claude",
    "/home/u/.local/bin/devkit uninstall --agent claude,codex",
    "cd /tmp && devkit uninstall --agent codex",
    "'/opt/dev kit/devkit' uninstall",
    r"C:\Tools\devkit.EXE uninstall --agent claude",
])
def test_uninstall_flagged(command):
    (f,) = check(Event("shell", command=command))
    assert f.rule == "self_guard" and "ask the user" in f.message.lower()


@pytest.mark.parametrize("command", ["devkit status", "devkit install --agent claude", "devkit hook claude pre-tool-use",
                                     "pip uninstall mydevkit", "echo devkits uninstall"])
def test_other_commands_allowed(command):
    assert check(Event("shell", command=command)) == []


def test_hook_and_config_files_flagged(isolated):
    for path in (isolated / ".claude" / "settings.json", isolated / ".codex" / "hooks.json",
                 isolated / ".config" / "devkit" / "config.yaml"):
        for kind in ("write", "edit"):
            (f,) = check(Event(kind, path=str(path)))
            assert str(path) in f.message


def test_env_overrides_followed(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    for path in ("cc/settings.json", "cx/hooks.json", "xdg/devkit/config.yaml"):
        assert check(Event("edit", path=str(tmp_path / path)))


def test_symlinked_settings_flagged_by_either_path(isolated, tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text("{}")
    (isolated / ".claude").mkdir()
    (isolated / ".claude" / "settings.json").symlink_to(real)
    assert check(Event("edit", path=str(real)))
    assert check(Event("edit", path=str(isolated / ".claude" / "settings.json")))


def test_reads_and_other_files_allowed(isolated):
    assert check(Event("edit", path=str(isolated / ".claude" / "settings.local.json"))) == []
    assert check(Event("edit", path=str(isolated / "project" / ".devkit.yaml"))) == []
    assert evaluate([Event("read", path=str(isolated / ".claude" / "settings.json"))]).findings == []


def test_repo_config_cannot_turn_it_off(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  self_guard: off\n")
    assert evaluate([Event("shell", command="devkit uninstall --agent claude", cwd=str(repo))]).blocked


def hook(agent, payload):
    out, err = io.StringIO(), io.StringIO()
    assert run_hook(agent, "pre-tool-use", io.StringIO(json.dumps(payload)), out, err) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


def test_through_the_claude_adapter(repo, isolated):
    bash = hook("claude", {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "devkit uninstall --agent claude"}})
    assert bash["permissionDecision"] == "deny" and "self_guard" in bash["permissionDecisionReason"]
    settings = str(isolated / ".claude" / "settings.json")
    edit = hook("claude", {"tool_name": "Edit", "cwd": str(repo),
                           "tool_input": {"file_path": settings, "old_string": "a", "new_string": "b"}})
    assert edit["permissionDecision"] == "deny"


def test_through_the_codex_adapter(repo, isolated):
    bash = hook("codex", {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": "devkit uninstall --agent codex"}})
    assert bash["permissionDecision"] == "deny"
    patch = f"*** Begin Patch\n*** Update File: {isolated / '.codex' / 'hooks.json'}\n@@\n-a\n+b\n*** End Patch\n"
    assert hook("codex", {"tool_name": "apply_patch", "cwd": str(repo),
                          "tool_input": {"command": patch}})["permissionDecision"] == "deny"
