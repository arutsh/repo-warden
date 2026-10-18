from devkit.engine import evaluate
from devkit.events import Event, Finding, Level


def shell(cwd, cmd="true"):
    return Event("shell", command=cmd, cwd=str(cwd))


def test_warn_does_not_block(stub_rules, repo, user_config):
    user_config("repos: all\n")
    stub_rules("w", findings=[Finding("w", "careful")], default=Level.WARN)
    out = evaluate([shell(repo)])
    assert not out.blocked and [f.message for f in out.warnings] == ["careful"]


def test_block_wins_and_every_finding_reported(stub_rules, repo, user_config):
    user_config("repos: all\n")
    stub_rules("w", findings=[Finding("w", "careful")], default=Level.WARN)
    stub_rules("b", findings=[Finding("b", "no")], default=Level.BLOCK)
    out = evaluate([shell(repo)])
    assert out.blocked
    assert {(lvl, f.rule) for lvl, f in out.findings} == {(Level.WARN, "w"), (Level.BLOCK, "b")}


def test_off_rule_not_run(stub_rules, repo, user_config):
    user_config("repos: all\n")
    rule = stub_rules("o", findings=[Finding("o", "x")], default=Level.OFF)
    assert evaluate([shell(repo)]).findings == [] and type(rule).calls == 0


def test_max_level_caps(stub_rules, repo, user_config):
    user_config("repos: all\n")
    stub_rules("capped", findings=[Finding("capped", "x")], default=Level.BLOCK, max_level=Level.WARN)
    out = evaluate([shell(repo)])
    assert not out.blocked and len(out.warnings) == 1


def test_kind_filter(stub_rules, repo, user_config):
    user_config("repos: all\n")
    rule = stub_rules("k", findings=[Finding("k", "x")], kinds={"edit"})
    assert evaluate([shell(repo)]).findings == [] and type(rule).calls == 0


def test_style_rules_need_scope_guard_rules_do_not(stub_rules, repo):
    stub_rules("style", findings=[Finding("style", "s")])
    stub_rules("guard", findings=[Finding("guard", "g")], guard=True)
    assert [f.rule for _, f in evaluate([shell(repo)]).findings if f.rule != "secrets"] == ["guard"]
    (repo / ".devkit.yaml").write_text("")
    assert {f.rule for _, f in evaluate([shell(repo)]).findings} == {"style", "guard"}


def test_outside_any_repo_only_guard_rules(stub_rules, tmp_path):
    stub_rules("style", findings=[Finding("style", "s")])
    stub_rules("guard", findings=[Finding("guard", "g")], guard=True)
    assert [f.rule for _, f in evaluate([shell(tmp_path)]).findings] == ["guard"]


def test_invalid_layer_becomes_a_note(repo, user_config):
    user_config("rules: [\n")
    out = evaluate([shell(repo, "cat .env")])
    assert out.blocked and out.notes and "config.yaml" in out.notes[0]


def test_file_event_uses_the_files_repo(stub_rules, tmp_path, repo):
    stub_rules("style", findings=[Finding("style", "s")])
    (repo / ".devkit.yaml").write_text("")
    ev = Event("edit", path=str(repo / "a.py"), before="", after="", cwd=str(tmp_path))
    assert any(f.rule == "style" for _, f in evaluate([ev]).findings)


def test_bracketed_path_uses_the_files_repo(stub_rules, tmp_path, repo):
    stub_rules("style", findings=[Finding("style", "s")])
    (repo / ".devkit.yaml").write_text("")
    ev = Event("edit", path=str(repo / "app" / "[id]" / "page.tsx"), before="", after="", cwd=str(tmp_path))
    assert any(f.rule == "style" for _, f in evaluate([ev]).findings)
