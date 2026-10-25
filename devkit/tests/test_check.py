"""`devkit check --staged | --range` in a temp repo."""

import io
import subprocess

import pytest

from devkit.check import check

DOCSTRING = 'def f():\n    """One.\n\n    Two.\n    Three.\n    """\n    return 1\n'
COMMENT = "# one\n# two\n# three\n# four\n"


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "t")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "t@example.com")
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, files, message="change"):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", *files)
    git(repo, "commit", "-q", "--no-verify", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def run(repo, **mode):
    out, err = io.StringIO(), io.StringIO()
    code = check(str(repo), out=out, err=err, **mode)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def opted(repo):
    (repo / ".devkit.yaml").write_text("")
    commit(repo, {".devkit.yaml": "", "README.md": "hi\n"}, "init")
    return repo


def test_staged_secret_file_blocks(repo):
    (repo / ".env.prod").write_text("KEY=1\n")
    git(repo, "add", ".env.prod")
    code, out, err = run(repo, staged=True)
    assert code == 1
    assert "block  .env.prod  [secrets]" in out and "1 blocking finding" in err


def test_secret_staged_in_a_subdirectory_of_a_repo_with_history(opted):
    (opted / "config").mkdir()
    (opted / "config" / "id_rsa").write_text("x")
    git(opted, "add", "config/id_rsa")
    code, out, _ = run(opted, staged=True)
    assert code == 1 and "config/id_rsa  [secrets]" in out


def test_untouched_docstring_not_blamed(opted):
    commit(opted, {"app.py": DOCSTRING})
    (opted / "app.py").write_text(DOCSTRING.replace("return 1", "return 2"))
    git(opted, "add", "app.py")
    assert run(opted, staged=True) == (0, "", "")


def test_new_long_comment_in_staged_file(opted):
    (opted / "app.py").write_text("x = 1\n" + COMMENT)
    git(opted, "add", "app.py")
    code, out, _ = run(opted, staged=True)
    assert code == 1 and "app.py:2  [comment_brevity]" in out


def test_staged_content_not_worktree(opted):
    (opted / "app.py").write_text("x = 1\n")
    git(opted, "add", "app.py")
    (opted / "app.py").write_text(COMMENT)  # unstaged: not part of the commit
    assert run(opted, staged=True)[0] == 0


def test_rename_keeps_old_content_as_before(opted):
    commit(opted, {"old.py": COMMENT + "x = 1\n"})
    git(opted, "mv", "old.py", "new.py")
    assert run(opted, staged=True)[0] == 0


def test_style_rules_skip_repo_not_opted_in(repo):
    (repo / "app.py").write_text(COMMENT)
    git(repo, "add", "app.py")
    assert run(repo, staged=True)[0] == 0


def test_malformed_repo_config_exits_2(repo):
    (repo / ".devkit.yaml").write_text("rules: [unclosed\n")
    code, _, err = run(repo, staged=True)
    assert code == 2 and ".devkit.yaml" in err


def test_unknown_rule_in_config_exits_2(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  nope: block\n")
    code, _, err = run(repo, staged=True)
    assert code == 2 and "rules.nope" in err


def test_git_failure_exits_2(tmp_path):
    code, _, err = run(tmp_path, staged=True)
    assert code == 2 and "git" in err


def test_range_over_two_commits(opted):
    base = git(opted, "rev-parse", "HEAD")
    commit(opted, {"a.py": "x = 1\n" + COMMENT}, "first")
    commit(opted, {"certs/server.KEY": "k"}, "second")
    code, out, _ = run(opted, spec=f"{base}..HEAD")
    assert code == 1
    assert "a.py:2  [comment_brevity]" in out and "certs/server.KEY  [secrets]" in out


def test_range_counts_from_the_merge_base(opted):
    git(opted, "branch", "-M", "main")
    git(opted, "switch", "-q", "-c", "feat/x")
    commit(opted, {"b.py": "y = 2\n"}, "feature")
    git(opted, "switch", "-q", "main")
    commit(opted, {"main.py": COMMENT}, "on main after the branch")
    git(opted, "switch", "-q", "feat/x")
    assert run(opted, spec="main..HEAD") == (0, "", "")
    assert run(opted, spec="main...HEAD")[0] == 0


def test_invalid_range_exits_2(opted):
    assert run(opted, spec="HEAD")[0] == 2
    assert run(opted, spec="nope..HEAD")[0] == 2
    assert run(opted, spec="--output=x..HEAD")[0] == 2


def test_initial_commit_staged(repo):
    (repo / "README.md").write_text("hi\n")
    git(repo, "add", "README.md")
    assert run(repo, staged=True) == (0, "", "")


def test_branch_name_checks_current_branch(opted):
    (opted / ".devkit.yaml").write_text("rules:\n  branch_name: { level: block, pattern: '^feat/.+$' }\n")
    git(opted, "switch", "-q", "-c", "tmp")
    code, out, _ = run(opted, staged=True)
    assert code == 1 and "(branch)  [branch_name]" in out and "'tmp'" in out
    git(opted, "branch", "-m", "feat/ok")
    assert run(opted, staged=True)[0] == 0


def test_branch_name_detached_head_uses_github_head_ref(opted, monkeypatch):
    (opted / ".devkit.yaml").write_text("rules:\n  branch_name: warn\n")
    git(opted, "switch", "-q", "--detach")
    assert run(opted, staged=True) == (0, "", "")
    monkeypatch.setenv("GITHUB_HEAD_REF", "bad-name")
    code, out, _ = run(opted, staged=True)
    assert code == 0 and "warn   (branch)  [branch_name]" in out


def test_warn_only_exits_0(opted):
    (opted / ".devkit.yaml").write_text("rules:\n  comment_brevity: warn\n")
    (opted / "app.py").write_text(COMMENT)
    git(opted, "add", "app.py")
    code, out, err = run(opted, staged=True)
    assert code == 0 and "warn   app.py:1  [comment_brevity]" in out and err == ""


def test_test_factories_runs_in_check(opted):
    (opted / ".devkit.yaml").write_text("rules:\n  test_factories: { level: block, discouraged_calls: [make_user] }\n")
    (opted / "tests").mkdir()
    (opted / "tests" / "test_a.py").write_text("u = make_user()\n")
    git(opted, "add", "tests/test_a.py")
    code, out, _ = run(opted, staged=True)
    assert code == 0 and "warn   tests/test_a.py:1  [test_factories]" in out


def test_cli_entry_point(opted, monkeypatch, capsys):
    from devkit.cli import main

    (opted / ".env").write_text("x")
    git(opted, "add", ".env")
    monkeypatch.chdir(opted)
    assert main(["check", "--staged"]) == 1
    assert "[secrets]" in capsys.readouterr().out


def test_config_loaded_once_per_check(opted, monkeypatch):
    from devkit import config

    calls = []
    real = config.load
    monkeypatch.setattr(config, "load", lambda *a, **k: calls.append(a) or real(*a, **k))
    (opted / "a.py").write_text("x = 1\n")
    (opted / "b.py").write_text(COMMENT)
    git(opted, "add", "a.py", "b.py")
    assert run(opted, staged=True)[0] == 1
    assert len(calls) == 1


def conflicted_merge(repo, theirs):
    """main gains `theirs` plus a conflicting edit; merging it into feat/x stops at the conflict."""
    git(repo, "branch", "-M", "main")
    commit(repo, {"shared.txt": "base\n"}, "base")
    git(repo, "switch", "-q", "-c", "feat/x")
    commit(repo, {"shared.txt": "ours\n"}, "ours")
    git(repo, "switch", "-q", "main")
    commit(repo, {"shared.txt": "theirs\n", **theirs}, "theirs")
    git(repo, "switch", "-q", "feat/x")
    assert subprocess.run(["git", "merge", "-q", "main"], cwd=repo, capture_output=True).returncode != 0
    (repo / "shared.txt").write_text("resolved\n")
    git(repo, "add", "shared.txt")


def test_merge_in_progress_softens_style_findings(opted):
    conflicted_merge(opted, {"main.py": COMMENT})
    code, out, err = run(opted, staged=True)
    assert code == 0 and "warn   main.py:1  [comment_brevity]" in out and "merge in progress" in err


def test_merge_in_progress_still_blocks_secrets(opted):
    conflicted_merge(opted, {".env": "KEY=1\n"})
    code, out, _ = run(opted, staged=True)
    assert code == 1 and "block  .env  [secrets]" in out
