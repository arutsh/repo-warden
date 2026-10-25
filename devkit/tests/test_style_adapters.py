"""Style rules give the same findings whichever agent the event came from."""

import io
import json

from devkit.adapters import claude, codex
from devkit.cli import run_hook
from devkit.engine import evaluate

COMMENT = ["# one", "# two", "# three", "# four"]


def hook(agent, stdin, event):
    out, err = io.StringIO(), io.StringIO()
    assert run_hook(agent, event, io.StringIO(stdin), out, err) == 0 and err.getvalue() == ""
    return json.loads(out.getvalue()) if out.getvalue() else None


def claude_payload(cwd, tool, event="PreToolUse", **ti):
    return json.dumps({"hook_event_name": event, "tool_name": tool, "tool_input": ti, "cwd": cwd})


def codex_payload(cwd, patch, event="PreToolUse"):
    return json.dumps({"hook_event_name": event, "tool_name": "apply_patch", "cwd": cwd,
                       "tool_input": {"command": patch}})


def envelope(*lines):
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"]) + "\n"


def test_same_comment_finding_from_claude_edit_and_codex_patch(repo):
    (repo / ".devkit.yaml").write_text("")
    (repo / "app.py").write_text("x = 1\ny = 2\n")
    c = claude_payload(str(repo), "Edit", file_path="app.py", old_string="y = 2", new_string="\n".join([*COMMENT, "y = 2"]))
    x = codex_payload(str(repo), envelope("*** Update File: app.py", "@@", " x = 1", *(f"+{l}" for l in COMMENT), " y = 2"))

    from_claude = evaluate(claude.events(json.loads(c), "pre")).findings
    from_codex = evaluate(codex.events(json.loads(x), "pre")).findings
    assert from_claude == from_codex and len(from_claude) == 1
    level, finding = from_claude[0]
    assert finding.rule == "comment_brevity" and finding.line == 2 and finding.path == str(repo / "app.py")

    reasons = [hook(a, p, "pre-tool-use")["hookSpecificOutput"] for a, p in (("claude", c), ("codex", x))]
    assert all(r["permissionDecision"] == "deny" for r in reasons)
    assert reasons[0]["permissionDecisionReason"] == reasons[1]["permissionDecisionReason"]


def test_same_comment_finding_from_new_file(repo):
    (repo / ".devkit.yaml").write_text("")
    body = "\n".join([*COMMENT, "x = 1"]) + "\n"
    c = claude_payload(str(repo), "Write", file_path=str(repo / "new.py"), content=body)
    x = codex_payload(str(repo), envelope("*** Add File: new.py", *(f"+{l}" for l in body.splitlines())))
    assert evaluate(claude.events(json.loads(c), "pre")).findings == evaluate(codex.events(json.loads(x), "pre")).findings
    assert hook("codex", x, "pre-tool-use")["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_test_factories_warns_from_post_tool_use_in_both(repo, user_config):
    user_config("rules:\n  test_factories: { level: warn, discouraged_calls: [make_valid_user] }\n")
    (repo / ".devkit.yaml").write_text("")
    (repo / "tests").mkdir()
    test_file = repo / "tests" / "test_users.py"
    test_file.write_text("def test_x():\n    u = make_valid_user()\n")  # the tool has already run
    c = claude_payload(str(repo), "Edit", "PostToolUse", file_path=str(test_file), old_string="x", new_string="y")
    x = codex_payload(str(repo), envelope("*** Update File: tests/test_users.py", "@@", "-a", "+b"), "PostToolUse")

    responses = [hook("claude", c, "post-tool-use"), hook("codex", x, "post-tool-use")]
    for resp in responses:
        hso = resp["hookSpecificOutput"]
        assert hso["hookEventName"] == "PostToolUse" and "permissionDecision" not in hso and "decision" not in resp
        assert "make_valid_user(...)" in hso["additionalContext"] and "make_valid_user" in resp["systemMessage"]
    assert responses[0] == responses[1]
    # Not at PreToolUse.
    assert hook("claude", claude_payload(str(repo), "Write", file_path=str(test_file), content="make_valid_user()\n"),
                "pre-tool-use") is None


# --- content is loaded only when a rule enabled for the repo reads it (effective config, D5) ---

def _edit(repo):
    (repo / "app.py").write_text("x = 1\n")
    return {"tool_name": "Edit", "cwd": str(repo),
            "tool_input": {"file_path": "app.py", "old_string": "x = 1", "new_string": "x = 2"}}


def test_content_not_read_in_a_repo_that_is_not_opted_in(repo):
    from devkit.adapters import claude

    [ev] = claude.events(_edit(repo), "pre")
    assert (ev.before, ev.after) == (None, None)


def test_content_read_when_comment_brevity_is_on(repo):
    from devkit.adapters import claude

    (repo / ".devkit.yaml").write_text("")
    [ev] = claude.events(_edit(repo), "pre")
    assert (ev.before, ev.after) == ("x = 1\n", "x = 2\n")


def test_content_not_read_when_the_content_rules_are_off(repo):
    from devkit.adapters import claude, codex

    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: off\n")
    [ev] = claude.events(_edit(repo), "pre")
    assert (ev.before, ev.after) == (None, None)
    patch = "*** Begin Patch\n*** Update File: app.py\n@@\n-x = 1\n+x = 2\n*** End Patch\n"
    [ev] = codex.events({"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": patch}}, "pre")
    assert (ev.before, ev.after) == (None, None)


def test_one_hook_call_loads_the_config_once(repo, monkeypatch):
    from devkit import config

    (repo / ".devkit.yaml").write_text("")
    loads = []
    real = config.load
    monkeypatch.setattr(config, "load", lambda *a, **kw: loads.append(a) or real(*a, **kw))
    x = codex_payload(str(repo), envelope(*(f"*** Add File: f{i}.py\n+x = {i}" for i in range(3))))
    assert hook("codex", x, "pre-tool-use") is None
    assert len(loads) == 1
    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: off\n")  # the next call sees the change
    assert hook("codex", x, "pre-tool-use") is None and len(loads) == 2


# --- an edit that can't be placed is judged as a fragment: no line number ---

def test_unplaced_edit_reports_no_line_number(repo):
    from devkit.adapters import claude
    from devkit.rules.comment_brevity import RULE

    (repo / "app.py").write_text("x = 1\n")
    payload = {"tool_name": "Edit", "cwd": str(repo), "tool_input": {
        "file_path": "app.py", "old_string": "not there", "new_string": "y = 2\n# a\n# b\n# c\n# d\n"}}
    [ev] = claude.events(payload, "pre", content=True)
    assert ev.fragment
    (f,) = RULE.check(ev, {"max_lines": 2})
    assert f.line is None and "at line" not in f.message and "'# a'" in f.message


def test_stale_codex_hunk_is_a_fragment(repo):
    from devkit.adapters import codex

    (repo / "app.py").write_text("changed\n")
    patch = "*** Begin Patch\n*** Update File: app.py\n@@\n-old\n+new\n*** End Patch\n"
    [ev] = codex.events({"tool_name": "apply_patch", "cwd": str(repo), "tool_input": {"command": patch}}, "pre",
                        content=True)
    assert ev.fragment
