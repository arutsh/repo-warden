"""`devkit install | uninstall --git-hooks` and the hook refusing a commit."""

import os
import subprocess
import sys

import pytest

from devkit import install
from devkit.cli import main


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for var in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{var}_NAME", "t")
        monkeypatch.setenv(f"GIT_{var}_EMAIL", "t@example.com")


def git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, check=check, capture_output=True, text=True)


def hook_path(repo):
    return repo / ".git" / "hooks" / "pre-commit"


@pytest.fixture
def exe(tmp_path):
    """A stand-in for the installed `devkit`: runs this checkout's CLI with the test interpreter."""
    path = tmp_path / "bin" / "devkit"
    path.parent.mkdir()
    path.write_text(f"#!/bin/sh\nexec '{sys.executable}' -m devkit.cli \"$@\"\n")
    path.chmod(0o755)
    return str(path)


def test_install_writes_managed_executable_hook(repo, exe):
    assert "installed" in install.install_git_hook(str(repo), exe)
    text = hook_path(repo).read_text()
    assert install.GIT_HOOK_MARKER in text and f"exec {exe} check --staged" in text
    assert os.access(hook_path(repo), os.X_OK)
    assert "already installed" in install.install_git_hook(str(repo), exe)
    assert "installed  (" in install.git_hook_state(str(repo))


def test_reinstall_with_new_path_rewrites_own_hook(repo, exe):
    install.install_git_hook(str(repo), "/old/devkit")
    assert "installed in" in install.install_git_hook(str(repo), exe)
    assert exe in hook_path(repo).read_text()


def test_linked_worktree_uses_shared_hooks(repo, exe, tmp_path):
    git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", str(wt))
    assert (wt / ".git").is_file()
    install.install_git_hook(str(wt), exe)
    assert install.is_managed_git_hook(hook_path(repo).read_text())


def test_refuses_foreign_hook_and_leaves_it(repo, exe, capsys, monkeypatch):
    hook_path(repo).write_text("#!/bin/sh\necho mine\n")
    monkeypatch.chdir(repo)
    assert main(["install", "--git-hooks", "--exe", exe]) == 1
    assert "check --staged" in capsys.readouterr().err
    assert hook_path(repo).read_text() == "#!/bin/sh\necho mine\n"
    assert "not written by devkit" in install.git_hook_state(str(repo))


def test_refuses_when_core_hooks_path_is_set(repo, exe):
    git(repo, "config", "core.hooksPath", ".githooks")
    with pytest.raises(install.InstallError, match="core.hooksPath"):
        install.install_git_hook(str(repo), exe)
    assert not (repo / ".githooks" / "pre-commit").exists() and not hook_path(repo).exists()
    assert "core.hooksPath is set" in install.git_hook_state(str(repo))


def test_not_a_repo(tmp_path, exe):
    with pytest.raises(install.InstallError, match="not in a git repository"):
        install.install_git_hook(str(tmp_path), exe)


def test_commit_staging_id_rsa_refused(repo, exe):
    install.install_git_hook(str(repo), exe)
    (repo / "id_rsa").write_text("key")
    git(repo, "add", "id_rsa")
    result = git(repo, "commit", "-q", "-m", "oops", check=False)
    assert result.returncode != 0
    output = result.stdout + result.stderr  # git sends a hook's stdout to stderr
    assert "block  id_rsa  [secrets]" in output
    assert output.index("[secrets]") < output.index("1 blocking finding")
    assert git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0

    (repo / "notes.txt").write_text("fine")
    git(repo, "rm", "-q", "--cached", "id_rsa")
    git(repo, "add", "notes.txt")
    assert git(repo, "commit", "-q", "-m", "ok", check=False).returncode == 0


def test_uninstall_removes_only_managed_hook(repo, exe, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert main(["install", "--git-hooks", "--exe", exe]) == 0
    assert main(["uninstall", "--git-hooks"]) == 0
    assert not hook_path(repo).exists()
    assert "not installed" in install.uninstall_git_hook(str(repo))

    hook_path(repo).write_text("#!/bin/sh\necho mine\n")
    assert "not written by devkit" in install.uninstall_git_hook(str(repo))
    assert hook_path(repo).read_text() == "#!/bin/sh\necho mine\n"


def test_status_shows_git_hook(repo, exe, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    install.install_git_hook(str(repo), exe)
    assert main(["status"]) == 0
    assert "git hook: installed" in capsys.readouterr().out


def test_install_needs_a_target(capsys):
    assert main(["install"]) == 2
    assert "--git-hooks" in capsys.readouterr().err


def test_edited_hook_is_not_overwritten_or_removed(repo, exe):
    install.install_git_hook(str(repo), exe)
    edited = hook_path(repo).read_text() + "npm test\n"
    hook_path(repo).write_text(edited)
    with pytest.raises(install.InstallError, match="edited since devkit wrote it"):
        install.install_git_hook(str(repo), exe)
    assert "edited since devkit wrote it" in install.uninstall_git_hook(str(repo))
    assert hook_path(repo).read_text() == edited
    assert "edited since devkit wrote it" in install.git_hook_state(str(repo))


def test_marker_alone_is_not_managed():
    assert not install.is_managed_git_hook("#!/bin/sh\n# devkit-managed\nrm -rf build\n")
    assert install.is_managed_git_hook(install.git_hook_script("/a b/devkit"))
