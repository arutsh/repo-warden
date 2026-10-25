"""`devkit init` and its templates."""

import yaml

from devkit import config
from devkit.cli import main
from devkit.initcmd import CI_WORKFLOW, init, template
from devkit.rules import comment_brevity


def test_fresh_repo_with_agents_md(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert main(["init", "--agents-md"]) == 0
    assert (repo / "AGENTS.md").read_text() == template("AGENTS.template.md")
    assert (repo / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    assert (repo / ".devkit.yaml").read_text() == template("devkit.yaml")
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "opted in: .devkit.yaml" in capsys.readouterr().out


def test_template_config_is_valid_and_opts_in(repo):
    init(repo)
    cfg = config.load(repo, strict=True)
    assert cfg.opted_in and str(cfg.rules["comment_brevity"].level) == "block"


def test_never_overwrites(repo):
    for name in (".devkit.yaml", "AGENTS.md", "CLAUDE.md"):
        (repo / name).write_text("mine\n")
    out = init(repo, agents_md=True)
    assert all((repo / n).read_text() == "mine\n" for n in (".devkit.yaml", "AGENTS.md", "CLAUDE.md"))
    assert "AGENTS.md: already exists, left unchanged" in out
    assert any(line.startswith("CLAUDE.md: already exists") and "@AGENTS.md" in line for line in out)


def test_existing_claude_md_importing_agents(repo):
    (repo / "CLAUDE.md").write_text("# notes\n@AGENTS.md\n")
    assert "CLAUDE.md: already exists and imports AGENTS.md" in init(repo, agents_md=True)


def test_dangling_symlink_not_overwritten(repo):
    (repo / "AGENTS.md").symlink_to(repo / "missing.md")
    assert "AGENTS.md: already exists, left unchanged" in init(repo, agents_md=True)
    assert not (repo / "missing.md").exists()


def test_dangling_claude_md_symlink_not_overwritten(repo):
    (repo / "CLAUDE.md").symlink_to(repo / "missing.md")
    assert any(line.startswith("CLAUDE.md: already exists, left unchanged") for line in init(repo, agents_md=True))
    assert not (repo / "missing.md").exists()


def test_openspec_rules_appended_when_none(repo):
    path = repo / "openspec" / "config.yaml"
    path.parent.mkdir()
    path.write_text("schema: spec-driven\n# rules:\n#   proposal: []")
    assert "openspec/config.yaml: rules added" in init(repo, openspec=True)
    data = yaml.safe_load(path.read_text())
    assert data["schema"] == "spec-driven" and data["rules"] == yaml.safe_load(template("openspec-rules.yaml"))["rules"]
    assert path.read_text().startswith("schema: spec-driven\n# rules:\n#   proposal: []\n")


def test_openspec_with_rules_unchanged_and_printed(repo):
    path = repo / "openspec" / "config.yaml"
    path.parent.mkdir()
    path.write_text("schema: spec-driven\nrules:\n  tasks: [x]\n")
    out = init(repo, openspec=True)
    assert path.read_text() == "schema: spec-driven\nrules:\n  tasks: [x]\n"
    assert any("already has rules" in line and "proposal:" in line for line in out)


def test_openspec_missing_config_prints_rules(repo):
    out = init(repo, openspec=True)
    assert not (repo / "openspec").exists() or not (repo / "openspec" / "config.yaml").exists()
    assert any("not found" in line and "proposal:" in line for line in out)


def test_openspec_invalid_yaml_refused(repo, monkeypatch, capsys):
    path = repo / "openspec" / "config.yaml"
    path.parent.mkdir()
    path.write_text("schema: [broken\n")
    monkeypatch.chdir(repo)
    assert main(["init", "--openspec"]) == 1
    assert path.read_text() == "schema: [broken\n" and "not valid YAML" in capsys.readouterr().err


def test_ci_workflow_is_valid_yaml(repo):
    assert f"{CI_WORKFLOW}: created" in init(repo, ci=True)
    wf = yaml.safe_load((repo / CI_WORKFLOW).read_text())
    assert "pull_request" in wf[True]  # YAML 1.1 reads the `on` key as true
    run = " ".join(step.get("run", "") for step in wf["jobs"]["check"]["steps"])
    assert 'devkit check --range "origin/${BASE_REF}..HEAD"' in run
    assert wf["jobs"]["check"]["steps"][0]["with"]["fetch-depth"] == 0
    assert f"{CI_WORKFLOW}: already exists, left unchanged" in init(repo, ci=True)


def test_templates_pass_comment_brevity():
    for name in ("devkit.yaml", "devkit-ci.yml", "openspec-rules.yaml"):
        assert comment_brevity.introduced(".yaml", "", template(name), name, 2) == [], name


def test_not_in_a_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 2
    assert "not in a git repository" in capsys.readouterr().err
