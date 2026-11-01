import io
import json
import subprocess

import pytest

from devkit.cli import run_hook
from devkit.review import state

SID = "0f5c7c1e-8d1a-4c2b-9a51-3b7f2d9e6a10"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


@pytest.fixture
def opted_in(repo, user_config):
    user_config("rules:\n  review: block\n")
    (repo / ".devkit.yaml").write_text("")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("a = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


# Recorded from Claude Code's hook input, with the paths and ids replaced.
def session_start(cwd, source="startup"):
    return {"session_id": SID, "transcript_path": f"/home/u/.claude/projects/x/{SID}.jsonl", "cwd": str(cwd),
            "hook_event_name": "SessionStart", "source": source}


def prompt(cwd, text="what does app.py do?"):
    return {"session_id": SID, "transcript_path": f"/home/u/.claude/projects/x/{SID}.jsonl", "cwd": str(cwd),
            "permission_mode": "default", "hook_event_name": "UserPromptSubmit", "prompt": text}


def stop_payload(cwd, *, permission_mode="default", background_tasks=(), active=False):
    return {"session_id": SID, "transcript_path": f"/home/u/.claude/projects/x/{SID}.jsonl", "cwd": str(cwd),
            "permission_mode": permission_mode, "hook_event_name": "Stop", "stop_hook_active": active,
            "last_assistant_message": "Done.", "background_tasks": list(background_tasks)}


def hook(event, payload):
    out, err = io.StringIO(), io.StringIO()
    assert run_hook("claude", event, io.StringIO(json.dumps(payload)), out, err) == 0
    return (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def start_turn(repo, source="startup"):
    assert hook("session-start", session_start(repo, source)) == (None, "")
    assert hook("user-prompt-submit", prompt(repo)) == (None, "")


def stop(repo, **kw):
    return hook("stop", stop_payload(repo, **kw))


def loop(repo):
    with state.locked(repo) as data:
        return (data["sessions"].get(SID) or {}).get("loop")


def set_record(repo, **rec):
    with state.locked(repo) as data:
        data["sessions"][SID]["loop"]["record"] = rec


def test_question_only_turn_prints_nothing(opted_in):
    start_turn(opted_in)
    assert stop(opted_in) == (None, "")


def test_edit_turn_blocks_with_round_1(opted_in):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    resp, err = stop(opted_in)
    assert err == "" and resp["decision"] == "block" and "systemMessage" not in resp
    assert resp["reason"].startswith("[devkit review] ") and "round 1 of 3" in resp["reason"]
    assert f"devkit review plan --session {SID}" in resp["reason"]
    assert loop(opted_in)["round"] == 1


def test_full_loop_through_the_hooks(opted_in):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    assert stop(opted_in)[0]["decision"] == "block"
    (opted_in / "src" / "app.py").write_text("a = 3\n")  # round 1's fix
    set_record(opted_in, fixed=1, open=1, dropped=0)
    resp, _ = stop(opted_in, active=True)
    assert "round 2 of 3" in resp["reason"]
    set_record(opted_in, fixed=0, open=0, dropped=0)
    assert stop(opted_in, active=True) == ({"systemMessage": "[devkit review] review: 2 rounds, 1 fixed, 1 to discuss"},
                                           "")
    assert loop(opted_in) is None
    assert hook("user-prompt-submit", prompt(opted_in))[0] is None
    assert stop(opted_in) == (None, "")


def test_round_not_completed_is_a_notice(opted_in):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    stop(opted_in)
    resp, _ = stop(opted_in, active=True)
    assert resp == {"systemMessage": "[devkit review] review round 1 of 3 was not completed"}


def test_pre_existing_changes_are_not_the_trigger(opted_in):
    (opted_in / "src" / "app.py").write_text("dirty = 1\n")
    start_turn(opted_in)
    assert stop(opted_in) == (None, "")


@pytest.mark.parametrize("source", ["resume", "compact"])
def test_restart_keeps_the_baseline(opted_in, source):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")  # changed, then the session was closed before stopping
    start_turn(opted_in, source=source)
    (opted_in / "src" / "new.py").write_text("b = 1\n")
    assert stop(opted_in)[0]["decision"] == "block"
    with state.locked(opted_in) as data:
        s = data["sessions"][SID]
    assert s["loop"]["from"] == s["baseline"] != s["turn_start"]  # round 1 covers both edits


@pytest.mark.parametrize("kw", [{"permission_mode": "plan"}, {"background_tasks": [{"id": "b1", "status": "running"}]}])
def test_never_blocked(opted_in, kw, monkeypatch):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    from devkit.review import snapshot

    monkeypatch.setattr(snapshot, "snapshot", lambda *a: pytest.fail("snapshot taken"))
    assert stop(opted_in, **kw) == (None, "")


def test_warn_prints_no_block(opted_in, user_config):
    user_config("rules:\n  review: warn\n")
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    resp, _ = stop(opted_in)
    assert list(resp) == ["systemMessage"] and "unreviewed changes" in resp["systemMessage"]
    assert stop(opted_in) == (None, "")


def test_snapshot_failure_exits_0_with_a_warning(opted_in):
    start_turn(opted_in)
    nested = opted_in / "nested"  # a git repo with no commit yet: `git add -A` refuses it
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / "f").write_text("x")
    resp, err = stop(opted_in)
    assert resp is None and err.startswith("devkit: ") and err.count("\n") == 1 and "allowing" in err


def test_untracked_env_file_does_not_trigger(opted_in):
    start_turn(opted_in)
    (opted_in / ".env.local").write_text("API_KEY=sekrit\n")
    assert stop(opted_in) == (None, "")


def test_pruned_tree_resets_the_session(opted_in):
    start_turn(opted_in)
    with state.locked(opted_in) as data:
        data["sessions"][SID]["baseline"] = data["sessions"][SID]["turn_start"] = "0" * 40
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    assert stop(opted_in) == (None, "")
    with state.locked(opted_in) as data:
        s = data["sessions"][SID]
        assert s["baseline"] == s["turn_start"] != "0" * 40 and s["loop"] is None


def test_invalid_state_file_warns_and_continues(opted_in, capsys):
    start_turn(opted_in)
    state.path_for(opted_in).write_text("{broken")
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    assert stop(opted_in) == (None, "")  # a fresh session starts at the current tree
    err = capsys.readouterr().err
    assert err.startswith("devkit: review state") and "starting from empty" in err and err.count("\n") == 1


def test_not_in_a_git_repo(tmp_path, user_config):
    user_config("repos: all\nrules:\n  review: block\n")
    d = tmp_path / "plain"
    d.mkdir()
    for event, payload in (("session-start", session_start(d)), ("user-prompt-submit", prompt(d)),
                           ("stop", stop_payload(d))):
        assert hook(event, payload) == (None, "")


def test_not_opted_in_runs_no_review(repo, user_config):
    user_config("rules:\n  review: block\n")
    (repo / "app.py").write_text("x = 1\n")
    assert hook("user-prompt-submit", prompt(repo)) == (None, "")
    (repo / "app.py").write_text("x = 2\n")
    assert stop(repo) == (None, "")
    assert not state.path_for(repo).exists()


def test_unused_pruned_baseline_does_not_restart_the_session(opted_in):
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    stop(opted_in)
    set_record(opted_in, fixed=0, open=0, dropped=0)
    stop(opted_in)  # the loop ends: last_reviewed is the current tree
    with state.locked(opted_in) as data:
        data["sessions"][SID]["baseline"] = "0" * 40
    hook("user-prompt-submit", prompt(opted_in))
    (opted_in / "src" / "app.py").write_text("a = 3\n")
    assert stop(opted_in)[0]["decision"] == "block"


def test_lock_held_too_long_fails_open(opted_in, monkeypatch):
    from devkit import gitsafe

    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    monkeypatch.setattr(gitsafe, "TIMEOUT_S", 3.0)  # the hook's one budget, read when the hook runs
    with state.locked(opted_in):  # another session holds the state
        resp, err = stop(opted_in)
    assert resp is None and "LockTimeout" in err and err.count("\n") == 1
