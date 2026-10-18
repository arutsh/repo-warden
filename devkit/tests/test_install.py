import json
import os
import subprocess
import sys

import pytest

from devkit import install
from devkit.cli import main

EXE = "/opt/dev kit/bin/devkit"


@pytest.fixture
def settings(isolated):
    return isolated / ".claude" / "settings.json"


USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 /x/mine.py"}]}
USER_SETTINGS = {"permissions": {"ask": ["Bash(git push*)"]}, "model": "opus",
                 "hooks": {"PreToolUse": [USER_HOOK], "Stop": [{"hooks": [{"type": "command", "command": "say hi"}]}]}}


def devkit_entries(data):
    return [h["command"] for groups in data.get("hooks", {}).values() for g in groups for h in g["hooks"]
            if "devkit" in h["command"]]


def test_install_into_missing_file(settings):
    assert "installed" in install.install("claude", EXE)
    data = json.loads(settings.read_text())
    assert devkit_entries(data) == ["'/opt/dev kit/bin/devkit' hook claude pre-tool-use"]
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Bash|Read|Grep|Glob|Write|Edit|MultiEdit" and pre["hooks"][0]["timeout"] == 15
    assert not settings.with_name("settings.json.devkit-backup").exists()
    assert oct(settings.stat().st_mode & 0o777) == "0o600"


def test_idempotent_and_preserves_user_settings(settings):
    settings.parent.mkdir()
    settings.write_text(json.dumps(USER_SETTINGS, indent=4))
    os.chmod(settings, 0o640)
    install.install("claude", EXE)
    first = settings.read_text()
    assert "already installed" in install.install("claude", EXE)
    assert settings.read_text() == first
    data = json.loads(first)
    assert data["permissions"] == USER_SETTINGS["permissions"] and data["model"] == "opus"
    assert data["hooks"]["PreToolUse"][0] == USER_HOOK and data["hooks"]["Stop"] == USER_SETTINGS["hooks"]["Stop"]
    assert len(devkit_entries(data)) == 1
    assert oct(settings.stat().st_mode & 0o777) == "0o640"


def test_backup_written_once(settings):
    settings.parent.mkdir()
    settings.write_text(json.dumps(USER_SETTINGS))
    install.install("claude", EXE)
    install.uninstall("claude")
    install.install("claude", "/other/devkit")
    backup = settings.with_name("settings.json.devkit-backup")
    assert json.loads(backup.read_text()) == USER_SETTINGS


def test_reinstall_with_new_path_replaces_entries(settings):
    install.install("claude", EXE)
    install.install("claude", "/new/devkit")
    assert devkit_entries(json.loads(settings.read_text())) == ["/new/devkit hook claude pre-tool-use"]


def test_uninstall_keeps_user_hooks(settings):
    settings.parent.mkdir()
    settings.write_text(json.dumps(USER_SETTINGS))
    install.install("claude", EXE)
    assert "removed" in install.uninstall("claude")
    assert json.loads(settings.read_text()) == USER_SETTINGS
    assert "not installed" in install.uninstall("claude")


def test_uninstall_shared_group_keeps_other_hooks(settings):
    settings.parent.mkdir()
    shared = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "devkit hook claude pre-tool-use"},
        {"type": "command", "command": "python3 mine.py"}]}]}}
    settings.write_text(json.dumps(shared))
    install.uninstall("claude")
    assert json.loads(settings.read_text())["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "python3 mine.py"}]


@pytest.mark.parametrize("text", ["{not json", "[1]", '{"hooks": []}'])
def test_invalid_file_refused_unchanged(settings, text):
    settings.parent.mkdir()
    settings.write_text(text)
    with pytest.raises(install.InstallError):
        install.install("claude", EXE)
    assert settings.read_text() == text
    assert main(["install", "--agent", "claude", "--exe", EXE]) == 1


def test_post_hook_registered_when_a_rule_runs_post(settings, stub_rules):
    stub_rules("p", guard=True, phases={"post"})
    install.install("claude", EXE)
    assert devkit_entries(json.loads(settings.read_text()))[-1] == "'/opt/dev kit/bin/devkit' hook claude post-tool-use"


def test_symlinked_settings_written_through(settings, tmp_path):
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir()
    real.write_text(json.dumps(USER_SETTINGS))
    settings.parent.mkdir()
    settings.symlink_to(real)
    install.install("claude", EXE)
    assert settings.is_symlink() and len(devkit_entries(json.loads(real.read_text()))) == 1
    assert settings.with_name("settings.json.devkit-backup").is_file()


def test_backup_keeps_original_mode(settings):
    settings.parent.mkdir()
    settings.write_text(json.dumps(USER_SETTINGS))
    os.chmod(settings, 0o600)
    install.install("claude", EXE)
    assert oct(settings.with_name("settings.json.devkit-backup").stat().st_mode & 0o777) == "0o600"


def test_empty_hooks_object_is_not_an_install(settings):
    settings.parent.mkdir()
    settings.write_text('{"hooks": {}}')
    assert not install.installed("claude")
    assert "not installed" in install.uninstall("claude")
    assert settings.read_text() == '{"hooks": {}}'


def test_non_list_event_refused(settings):
    settings.parent.mkdir()
    settings.write_text('{"hooks": {"PreToolUse": {}}}')
    with pytest.raises(install.InstallError):
        install.install("claude", EXE)
    assert main(["install", "--agent", "claude", "--exe", EXE]) == 1


@pytest.mark.parametrize("command", [r"'C:\Tools\devkit.EXE' hook claude pre-tool-use",
                                     "/usr/bin/devkit.exe hook claude pre-tool-use"])
def test_windows_exe_recognised(command):
    assert install.is_managed({"type": "command", "command": command}, "claude")


def test_claude_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    install.install("claude", EXE)
    assert (tmp_path / "cc" / "settings.json").is_file()


def test_unknown_agent_rejected(capsys):
    with pytest.raises(SystemExit):
        main(["install", "--agent", "cursor"])
    assert "supported: claude, codex" in capsys.readouterr().err


def test_status_reports_install_state(settings, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    main(["status"])
    assert "claude   not installed" in capsys.readouterr().out
    main(["install", "--agent", "claude", "--exe", EXE])
    main(["status"])
    assert "claude   installed" in capsys.readouterr().out


def test_status_levels_and_sources(repo, user_config, monkeypatch, capsys):
    user_config("rules:\n  secrets: { patterns: ['*.tfvars'] }\n")
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: off\n")
    monkeypatch.chdir(repo)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "opted in: .devkit.yaml" in out and "secrets          block  (default)" in out
    assert 'patterns: ["*.tfvars"]  (user: ' in out
    assert "rules.secrets.level: off (repo: .devkit.yaml: a repo cannot loosen a guard rule)" in out


def test_status_invalid_config_exits_nonzero(repo, monkeypatch, capsys):
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: maybe\n")
    monkeypatch.chdir(repo)
    assert main(["status"]) == 1
    err = capsys.readouterr().err
    assert ".devkit.yaml" in err and "rules.secrets.level" in err


def test_console_script_end_to_end(repo):
    """The installed `devkit` entry point, as an agent would run it."""
    exe = os.path.join(os.path.dirname(sys.executable), "devkit")
    stdin = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat .env"}, "cwd": str(repo)})
    res = subprocess.run([exe, "hook", "claude", "pre-tool-use"], input=stdin, capture_output=True, text=True)
    assert res.returncode == 0 and json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- Codex: the same guarantees in ~/.codex/hooks.json ---

@pytest.fixture
def codex_hooks(isolated):
    return isolated / ".codex" / "hooks.json"


CODEX_USER = {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": [
    {"type": "command", "command": "/usr/bin/python3 ~/.codex/hooks/policy.py", "statusMessage": "Checking"}]}]}}


