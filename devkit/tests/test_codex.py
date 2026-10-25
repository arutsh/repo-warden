import io
import json

import pytest

from devkit.adapters import codex
from devkit.cli import run_hook


def payload(tool: str, cwd: str, command, event="PreToolUse") -> str:
    return json.dumps({"session_id": "s", "turn_id": "t", "hook_event_name": event, "model": "gpt-5-codex",
                       "cwd": cwd, "tool_name": tool, "tool_use_id": "call_1", "tool_input": {"command": command}})


def envelope(*lines: str) -> str:
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"]) + "\n"


def hook(stdin: str, event="pre-tool-use"):
    out, err = io.StringIO(), io.StringIO()
    code = run_hook("codex", event, io.StringIO(stdin), out, err)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None), err.getvalue()


def denied(resp) -> str:
    hso = resp["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse" and hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


def test_bash_cat_env_denied(repo):
    code, resp, _ = hook(payload("Bash", str(repo), "cat .env"))
    reason = denied(resp)
    assert code == 0 and "'.env'" in reason and "secrets" in reason


def test_bash_argv_form(repo):
    assert denied(hook(payload("Bash", str(repo), ["bash", "-lc", "cat config/.env.local"]))[1])


@pytest.mark.parametrize("patch,name", [
    (envelope("*** Add File: .env.local", "+TOKEN=x"), ".env.local"),
    (envelope("*** Update File: config/.env.local", "@@", "-A=1", "+A=2"), ".env.local"),
    (envelope("*** Update File: notes.txt", "*** Move to: .env.local", "@@", "-a", "+b"), ".env.local"),
    (envelope("*** Update File: .env.local", "*** Move to: notes.txt", "@@", "-a", "+b"), ".env.local"),
    (envelope("*** Delete File: .env.local"), ".env.local"),
    (envelope("*** Add File: ok.txt", "+hi", "*** Add File: certs/server.key", "+---"), "server.key"),
])
def test_apply_patch_touching_secret_denied(repo, patch, name):
    assert f"'{name}'" in denied(hook(payload("apply_patch", str(repo), patch))[1])


def test_allowed_patch_prints_nothing(repo):
    (repo / "app.py").write_text("x = 1\n")
    patch = envelope("*** Update File: app.py", "@@", "-x = 1", "+x = 2", "*** Add File: docs/dotenv.md", "+env docs")
    assert hook(payload("apply_patch", str(repo), patch)) == (0, None, "")


def test_malformed_patch_still_judged_for_secrets(repo):
    assert denied(hook(payload("apply_patch", str(repo), "*** Begin Patch\n*** Add File: .env\n+X=1\n"))[1])
    assert hook(payload("apply_patch", str(repo), "not a patch"))[1] is None


@pytest.mark.parametrize("stdin", ["garbage", "[1, 2]", ""])
def test_malformed_input_fails_open(stdin):
    code, resp, err = hook(stdin)
    assert code == 0 and resp is None and "allowing" in err


def test_unknown_tool_no_opinion(repo):
    assert hook(json.dumps({"tool_name": "mcp__fs__read", "tool_input": {"path": ".env"}, "cwd": str(repo)}))[1] is None


def test_never_allows(repo):
    for stdin in (payload("Bash", str(repo), "ls"), payload("Bash", str(repo), "cat .env")):
        assert '"allow"' not in json.dumps(hook(stdin)[1])


def test_content_events_apply_the_patch(repo):
    (repo / "app.py").write_text("def f():\n    return 1\n")
    patch = envelope("*** Update File: app.py", "@@ def f():", "-    return 1", "+    # two", "+    return 2",
                     "*** Add File: new.py", "+a = 1")
    ev = codex.events(json.loads(payload("apply_patch", str(repo), patch)), "pre", content=True)
    assert [(e.kind, e.path) for e in ev] == [("edit", str(repo / "app.py")), ("write", str(repo / "new.py"))]
    assert ev[0].before == "def f():\n    return 1\n" and ev[0].after == "def f():\n    # two\n    return 2\n"
    assert ev[1].after == "a = 1\n"


def test_stale_patch_judged_on_hunk_text(repo):
    (repo / "app.py").write_text("changed since\n")
    patch = envelope("*** Update File: app.py", "@@", " keep", "-old", "+new")
    [ev] = codex.events(json.loads(payload("apply_patch", str(repo), patch)), "pre", content=True)
    assert (ev.before, ev.after) == ("keep\nold\n", "keep\nnew\n")


def test_moved_file_is_judged_at_its_destination(repo):
    (repo / "a.py").write_text("x\n")
    patch = envelope("*** Update File: a.py", "*** Move to: b.py", "@@", "-x", "+y")
    ev = codex.events(json.loads(payload("apply_patch", str(repo), patch)), "pre", content=True)
    assert [(e.kind, e.path or e.command) for e in ev] == [("shell", str(repo / "a.py")), ("edit", str(repo / "b.py"))]
    assert (ev[1].before, ev[1].after) == ("x\n", "y\n")


def test_post_phase_reads_the_result(repo):
    (repo / "app.py").write_text("after\n")
    patch = envelope("*** Update File: app.py", "@@", "-before", "+after", "*** Delete File: gone.py")
    ev = codex.events(json.loads(payload("apply_patch", str(repo), patch, "PostToolUse")), "post", content=True)
    assert [(e.kind, e.after) for e in ev if e.kind == "edit"] == [("edit", "after\n")]


def test_content_not_read_unless_a_rule_needs_it(repo, secrets_only):
    (repo / "app.py").write_text("x\n")
    ev = codex.events(json.loads(payload("apply_patch", str(repo), envelope("*** Update File: app.py", "-x", "+y"))),
                      "pre")
    assert [(e.kind, e.before, e.after) for e in ev] == [("edit", None, None)]


def test_indented_marker_in_a_doc_edit_is_not_a_secret_reference(repo):
    (repo / "README.md").write_text("intro\n *** Delete File: .env\nold\n")
    patch = envelope("*** Update File: README.md", "@@", " intro", "  *** Delete File: .env", "-old", "+new")
    assert hook(payload("apply_patch", str(repo), patch))[1] is None


def test_post_tool_use_block_output(repo, stub_rules):
    from devkit.events import Finding

    stub_rules("p", guard=True, phases={"post"}, kinds={"edit"}, findings=[Finding("p", "fix this")])
    (repo / "a.py").write_text("y\n")
    _, resp, _ = hook(payload("apply_patch", str(repo), envelope("*** Update File: a.py", "-x", "+y"), "PostToolUse"),
                      "post-tool-use")
    assert resp == {"decision": "block", "reason": "[devkit p] fix this"}


def test_console_script_end_to_end(repo):
    import os
    import subprocess
    import sys

    exe = os.path.join(os.path.dirname(sys.executable), "devkit")
    stdin = payload("apply_patch", str(repo), envelope("*** Add File: .env.local", "+X=1"))
    res = subprocess.run([exe, "hook", "codex", "pre-tool-use"], input=stdin, capture_output=True, text=True)
    assert res.returncode == 0 and denied(json.loads(res.stdout))
