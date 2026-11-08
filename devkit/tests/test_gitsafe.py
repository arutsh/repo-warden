import subprocess

import pytest

from devkit import gitsafe


def test_parity_with_repo_warden():
    from repo_warden import gitcmd

    assert gitsafe.GIT_ENV == gitcmd.GIT_ENV
    assert gitsafe.BASE_OVERRIDES == gitcmd.BASE_OVERRIDES
    assert gitsafe._DRIVER_KEY.pattern == gitcmd._DRIVER_KEY.pattern
    assert gitsafe.CONFIG_MAX_OUTPUT == gitcmd.CONFIG_MAX_OUTPUT
    names = ["core.bare", "filter.a.b.clean", "filter.a.b.process", "diff.x.textconv", "merge.m.driver",
             "diff.x.command", "filter.c.smudge", "filter.a.b.clean"]
    assert gitsafe.driver_overrides(names) == gitcmd.driver_overrides(names)
    for mod in (gitsafe, gitcmd):
        with pytest.raises(mod.GitConfigError):
            mod.driver_overrides(["filter.a=b.clean"])


def test_filter_clean_key_is_blanked():
    assert gitsafe.driver_overrides(["core.bare", "filter.x.clean", "filter.x.smudge"]) == [
        "filter.x.clean=", "filter.x.smudge=", "filter.x.required=false"]


def test_repo_overrides_lists_the_repos_drivers(repo):
    subprocess.run(["git", "-C", str(repo), "config", "filter.x.clean", "touch pwned"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "diff.y.textconv", "touch pwned"], check=True)
    assert gitsafe.repo_overrides(repo) == ["filter.x.clean=", "diff.y.textconv=", "filter.x.required=false"]


def test_unoverridable_key_refused():
    with pytest.raises(gitsafe.GitConfigError):
        gitsafe.driver_overrides(["filter.a=b.clean"])


def test_oversized_config_refused(repo, monkeypatch):
    monkeypatch.setattr(gitsafe, "CONFIG_MAX_OUTPUT", 10)
    with pytest.raises(gitsafe.GitConfigError):
        gitsafe.run(repo, "rev-parse", "--git-dir")


def test_inherited_git_env_is_dropped(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other-index"))
    assert gitsafe.run(repo, "rev-parse", "--git-dir").strip() == b".git"


def test_failure_raises(repo):
    with pytest.raises(gitsafe.GitError, match="git cat-file failed"):
        gitsafe.run(repo, "cat-file", "-e", "0" * 40)
