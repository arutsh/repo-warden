import io
import json

import pytest

from devkit import rules
from devkit.adapters import claude
from devkit.cli import run_hook
from devkit.events import Finding, Level

from .conftest import payload


def hook(stdin: str, event="pre-tool-use"):
    out, err = io.StringIO(), io.StringIO()
    code = run_hook("claude", event, io.StringIO(stdin), out, err)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def test_bash_secret_denied(repo):
    code, resp, _ = hook(payload("Bash", str(repo), command="cat .env"))
    assert code == 0
    hso = resp["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and hso["hookEventName"] == "PreToolUse"
    assert "'.env'" in hso["permissionDecisionReason"] and "secrets" in hso["permissionDecisionReason"]


def test_allowed_command_prints_nothing(repo):
    assert hook(payload("Bash", str(repo), command="ls src")) == (0, None, "")


@pytest.mark.parametrize("tool,ti", [
    ("Read", {"file_path": ".env.local"}),
    ("Grep", {"pattern": "KEY", "glob": "**/.env*"}),
    ("Grep", {"pattern": "KEY", "path": "config/.env"}),
    ("Glob", {"pattern": "**/*.pem"}),
])
def test_read_tools_denied(repo, tool, ti):
    _, resp, _ = hook(payload(tool, str(repo), **ti))
    assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_grep_regex_is_not_a_path(repo):
    assert hook(payload("Grep", str(repo), pattern=".env", path="src"))[1] is None


@pytest.mark.parametrize("stdin", ["garbage", "[1, 2]", ""])
def test_malformed_input_fails_open(stdin):
    code, resp, err = hook(stdin)
    assert code == 0 and resp is None and "allowing" in err


def test_rule_crash_fails_open(repo, stub_rules):
    rule = stub_rules("boom", guard=True)
    type(rule).check = lambda self, e, s: 1 / 0
    code, resp, err = hook(payload("Bash", str(repo), command="ls"))
    assert code == 0 and resp is None and "ZeroDivisionError" in err


def test_unknown_tool_no_opinion(repo):
    assert hook(payload("WebFetch", str(repo), url="https://x/.env"))[1] is None


def test_never_allow(repo, stub_rules, user_config):
    user_config("repos: all\n")
    stub_rules("w", findings=[Finding("w", "careful")], default=Level.WARN)
    for stdin in (payload("Bash", str(repo), command="ls"), payload("Bash", str(repo), command="cat .env")):
        _, resp, _ = hook(stdin)
        assert "allow" not in json.dumps(resp).replace("allowing", "")


def test_warning_is_context_not_decision(repo, stub_rules, user_config):
    user_config("repos: all\n")
    stub_rules("w", findings=[Finding("w", "careful")], default=Level.WARN)
    _, resp, _ = hook(payload("Bash", str(repo), command="ls"))
    assert "permissionDecision" not in resp["hookSpecificOutput"]
    assert "careful" in resp["hookSpecificOutput"]["additionalContext"] and "careful" in resp["systemMessage"]


def test_config_error_reported_but_still_enforced(repo):
    (repo / ".devkit.yaml").write_text("rules: [\n")
    _, resp, _ = hook(payload("Bash", str(repo), command="cat .env"))
    assert resp["hookSpecificOutput"]["permissionDecision"] == "deny" and ".devkit.yaml" in resp["systemMessage"]


def test_edit_judged_on_resulting_file(repo):
    f = repo / "a.py"
    f.write_text("x = 1\ny = 2\n")
    (ev,) = claude.events(json.loads(payload("Edit", str(repo), file_path=str(f), old_string="y = 2",
                                             new_string="# a\n# b\ny = 3")), "pre", content=True)
    assert ev.kind == "edit" and ev.before == "x = 1\ny = 2\n" and ev.after == "x = 1\n# a\n# b\ny = 3\n"


def test_edit_replace_all_and_multiedit(repo):
    f = repo / "a.py"
    f.write_text("a a b\n")
    (ev,) = claude.events(json.loads(payload("Edit", str(repo), file_path="a.py", old_string="a",
                                             new_string="c", replace_all=True)), "pre", content=True)
    assert ev.after == "c c b\n" and ev.path == str(repo / "a.py")
    (ev,) = claude.events(json.loads(payload("MultiEdit", str(repo), file_path=str(f), edits=[
        {"old_string": "a a", "new_string": "x"}, {"old_string": "b", "new_string": "y"}])), "pre", content=True)
    assert ev.after == "x y\n"


def test_edit_that_cannot_be_placed(repo):
    f = repo / "a.py"
    f.write_text("x\n")
    (ev,) = claude.events(json.loads(payload("Edit", str(repo), file_path=str(f), old_string="nope",
                                             new_string="new")), "pre", content=True)
    assert ev.before == "" and ev.after == "new"


def test_write_and_post_events(repo):
    f = repo / "a.py"
    f.write_text("old\n")
    (ev,) = claude.events(json.loads(payload("Write", str(repo), file_path=str(f), content="new\n")), "pre", content=True)
    assert (ev.kind, ev.before, ev.after) == ("write", "old\n", "new\n")
    (ev,) = claude.events(json.loads(payload("Write", str(repo), file_path=str(f), content="ignored")), "post", content=True)
    assert (ev.kind, ev.phase, ev.after) == ("edit", "post", "old\n")


def test_fifo_does_not_hang(repo):
    import os

    os.mkfifo(repo / "pipe.py")
    (ev,) = claude.events(json.loads(payload("Write", str(repo), file_path=str(repo / "pipe.py"), content="x")), "pre", content=True)
    assert ev.before == ""


def test_file_content_not_read_unless_a_rule_needs_it(repo, secrets_only, stub_rules):
    f = repo / "a.py"
    f.write_text("old\n")
    (ev,) = claude.events(json.loads(payload("Write", str(repo), file_path=str(f), content="new\n")), "pre")
    assert (ev.kind, ev.path, ev.before, ev.after) == ("write", str(f), None, None)
    stub_rules("c", guard=True)
    type(rules.get_rule("c")).content = True
    (ev,) = claude.events(json.loads(payload("Write", str(repo), file_path=str(f), content="new\n")), "pre")
    assert (ev.before, ev.after) == ("old\n", "new\n")


def test_unknown_hook_event_fails_open(repo):
    code, resp, err = hook(payload("Bash", str(repo), command="cat .env"), event="nope")
    assert code == 0 and resp is None and "allowing" in err


def test_post_block_uses_decision(stub_rules, repo):
    stub_rules("g", findings=[Finding("g", "bad")], guard=True, phases={"post"})
    _, resp, _ = hook(json.dumps({"tool_name": "Write", "cwd": str(repo),
                                  "tool_input": {"file_path": str(repo / "a.py"), "content": ""}}), "post-tool-use")
    assert resp["decision"] == "block" and "bad" in resp["reason"]
