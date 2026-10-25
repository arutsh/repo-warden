import pytest

from devkit import config
from devkit.engine import evaluate
from devkit.events import Event
from devkit.rules.branch_name import RULE, created_branches

SETTINGS = {"pattern": r"^(feat|fix)/.+$", "base": "main"}


def check(command, **settings):
    return RULE.check(Event("shell", command=command), {**SETTINGS, **settings})


@pytest.mark.parametrize("command", ["git switch -c tmp", "git checkout -b tmp", "git branch tmp",
                                     "git fetch && git switch -C 'tmp'", "git checkout -b -- tmp"])
def test_non_conforming_branch_flagged(command):
    (f,) = check(command)
    assert "'tmp'" in f.message and r"'^(feat|fix)/.+$'" in f.message


@pytest.mark.parametrize("command", ["git switch -c feat/login", "git checkout -b fix/crash",
                                     "git switch -c main", "git switch main", "git checkout tmp"])
def test_conforming_or_not_creation(command):
    assert check(command) == []


@pytest.mark.parametrize("command", ["git branch -d old", "git branch -D old", "git branch --list",
                                     "git branch | cat", "git branch"])
def test_flags_and_listing_ignored(command):
    assert created_branches(command) == []


def test_configured_base_accepted():
    assert check("git switch -c develop", base="develop") == []
    assert len(check("git switch -c develop")) == 1


def test_every_creation_judged():
    assert len(check("git switch -c a && git switch -c b && git switch -c feat/c")) == 2


def test_unrelated_event_allowed():
    assert RULE.kinds == {"shell"}


def test_off_by_default_and_on_via_config(repo, user_config):
    (repo / ".devkit.yaml").write_text("")
    ev = [Event("shell", command="git switch -c tmp", cwd=str(repo))]
    assert evaluate(ev).findings == []
    (repo / ".devkit.yaml").write_text("rules:\n  branch_name: { level: block, pattern: '^feat/.+$' }\n")
    out = evaluate(ev)
    assert out.blocked and "'^feat/.+$'" in out.blocks[0].message


def test_invalid_pattern_is_a_config_error(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  branch_name: { level: block, pattern: '(' }\n")
    with pytest.raises(config.ConfigError, match=r"rules\.branch_name\.pattern: invalid regular expression"):
        config.load(repo, strict=True)
    # A hook skips the broken layer and keeps enforcing the guard rule.
    out = evaluate([Event("shell", command="cat .env", cwd=str(repo))])
    assert out.blocked and ".devkit.yaml" in out.notes[0]


def test_invalid_max_lines_is_a_config_error(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: { max_lines: 0 }\n")
    with pytest.raises(config.ConfigError, match=r"rules\.comment_brevity\.max_lines: must be at least 1"):
        config.load(repo, strict=True)


@pytest.mark.parametrize("command", ["git branch\necho done", "git branch 2>/dev/null", "git branch >branches.txt",
                                     "git branch 2>&1 | cat"])
def test_listing_with_newline_or_redirection_is_not_a_creation(command):
    assert created_branches(command) == []
