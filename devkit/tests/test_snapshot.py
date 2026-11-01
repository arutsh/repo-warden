import subprocess

import pytest

from devkit import gitsafe
from devkit.gitsafe import GitError
from devkit.review.snapshot import cache_dir, changed_paths, diff, snapshot
from devkit.rules.secrets import DEFAULT_PATTERNS


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


@pytest.fixture
def committed(repo):
    (repo / "app.py").write_text("a = 1\nb = 2\n")
    git(repo, "add", "app.py")
    git(repo, "commit", "-qm", "init")
    return repo


def tree_files(repo, tree) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", tree).split()


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_id_equals_write_tree(tmp_path, object_format):
    repo = tmp_path / object_format
    subprocess.run(["git", "init", "-q", f"--object-format={object_format}", str(repo)], check=True)
    for name in ("a.py", "a-b", "a.b", "a/x.py", "a/deep/er/y.py", "ab/z", "B", "space name.txt"):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(name)
    (repo / "run.sh").write_text("#!/bin/sh\n")
    (repo / "run.sh").chmod(0o755)
    (repo / "link").symlink_to("a.py")
    sub = repo / "sub"
    subprocess.run(["git", "init", "-q", f"--object-format={object_format}", str(sub)], check=True)
    (sub / "f").write_text("f")
    git(sub, "add", "f")
    git(sub, "commit", "-qm", "sub")
    git(repo, "add", "a.py")  # a real index for the snapshot to copy

    expected = subprocess.run(
        f"cp .git/index {tmp_path}/idx && GIT_INDEX_FILE={tmp_path}/idx git add -A 2>/dev/null"
        f" && GIT_INDEX_FILE={tmp_path}/idx git write-tree", shell=True, cwd=repo, check=True,
        capture_output=True, text=True).stdout.strip()
    tree = snapshot(repo, ())
    assert tree == expected
    assert "160000 commit" in git(repo, "ls-tree", tree, "sub") and "120000" in git(repo, "ls-tree", tree, "link")


def test_same_tree_same_id(committed):
    assert snapshot(committed, DEFAULT_PATTERNS) == snapshot(committed, DEFAULT_PATTERNS)


def test_edit_changes_id(committed):
    before = snapshot(committed, DEFAULT_PATTERNS)
    (committed / "app.py").write_text("a = 1\nb = 3\n")
    assert snapshot(committed, DEFAULT_PATTERNS) != before


def test_untracked_files_included_ignored_left_out(committed):
    (committed / ".gitignore").write_text("build/\n")
    (committed / "build").mkdir()
    (committed / "build" / "out.o").write_text("x")
    (committed / "new.py").write_text("c = 3\n")
    assert tree_files(committed, snapshot(committed, DEFAULT_PATTERNS)) == [".gitignore", "app.py", "new.py"]


def test_untracked_env_file_left_out(committed):
    before = snapshot(committed, DEFAULT_PATTERNS)
    (committed / ".env.local").write_text("API_KEY=sekrit\n")
    after = snapshot(committed, DEFAULT_PATTERNS)
    assert ".env.local" not in tree_files(committed, after) and after == before


def test_secret_paths_left_out_at_any_depth_and_case(committed):
    (committed / "keys").mkdir()
    (committed / "keys" / "ID_RSA").write_text("k")
    (committed / "certs.pem").mkdir()
    (committed / "certs.pem" / "inner.txt").write_text("k")
    (committed / "conf").mkdir()
    (committed / "conf" / "app.tfvars").write_text("k")
    tree = snapshot(committed, (*DEFAULT_PATTERNS, "*.tfvars"))
    assert tree_files(committed, tree) == ["app.py"]


def test_tracked_secret_left_out(committed):
    (committed / ".env.prod").write_text("API_KEY=sekrit\n")
    git(committed, "add", "-f", ".env.prod")
    git(committed, "commit", "-qm", "tracked secret")
    assert tree_files(committed, snapshot(committed, DEFAULT_PATTERNS)) == ["app.py"]
    assert git(committed, "ls-files") == ".env.prod\napp.py\n"  # the user's index keeps it


def test_index_head_and_worktree_untouched(committed):
    (committed / "app.py").write_text("a = 10\nb = 2\n")
    git(committed, "add", "app.py")
    (committed / "app.py").write_text("a = 10\nb = 20\n")
    (committed / "new.py").write_text("c = 3\n")
    def state():
        return [git(committed, *args) for args in (("diff", "--cached"), ("diff",), ("rev-parse", "HEAD"),
                                                   ("status", "--porcelain"), ("for-each-ref",))]

    before = state()
    snapshot(committed, DEFAULT_PATTERNS)
    assert state() == before
    assert (committed / "app.py").read_text() == "a = 10\nb = 20\n"


