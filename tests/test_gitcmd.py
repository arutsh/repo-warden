import pytest

from repo_warden.execution import DirectBackend
from repo_warden.gitcmd import BASE_OVERRIDES, GitConfigError, driver_overrides, git_argv, git_env, repo_overrides

from .conftest import git


def overrides(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]


def test_base_env():
    env = git_env()
    for k, v in {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                 "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                 "GIT_PAGER": "cat", "PAGER": "cat", "GIT_ATTR_NOSYSTEM": "1"}.items():
        assert env[k] == v


def test_base_argv():
    argv = git_argv("status")
    assert argv[:2] == ["git", "--no-pager"] and argv[-1] == "status"
    got = overrides(argv)
    for kv in ("core.fsmonitor=false", "core.hooksPath=/dev/null", "core.pager=cat", "core.sshCommand=false",
               "credential.helper=", "protocol.allow=never", "diff.external=", "core.attributesFile=/dev/null",
               "log.showSignature=false"):
        assert kv in got
    assert got == list(BASE_OVERRIDES)


def test_driver_overrides_cover_each_kind():
    names = ["core.bare", "filter.lfs.clean", "filter.lfs.smudge", "filter.lfs.process", "filter.lfs.required",
             "diff.tc.textconv", "diff.cmd.command", "diff.renames", "merge.ours.driver", "merge.ours.name",
             "filter.My.Dotted.clean"]
    assert driver_overrides(names) == [
        "filter.lfs.clean=", "filter.lfs.smudge=", "filter.lfs.process=", "diff.tc.textconv=",
        "diff.cmd.command=", "merge.ours.driver=", "filter.My.Dotted.clean=",
        "filter.lfs.required=false", "filter.My.Dotted.required=false",
    ]


@pytest.mark.parametrize("name", ["filter.a=b.clean", "diff.x\ny.textconv"])
def test_unoverridable_key_fails_closed(name):
    with pytest.raises(GitConfigError):
        driver_overrides([name])


async def test_repo_overrides_from_fixture_config(repo_dir):
    for key, value in {"filter.evil.clean": "touch /x", "filter.evil.smudge": "touch /x",
                       "filter.proc.process": "touch /x", "diff.tc.textconv": "touch /x",
                       "diff.cmd.command": "touch /x", "merge.m.driver": "touch /x"}.items():
        git(repo_dir, "config", key, value)
    got = await repo_overrides(DirectBackend(repo_dir), str(repo_dir))
    assert set(got) == {"filter.evil.clean=", "filter.evil.smudge=", "filter.proc.process=", "diff.tc.textconv=",
                        "diff.cmd.command=", "merge.m.driver=", "filter.evil.required=false",
                        "filter.proc.required=false"}
    argv = git_argv("diff", extra_overrides=got)
    assert overrides(argv)[-len(got):] == got  # after the base overrides; -c values beat repo config


async def test_repo_overrides_include_worktree_config(repo_dir, tmp_path):
    git(repo_dir, "commit", "-q", "--allow-empty", "-m", "init")
    wt = tmp_path / "wt"
    git(repo_dir, "worktree", "add", "-q", str(wt))
    git(repo_dir, "config", "extensions.worktreeConfig", "true")
    git(wt, "config", "--worktree", "filter.wt.clean", "touch /x")
    got = await repo_overrides(DirectBackend(wt), str(wt))
    assert "filter.wt.clean=" in got


async def test_repo_overrides_include_included_file(repo_dir, tmp_path):
    inc = tmp_path / "inc.cfg"
    inc.write_text("[diff \"inc\"]\n\ttextconv = touch /x\n")
    git(repo_dir, "config", "include.path", str(inc))
    assert "diff.inc.textconv=" in await repo_overrides(DirectBackend(repo_dir), str(repo_dir))


async def test_broken_config_fails_closed(repo_dir):
    (repo_dir / ".git" / "config").write_text("[core\n")
    with pytest.raises(GitConfigError):
        await repo_overrides(DirectBackend(repo_dir), str(repo_dir))
