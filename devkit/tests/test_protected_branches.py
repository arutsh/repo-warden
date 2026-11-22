import io
import json
import subprocess

import pytest

from devkit import config
from devkit.cli import run_hook
from devkit.engine import evaluate
from devkit.events import Event, Level
from devkit.rules.protected_branches import RULE, git_calls, pushed_branches

from .conftest import payload

SETTINGS = {"branches": ["main", "master"], "exempt_repos": []}
GRANDFLOW = """git branch --show-current && git add -A docs/ openspec/ && git status -s && git commit -q -F - <<'EOF'
chore(openspec): archive budget-fix-421-ledger-cleanup

Then git push origin main once it is reviewed.
EOF
git log --oneline -1 && git push origin main 2>&1 | tail -5"""


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)


@pytest.fixture
def main_repo(repo):
    git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    (repo / "a.txt").write_text("a\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def check(repo, command, **settings):
    return RULE.check(Event("shell", command=command, cwd=str(repo)), {**SETTINGS, **settings})


def calls(command, cwd="/r"):
    return [(c.dir, c.sub, *c.args) for c in git_calls(command, cwd)]


def test_grandflow_command_parsed():
    got = calls(GRANDFLOW)
    assert [c[1] for c in got] == ["branch", "add", "status", "commit", "log", "push"]
    assert got[-1] == ("/r", "push", "origin", "main")


@pytest.mark.parametrize("command", [
    "git commit -m 'then git push origin main'",
    'git commit -m "fix: git push origin main && rm -rf x"',
    "cat <<EOF > notes.md\ngit push origin main\nEOF",
])
def test_data_is_not_a_command(command):
    assert all(c[1] != "push" for c in calls(command))


def test_shell_c_scripts_and_redirections():
    assert calls("bash -lc 'cd sub && git push origin main 2>&1 | tail -5'") == [("/r/sub", "push", "origin", "main")]
    assert calls("sh -c \"git commit -m x\" > log.txt") == [("/r", "commit", "-m", "x")]
    assert calls("git push origin main 2> /dev/null &> out") == [("/r", "push", "origin", "main")]
    assert calls("git push & git status")[0][1:] == ("push",)


def test_directories_followed():
    assert calls("git -C ../other push")[0][:2] == ("/other", "push")
    assert calls("cd sub && git commit")[0][:2] == ("/r/sub", "commit")
    assert calls("cd - && git push")[0][0] is None
    assert calls("git -c core.x=1 --no-pager commit -q")[0][1:] == ("commit", "-q")


@pytest.mark.parametrize("args", [
    "origin main", "origin +main", "origin HEAD:main", "origin feat:main", "origin :main",
    "origin refs/heads/main", "--delete origin main", "origin --delete main", "-f origin main", "--all origin",
    "--mirror origin", "origin 'refs/heads/*'",
])
def test_protected_push_targets(args):
    every, names = pushed_branches(args.replace("'", "").split(), "feat/x")
    assert every or "main" in names


@pytest.mark.parametrize("args", [
    "origin feat/x", "-u origin feat/x", "--set-upstream origin feat/x", "origin v1.0 --tags",
    "origin refs/tags/v1", "--tags", "--force-with-lease=feat/x origin feat/x",
])
def test_unprotected_push_targets(args):
    every, names = pushed_branches(args.split(), "feat/x")
    assert not every and "main" not in names


@pytest.mark.parametrize("args", ["", "origin", "origin HEAD", "-u", "--force-with-lease"])
def test_branchless_push_follows_the_current_branch(args):
    assert pushed_branches(args.split(), "main") == (False, ["main"])
    assert pushed_branches(args.split(), "feat/x") == (False, ["feat/x"])
    assert pushed_branches(args.split(), None) == (False, [])


def test_commit_and_push_on_main_blocked(main_repo):
    (f,) = check(main_repo, "git add -A && git commit -m x && git push origin main")
    assert f.rule == "protected_branches" and "commit on the protected branch 'main'" in f.message
    assert "pull request" in f.message and "ask the user" in f.message
    assert evaluate([Event("shell", command="git commit -m x", cwd=str(main_repo))]).blocked


def test_the_grandflow_command_blocked(main_repo):
    (f,) = check(main_repo, GRANDFLOW)
    assert "'main'" in f.message


@pytest.mark.parametrize("command", ["git push", "git push origin", "git push origin HEAD", "git push -u"])
def test_bare_push_on_main_blocked(main_repo, command):
    (f,) = check(main_repo, command)
    assert "push to the protected branch 'main'" in f.message


def test_push_to_main_from_a_feature_branch_blocked(main_repo):
    git(main_repo, "switch", "-q", "-c", "feat/x")
    assert check(main_repo, "git push origin HEAD:main")


def test_feature_branch_work_allowed(main_repo):
    git(main_repo, "switch", "-q", "-c", "feat/x")
    assert check(main_repo, 'git commit -m "fix main menu" && git push -u origin feat/x') == []
    assert check(main_repo, "git push") == []


@pytest.mark.parametrize("command", [
    "git switch -c feat/y && git commit -m y && git push -u origin feat/y",
    "git checkout -b feat/y && git commit -m y && git push",
    "git switch --detach && git commit -m y",
])
def test_branching_off_main_first_allowed(main_repo, command):
    assert check(main_repo, command) == []


def test_switching_back_to_main_is_followed(main_repo):
    git(main_repo, "switch", "-q", "-c", "feat/x")
    assert check(main_repo, "git checkout main && git commit -m x")
    assert check(main_repo, "git checkout -- a.txt && git commit -m x") == []


def test_repo_added_branch(main_repo):
    git(main_repo, "switch", "-q", "-c", "feat/x")
    assert check(main_repo, "git push origin release", branches=["main", "master", "release"])
    assert check(main_repo, "git push origin release") == []


def test_master_protected_by_default(repo):
    git(repo, "symbolic-ref", "HEAD", "refs/heads/master")
    assert check(repo, "git commit -m x")


@pytest.mark.parametrize("command", ["gh pr merge 12 --merge", "git push origin v1.0", "git commit-tree x",
                                     "echo git push origin main", "git log --grep=push"])
def test_not_reported(main_repo, command):
    assert check(main_repo, command) == []


def test_detached_head_and_non_repo(main_repo, tmp_path):
    git(main_repo, "switch", "-q", "--detach")
    assert check(main_repo, "git commit -m x && git push") == []
    outside = tmp_path / "plain"
    outside.mkdir()
    assert check(outside, "git commit -m x && git push origin HEAD") == []


def test_worktree_branch_read(main_repo, tmp_path):
    wt = tmp_path / "wt"
    git(main_repo, "worktree", "add", "-q", "-b", "feat/w", str(wt))
    assert check(wt, "git commit -m x") == []
    assert check(wt, "git checkout main && git commit -m x")


def test_exempt_repo(main_repo, monkeypatch, isolated):
    monkeypatch.setenv("HOME", str(main_repo.parent))
    assert check(main_repo, "git commit -m x", exempt_repos=["~/repo"]) == []


def test_runs_no_git(main_repo, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the rule ran a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    assert check(main_repo, "ls -la && cat a.txt") == []
    assert check(main_repo, "git commit -m x")


@pytest.mark.parametrize("value", [[""], ["a b"], ["x:y"], ["a..b"], [3]])
def test_invalid_branches_rejected(main_repo, value):
    (main_repo / ".devkit.yaml").write_text(json.dumps({"rules": {"protected_branches": {"branches": value}}}))
    with pytest.raises(config.ConfigError, match="rules.protected_branches.branches"):
        config.load(main_repo, strict=True)


def test_repo_cannot_exempt_itself(main_repo):
    (main_repo / ".devkit.yaml").write_text("rules:\n  protected_branches: { level: off, exempt_repos: ['.'] }\n")
    cfg = config.load(main_repo, strict=True)
    rc = cfg.rules["protected_branches"]
    assert rc.level is Level.BLOCK and rc.settings["exempt_repos"] == []
    assert sum("rules.protected_branches." in i for i in cfg.ignored) == 2
    assert evaluate([Event("shell", command="git commit -m x", cwd=str(main_repo))]).blocked


def test_repo_adds_a_branch_and_status_shows_ignored(main_repo, monkeypatch, capsys):
    (main_repo / ".devkit.yaml").write_text("rules:\n  protected_branches: { level: warn, branches: [release] }\n")
    assert config.load(main_repo, strict=True).rules["protected_branches"].settings["branches"] == \
        ["main", "master", "release"]
    from devkit.cli import main
    monkeypatch.chdir(main_repo)
    main(["status"])
    out = capsys.readouterr().out
    assert "protected_branches" in out and "rules.protected_branches.level: warn" in out


def test_user_config_exempts_a_repo(main_repo, user_config):
    user_config(f"rules:\n  protected_branches: {{ exempt_repos: ['{main_repo}'] }}\n")
    assert not evaluate([Event("shell", command="git commit -m x", cwd=str(main_repo))]).blocked
    user_config("rules:\n  protected_branches: warn\n")
    assert not evaluate([Event("shell", command="git commit -m x", cwd=str(main_repo))]).blocked


def _hook(agent, stdin):
    out, err = io.StringIO(), io.StringIO()
    assert run_hook(agent, "pre-tool-use", io.StringIO(stdin), out, err) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"]


def test_claude_hook_denies_the_grandflow_command(main_repo):
    hso = _hook("claude", payload("Bash", str(main_repo), command=GRANDFLOW))
    assert hso["permissionDecision"] == "deny" and "protected_branches" in hso["permissionDecisionReason"]


def test_codex_hook_denies_the_grandflow_command(main_repo):
    stdin = json.dumps({"session_id": "s", "turn_id": "t", "hook_event_name": "PreToolUse", "cwd": str(main_repo),
                        "tool_name": "Bash", "tool_use_id": "c", "tool_input": {"command": ["bash", "-lc", GRANDFLOW]}})
    hso = _hook("codex", stdin)
    assert hso["permissionDecision"] == "deny" and "protected_branches" in hso["permissionDecisionReason"]