def test_fresh_repo_without_index(repo):
    (repo / "app.py").write_text("a = 1\n")
    assert tree_files(repo, snapshot(repo, DEFAULT_PATTERNS)) == ["app.py"]
    assert not (repo / ".git" / "index").exists()


def test_hostile_filter_driver_never_runs(committed, tmp_path):
    marker = tmp_path / "pwned"
    git(committed, "config", "filter.x.clean", f"touch {marker}; cat")
    git(committed, "config", "filter.x.required", "true")
    (committed / ".gitattributes").write_text("*.py filter=x\n")
    (committed / "app.py").write_text("a = 1\nb = 3\n")
    tree = snapshot(committed, DEFAULT_PATTERNS)
    assert not marker.exists()
    assert "app.py" in tree_files(committed, tree)


def test_diff_shows_only_the_edit(committed):
    a = snapshot(committed, DEFAULT_PATTERNS)
    (committed / "app.py").write_text("a = 1\nb = 3\n")
    (committed / ".env.local").write_text("API_KEY=sekrit\n")
    b = snapshot(committed, DEFAULT_PATTERNS)
    assert changed_paths(committed, a, b, DEFAULT_PATTERNS) == ["app.py"]
    out = diff(committed, a, b, DEFAULT_PATTERNS)
    assert "app.py | 2 +-" in out and "-b = 2\n+b = 3\n" in out
    assert ".env" not in out and "sekrit" not in out


def test_diff_excludes_secrets_in_trees_taken_with_fewer_patterns(committed):
    a = snapshot(committed, ())
    (committed / "app.tfvars").write_text("token = sekrit\n")
    (committed / "app.py").write_text("a = 2\nb = 2\n")
    b = snapshot(committed, ())
    patterns = (*DEFAULT_PATTERNS, "*.tfvars")
    assert changed_paths(committed, a, b, patterns) == ["app.py"]
    assert "sekrit" not in diff(committed, a, b, patterns)


def test_rename_lists_both_paths(committed):
    a = snapshot(committed, DEFAULT_PATTERNS)
    (committed / "app.py").rename(committed / "main.py")
    b = snapshot(committed, DEFAULT_PATTERNS)
    assert changed_paths(committed, a, b, DEFAULT_PATTERNS) == ["app.py", "main.py"]


def test_hostile_textconv_never_runs_in_diff(committed, tmp_path):
    marker = tmp_path / "pwned"
    a = snapshot(committed, DEFAULT_PATTERNS)
    (committed / "app.py").write_text("a = 1\nb = 3\n")
    b = snapshot(committed, DEFAULT_PATTERNS)
    git(committed, "config", "diff.x.textconv", f"touch {marker}; cat")
    (committed / ".gitattributes").write_text("*.py diff=x\n")
    diff(committed, a, b, DEFAULT_PATTERNS)
    assert not marker.exists()


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "HEAD", "0" * 39, "0" * 40 + "\n"])
def test_non_tree_id_refused(committed, bad):
    with pytest.raises(GitError, match="not a tree id"):
        diff(committed, bad, "0" * 40, DEFAULT_PATTERNS)


def test_submodule_bump_is_a_change(committed):
    sub = committed / "sub"
    subprocess.run(["git", "init", "-q", str(sub)], check=True)
    (sub / "f").write_text("1")
    git(sub, "add", "f")
    git(sub, "commit", "-qm", "1")
    a = snapshot(committed, DEFAULT_PATTERNS)
    (sub / "f").write_text("2")
    git(sub, "commit", "-qam", "2")
    b = snapshot(committed, DEFAULT_PATTERNS)
    assert changed_paths(committed, a, b, DEFAULT_PATTERNS) == ["sub"]
    assert "Subproject commit" in diff(committed, a, b, DEFAULT_PATTERNS)


def test_relative_xdg_cache_home_ignored(isolated, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "cache")
    assert cache_dir() == isolated / ".cache" / "devkit" / "review"


def test_one_time_budget_for_the_whole_snapshot(committed, monkeypatch):
    monkeypatch.setattr(gitsafe, "TIMEOUT_S", 0.0)
    with pytest.raises(GitError, match="out of time"):
        snapshot(committed, DEFAULT_PATTERNS)
    assert not cache_dir().exists() or list(cache_dir().iterdir()) == []
