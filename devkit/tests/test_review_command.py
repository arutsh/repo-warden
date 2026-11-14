import io
import json
import re
import subprocess

import pytest

from devkit.cli import main
from devkit.review import state
from .test_review_hooks import SID, loop, opted_in, set_record, start_turn, stop  # noqa: F401

SECRET_NAME = ".en" + "v.local"  # matches the secrets rule's patterns, so snapshots and diffs must leave it out


@pytest.fixture
def in_repo(opted_in, monkeypatch):
    monkeypatch.chdir(opted_in)
    return opted_in


def run(capsys, monkeypatch, *argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["review", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def edit(repo, text):
    (repo / "src" / "app.py").write_text(text)


def blocked_round_1(repo):
    start_turn(repo)
    edit(repo, "a = 2\n")
    assert stop(repo)[0]["decision"] == "block"


def finding(outcome="fixed", **kw):
    return {"focus": "correctness", "model": "opus", "file": "src/app.py", "line": 1, "claim": "c", "score": 80,
            "outcome": outcome, **kw}


def test_plan_round_1_lists_every_focus(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    code, out, _ = run(capsys, monkeypatch, "plan")
    plan = json.loads(out)
    assert code == 0 and plan["round"] == 1 and plan["max"] == 3 and plan["session"] == SID
    assert plan["reviewers"] == {"correctness": "opus", "rules": "sonnet", "tests": "sonnet", "security": "sonnet",
                                 "history": "sonnet"}
    assert plan["checker"] == "opus" and plan["scope"] == f"devkit review diff --session {SID}"


def test_plan_round_2_lists_only_followup_reviewers(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    edit(in_repo, "a = 3\n")
    set_record(in_repo, fixed=1, open=0, dropped=0)
    assert "round 2" in stop(in_repo)[0]["reason"]
    plan = json.loads(run(capsys, monkeypatch, "plan")[1])
    assert plan["round"] == 2 and plan["reviewers"] == {"correctness": "opus"}


def test_followup_focus_missing_from_reviewers_uses_its_default_model(in_repo, capsys, monkeypatch):
    (in_repo / ".devkit.yaml").write_text(
        "rules:\n  review:\n    reviewers: {correctness: haiku}\n    followup_reviewers: [correctness, tests]\n")
    blocked_round_1(in_repo)
    assert json.loads(run(capsys, monkeypatch, "plan")[1])["reviewers"] == {"correctness": "haiku"}
    edit(in_repo, "a = 3\n")
    set_record(in_repo, fixed=1, open=0, dropped=0)
    stop(in_repo)
    assert json.loads(run(capsys, monkeypatch, "plan")[1])["reviewers"] == {"correctness": "haiku", "tests": "sonnet"}


def test_diff_for_round_2_shows_only_the_round_1_fixes(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    code, out, _ = run(capsys, monkeypatch, "diff")
    assert code == 0 and "+a = 2" in out and "-a = 1" in out
    edit(in_repo, "a = 3\n")
    set_record(in_repo, fixed=1, open=0, dropped=0)
    stop(in_repo)
    out = run(capsys, monkeypatch, "diff")[1]
    assert "-a = 2" in out and "+a = 3" in out and "-a = 1" not in out


def test_diff_leaves_out_secret_paths(in_repo, capsys, monkeypatch):
    start_turn(in_repo)
    edit(in_repo, "a = 2\n")
    (in_repo / SECRET_NAME).write_text("TOKEN=hunter2\n")
    stop(in_repo)
    out = run(capsys, monkeypatch, "diff")[1]
    assert "+a = 2" in out and "hunter2" not in out and SECRET_NAME not in out


def test_skip_is_visible_at_the_next_stop(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    assert run(capsys, monkeypatch, "skip", "--reason", "user asked")[0] == 0
    assert stop(in_repo, active=True) == ({"systemMessage": "[devkit review] review skipped: user asked"}, "")
    assert loop(in_repo) is None


@pytest.mark.parametrize("argv", [["plan"], ["diff"], ["skip", "--reason", "x"], ["record"]])
def test_no_active_loop_is_an_error(in_repo, capsys, monkeypatch, argv):
    start_turn(in_repo)
    code, out, err = run(capsys, monkeypatch, *argv, stdin="[]")
    assert code == 1 and out == "" and "no review loop is active" in err
    assert loop(in_repo) is None


def test_two_active_loops_need_a_session(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    with state.locked(in_repo) as data:
        data["sessions"]["other"] = {**data["sessions"][SID]}
    code, _, err = run(capsys, monkeypatch, "plan")
    assert code == 1 and "--session" in err and SID in err
    assert json.loads(run(capsys, monkeypatch, "plan", "--session", "other")[1])["session"] == "other"
    assert run(capsys, monkeypatch, "plan", "--session", "nope")[0] == 1


def test_plan_start_begins_an_on_demand_loop(in_repo, capsys, monkeypatch):
    start_turn(in_repo)
    edit(in_repo, "a = 2\n")
    code, out, _ = run(capsys, monkeypatch, "plan", "--start")
    assert code == 0 and json.loads(out)["round"] == 1 and loop(in_repo)["round"] == 1
    assert run(capsys, monkeypatch, "plan", "--start")[0] == 0  # already running: the same loop
    assert "+a = 2" in run(capsys, monkeypatch, "diff")[1]


def test_plan_start_with_nothing_to_review(in_repo, capsys, monkeypatch):
    start_turn(in_repo)
    code, _, err = run(capsys, monkeypatch, "plan", "--start")
    assert code == 1 and "nothing to review" in err


def test_plan_start_refuses_when_no_session_is_known(in_repo, capsys, monkeypatch):
    edit(in_repo, "a = 2\n")
    code, out, err = run(capsys, monkeypatch, "plan", "--start")
    assert code == 1 and out == "" and "no agent session is known" in err
    assert not state.path_for(in_repo).exists()


def test_plan_start_for_a_named_new_session_reviews_changes_since_head(in_repo, capsys, monkeypatch):
    edit(in_repo, "a = 2\n")
    code, out, _ = run(capsys, monkeypatch, "plan", "--start", "--session", "s-new")
    assert code == 0 and json.loads(out)["session"] == "s-new"
    assert "+a = 2" in run(capsys, monkeypatch, "diff")[1]


def test_plan_start_in_a_repo_with_no_commits(tmp_path, user_config, capsys, monkeypatch):
    user_config("rules:\n  review: block\n")
    d = tmp_path / "fresh"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    (d / ".devkit.yaml").write_text("")
    (d / "x.py").write_text("x = 1\n")
    monkeypatch.chdir(d)
    code, out, _ = run(capsys, monkeypatch, "plan", "--start", "--session", "s1")
    assert code == 0 and json.loads(out)["round"] == 1
    assert "+x = 1" in run(capsys, monkeypatch, "diff")[1]


def test_plan_start_snapshots_before_taking_the_lock(in_repo, capsys, monkeypatch):
    start_turn(in_repo)
    edit(in_repo, "a = 2\n")
    from devkit.review import snapshot

    real = snapshot.snapshot

    def spy(*a, **kw):
        with state.locked(in_repo, timeout_s=0):  # raises LockTimeout if the command already holds the lock
            return real(*a, **kw)

    monkeypatch.setattr(snapshot, "snapshot", spy)
    assert run(capsys, monkeypatch, "plan", "--start")[0] == 0


def test_a_recorded_round_is_not_overwritten(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    assert run(capsys, monkeypatch, "skip", "--reason", "user asked")[0] == 0
    code, _, err = run(capsys, monkeypatch, "record", stdin="[]")
    assert code == 1 and "already recorded" in err
    assert loop(in_repo)["record"] == {"skipped": "user asked"}
    assert run(capsys, monkeypatch, "skip", "--reason", "again")[0] == 1


def test_record_then_skip_is_refused(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    run(capsys, monkeypatch, "record", stdin="[]")
    assert run(capsys, monkeypatch, "skip", "--reason", "x")[0] == 1
    assert loop(in_repo)["record"] == {"fixed": 0, "open": 0, "dropped": 0}


def test_reset_clears_state(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    assert run(capsys, monkeypatch, "reset") == (0, "review state cleared\n", "")
    assert run(capsys, monkeypatch, "reset") == (0, "no review state to clear\n", "")
    assert run(capsys, monkeypatch, "plan")[0] == 1


def test_record_derives_counts(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    findings = [finding("fixed"), finding("discuss"),
                finding("dropped", reason="no evidence", file=None, line=None, score=None)]
    code, out, _ = run(capsys, monkeypatch, "record", stdin=json.dumps(findings))
    lines = out.splitlines()
    assert code == 0 and lines[0] == "recorded: 1 fixed, 1 open, 1 dropped"
    assert re.fullmatch(r"open finding ids: f-[0-9a-f]{8}", lines[1]) and len(lines) == 2
    assert loop(in_repo)["record"] == {"fixed": 1, "open": 1, "dropped": 1}


def test_record_reads_the_findings_file(in_repo, capsys, monkeypatch, tmp_path):
    blocked_round_1(in_repo)
    path = tmp_path / "round1.json"
    path.write_text(json.dumps([finding("fixed"), finding("discuss")]), encoding="utf-8")
    code, out, _ = run(capsys, monkeypatch, "record", "--file", str(path), stdin="not json")
    assert code == 0 and out.startswith("recorded: 1 fixed, 1 open, 0 dropped\n")
    assert loop(in_repo)["record"] == {"fixed": 1, "open": 1, "dropped": 0}


def test_record_with_a_missing_file_records_nothing(in_repo, capsys, monkeypatch, tmp_path):
    blocked_round_1(in_repo)
    code, out, err = run(capsys, monkeypatch, "record", "--file", str(tmp_path / "nope.json"), stdin="[]")
    assert code == 1 and out == "" and "nope.json" in err
    assert loop(in_repo)["record"] is None


def test_record_counts_questions_as_open(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    run(capsys, monkeypatch, "record", stdin=json.dumps([finding("question", score=50)]))
    assert loop(in_repo)["record"] == {"fixed": 0, "open": 1, "dropped": 0}


@pytest.mark.parametrize("stdin", [
    "not json", "{}", '[{"outcome": "fixed"}]', json.dumps([finding("maybe")]), json.dumps([finding(score=101)]),
    json.dumps([finding(focus="style")]), json.dumps([finding("dropped")]), json.dumps([finding(line=0)]),
    json.dumps([finding(), "x"]), "[" * 100000])
def test_invalid_input_records_nothing(in_repo, capsys, monkeypatch, stdin):
    blocked_round_1(in_repo)
    code, out, err = run(capsys, monkeypatch, "record", stdin=stdin)
    assert code == 1 and out == "" and err.startswith("devkit: ")
    assert loop(in_repo)["record"] is None


def test_recorded_round_drives_the_next_stop(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    run(capsys, monkeypatch, "record", stdin="[]")
    resp, _ = stop(in_repo, active=True)
    assert resp == {"systemMessage": "[devkit review] review: 1 round, 0 fixed, 0 to discuss"}


def test_outside_a_repo(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run(capsys, monkeypatch, "plan")[0] == 2


def test_status_shows_an_active_loop(in_repo, capsys):
    blocked_round_1(in_repo)
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "loop active: round 1 of 3" in out and "last review completed: never" in out


def test_status_shows_the_last_completed_review(in_repo, capsys):
    blocked_round_1(in_repo)
    set_record(in_repo, fixed=0, open=0, dropped=0)
    stop(in_repo, active=True)
    main(["status"])
    out = capsys.readouterr().out
    assert "loop active: no" in out and "last review completed: 20" in out


def test_status_without_review_state_prints_no_review_section(in_repo, capsys):
    main(["status"])
    assert "\nreview:\n" not in capsys.readouterr().out


def test_last_review_outlives_its_session(in_repo, capsys):
    blocked_round_1(in_repo)
    set_record(in_repo, fixed=0, open=0, dropped=0)
    stop(in_repo, active=True)
    with state.locked(in_repo) as data:
        data["sessions"].clear()  # pruned, or rebuilt after git gc
    main(["status"])
    out = capsys.readouterr().out
    assert "last review completed: 20" in out and f"(session {SID})" in out