def test_codex_install_into_missing_file(codex_hooks):
    msg = install.install("codex", EXE)
    assert "installed" in msg and "/hooks" in msg
    data = json.loads(codex_hooks.read_text())
    assert devkit_entries(data) == ["'/opt/dev kit/bin/devkit' hook codex pre-tool-use"]
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "^(Bash|apply_patch)$" and pre["hooks"][0]["timeout"] == 15


def test_codex_idempotent_backup_and_uninstall(codex_hooks):
    codex_hooks.parent.mkdir()
    codex_hooks.write_text(json.dumps(CODEX_USER))
    install.install("codex", EXE)
    first = codex_hooks.read_text()
    assert "already installed" in install.install("codex", EXE) and codex_hooks.read_text() == first
    data = json.loads(first)
    assert data["hooks"]["PreToolUse"][0] == CODEX_USER["hooks"]["PreToolUse"][0] and len(devkit_entries(data)) == 1
    assert "removed" in install.uninstall("codex")
    assert json.loads(codex_hooks.read_text()) == CODEX_USER
    install.install("codex", "/other/devkit")
    assert json.loads(codex_hooks.with_name("hooks.json.devkit-backup").read_text()) == CODEX_USER


def test_codex_invalid_json_refused_unchanged(codex_hooks):
    codex_hooks.parent.mkdir()
    codex_hooks.write_text("{oops")
    assert main(["install", "--agent", "codex", "--exe", EXE]) == 1
    assert codex_hooks.read_text() == "{oops"


def test_agents_are_independent(settings, codex_hooks):
    main(["install", "--agent", "claude,codex", "--exe", EXE])
    install.uninstall("claude")
    assert not install.installed("claude") and install.installed("codex")
    assert devkit_entries(json.loads(codex_hooks.read_text())) == ["'/opt/dev kit/bin/devkit' hook codex pre-tool-use"]


def test_codex_post_hook_when_a_rule_runs_post(codex_hooks, stub_rules):
    stub_rules("p", guard=True, phases={"post"})
    install.install("codex", EXE)
    post = json.loads(codex_hooks.read_text())["hooks"]["PostToolUse"][0]
    assert post["matcher"] == "^apply_patch$" and post["hooks"][0]["command"].endswith("hook codex post-tool-use")


@pytest.mark.parametrize("toml,warned", [
    ("[features]\nhooks = false\n", True),
    ("[features]\ncodex_hooks = false\n", True),
    ("[features]\nhooks = true\ncodex_hooks = false\n", False),
    ("model = 'gpt-5'\n", False),
    ("[features\n", True),  # unreadable: say we could not check
])
def test_codex_hooks_disabled_warning(isolated, toml, warned):
    (isolated / ".codex").mkdir()
    (isolated / ".codex" / "config.toml").write_text(toml)
    assert ("warning:" in install.install("codex", EXE)) is warned
    assert ("warning:" in install.install("codex", EXE)) is warned  # also when already installed


def test_codex_status_shows_warning(isolated, repo, monkeypatch, capsys):
    (isolated / ".codex").mkdir()
    (isolated / ".codex" / "config.toml").write_text("[features]\nhooks = false\n")
    monkeypatch.chdir(repo)
    main(["status"])
    out = capsys.readouterr().out
    assert "codex    not installed" in out and "hooks are disabled" in out


def test_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "cx"))
    install.install("codex", EXE)
    assert (tmp_path / "cx" / "hooks.json").is_file()
