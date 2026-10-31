import subprocess

import pytest

from devkit.cli import main
from devkit.config import ConfigError, load
from devkit.engine import evaluate
from devkit.events import Event, Level
from devkit.rules import review


@pytest.fixture
def checks(monkeypatch):
    """The events the review rule's check was called with."""
    seen = []
    monkeypatch.setattr(review.Review, "check", lambda self, event, settings: seen.append(event) or [])
    return seen


def stop(repo):
    return Event("turn", "stop", cwd=str(repo), session_id="s1", meta={"permission_mode": "default"})


def test_defaults(repo):
    rc = load(repo, strict=True).rules["review"]
    assert rc.level is Level.OFF
    assert rc.settings["max_rounds"] == 3 and rc.settings["followup_reviewers"] == ["correctness"]
    assert rc.settings["reviewers"]["correctness"] == "opus" and rc.settings["checker"] == "opus"


def test_off_by_default_runs_no_git(repo, checks, monkeypatch):
    (repo / ".devkit.yaml").write_text("")
    (repo / "app.py").write_text("x = 1\n")

    def no_git(*a, **k):
        raise AssertionError("git subprocess started")

    monkeypatch.setattr(subprocess, "Popen", no_git)
    out = evaluate([Event("turn", phase, cwd=str(repo), session_id="s1") for phase in ("session-start", "prompt")]
                   + [stop(repo)])
    assert not out.findings and checks == []


def test_not_opted_in(repo, user_config, checks):
    user_config("rules:\n  review: block\n")
    assert not evaluate([stop(repo)]).findings and checks == []


def test_opted_in_runs_check(repo, user_config, checks):
    user_config("rules:\n  review: block\n")
    (repo / ".devkit.yaml").write_text("")
    evaluate([stop(repo)])
    assert [e.phase for e in checks] == ["stop"]


def test_invalid_model_fails_status(repo, monkeypatch, capsys):
    (repo / ".devkit.yaml").write_text("rules:\n  review: { level: block, checker: gpt }\n")
    monkeypatch.chdir(repo)
    assert main(["status"]) != 0
    err = capsys.readouterr().err
    assert ".devkit.yaml" in err and "rules.review.checker" in err


@pytest.mark.parametrize("setting,key", [
    ("max_rounds: 0", "rules.review.max_rounds"),
    ("max_rounds: 6", "rules.review.max_rounds"),
    ("max_rounds: true", "rules.review.max_rounds"),
    ("reviewers: { style: opus }", "rules.review.reviewers"),
    ("reviewers: { correctness: gpt }", "rules.review.reviewers"),
    ("reviewers: {}", "rules.review.reviewers"),
    ("followup_reviewers: [style]", "rules.review.followup_reviewers"),
    ("followup_reviewers: []", "rules.review.followup_reviewers"),
    ("log_retention_days: 0", "rules.review.log_retention_days"),
    ("external: [codex]", "rules.review.external"),
])
def test_invalid_settings(repo, setting, key):
    (repo / ".devkit.yaml").write_text(f"rules:\n  review: {{ level: block, {setting} }}\n")
    with pytest.raises(ConfigError) as e:
        load(repo, strict=True)
    assert e.value.key == key and e.value.path == repo / ".devkit.yaml"


def test_repo_sets_review_settings(repo):
    (repo / ".devkit.yaml").write_text(
        "rules:\n  review: { level: block, max_rounds: 5, reviewers: { correctness: inherit }, skip_paths: ['*.md'] }\n")
    rc = load(repo, strict=True).rules["review"]
    assert rc.level is Level.BLOCK and rc.settings["max_rounds"] == 5
    assert rc.settings["reviewers"] == {"correctness": "inherit"} and rc.settings["skip_paths"] == ["*.md"]


def test_repo_cannot_turn_on_log_content(repo, monkeypatch, capsys):
    (repo / ".devkit.yaml").write_text("rules:\n  review: { level: block, log_content: true }\n")
    cfg = load(repo, strict=True)
    rc = cfg.rules["review"]
    assert rc.settings["log_content"] is False and rc.sources["log_content"] == "default"
    assert rc.level is Level.BLOCK
    assert any(i.startswith("rules.review.log_content") for i in cfg.ignored)
    monkeypatch.chdir(repo)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "ignored settings:" in out and "rules.review.log_content (repo: .devkit.yaml" in out


def test_user_config_turns_on_log_content(repo, user_config):
    user_config("rules:\n  review: { log_content: true }\n")
    cfg = load(repo, strict=True)
    assert cfg.rules["review"].settings["log_content"] is True and not cfg.ignored
    assert cfg.rules["review"].sources["log_content"].startswith("user: ")
