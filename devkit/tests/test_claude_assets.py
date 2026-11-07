import json
import re
import shlex

import pytest
import yaml

from devkit import install
from devkit.cli import build_parser, main
from devkit.rules.review import FOCUSES, Review

EXE = "/opt/devkit/bin/devkit"
ASSETS = install.assets()
AGENT_FILES = {rel: text for rel, text in ASSETS.items() if rel.startswith("agents/")}
SKILL_TEXT = ASSETS["skills/devkit-review/SKILL.md"]


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter"
    return yaml.safe_load(m.group(1)), text[m.end():]


def test_one_agent_per_focus_and_a_checker():
    assert set(AGENT_FILES) == {f"agents/devkit-review-{f}.md" for f in (*FOCUSES, "checker")}


@pytest.mark.parametrize("rel", sorted(AGENT_FILES))
def test_agent_definition(rel):
    meta, body = frontmatter(AGENT_FILES[rel])
    focus = rel.removeprefix("agents/devkit-review-").removesuffix(".md")
    assert meta["name"] == f"devkit-review-{focus}"
    assert [t.strip() for t in meta["tools"].split(",")] == ["Read", "Grep", "Glob", "Bash"]
    default = Review.settings["checker"] if focus == "checker" else Review.settings["reviewers"][focus]
    assert meta["model"] == default
    assert body.splitlines()[0] == install.AGENT_MARKER
    if focus != "checker":
        contract = re.search(r"```json\n(.*?)```", body, re.S).group(1)
        assert set(json.loads(contract)[0]) == {"focus", "file", "line", "quote", "claim", "failure_scenario"}
        assert json.loads(contract)[0]["focus"] == focus


def test_checker_carries_rubric_and_thresholds():
    _, body = frontmatter(AGENT_FILES["agents/devkit-review-checker.md"])
    assert [int(s) for s in re.findall(r"^\| (\d+) \|", body, re.M)] == [0, 25, 50, 75, 100]
    assert "75 or more counts as confirmed" in body and "50 is shown to the user as a\nquestion" in body
    assert "below 50 the finding is dropped" in body
    assert set(json.loads(re.search(r"```json\n(.*?)```", body, re.S).group(1))) == {"score", "reason"}


def test_skill_frontmatter():
    meta, _ = frontmatter(SKILL_TEXT)
    assert meta["name"] == "devkit-review" and meta["description"]


def skill_commands():
    """Every `devkit review ...` the skill tells the agent to run, as argv for the parser."""
    found = []
    for cmd in re.findall(r"`(devkit review [^`]*)`", SKILL_TEXT):
        argv = []
        for tok in shlex.split(re.sub(r"<[^<>]+>|\$\{\w+\}", "x", cmd)):  # placeholders, skill substitutions
            if tok in ("<", "|", ">"):
                break
            argv.append(tok)
        found.append(argv[1:])
    return found


def test_skill_commands_exist_in_the_cli():
    cmds = skill_commands()
    assert {c[1] for c in cmds} == {"plan", "skip", "record", "verdict"}
    for argv in cmds:
        build_parser().parse_args(argv)  # SystemExit (2) on an unknown subcommand or flag


def test_skill_process_points():
    """The spec's process steps that are easy to lose in an edit."""
    text = " ".join(SKILL_TEXT.split())
    assert "never a fork of this conversation" in text
    assert "Paths only: never paste or summarise the files" in text
    assert "Start at most 10 at a time" in text
    assert "dropped** with the reason `no evidence`" in text
    assert "Never commit, push, stage" in text
    assert "never put the JSON in the command line" in text
    assert "devkit review plan --start" in text
    for part in ("**Fixed:**", "**To discuss:**", "**Dropped: N**"):
        assert part in text


@pytest.fixture
def claude_dir(isolated):
    return isolated / ".claude"


def snapshot_files(base):
    return {str(p.relative_to(base)): (p.read_bytes(), p.stat().st_mtime_ns) for p in base.rglob("*")
            if p.is_file() and p.name != "settings.json"}


def test_install_writes_skill_and_agents(claude_dir):
    out = install.install("claude", EXE)
    assert f"installed the devkit-review skill and reviewer agents in {claude_dir}" in out
    for rel, text in ASSETS.items():
        assert (claude_dir / rel).read_text() == text
    assert (claude_dir / "skills/devkit-review/.devkit-managed").is_file()


def test_idempotent_skill_install(claude_dir):
    install.install("claude", EXE)
    first, settings = snapshot_files(claude_dir), (claude_dir / "settings.json").read_text()
    assert "already installed the devkit-review skill" in install.install("claude", EXE)
    assert snapshot_files(claude_dir) == first  # same bytes, and not rewritten
    assert (claude_dir / "settings.json").read_text() == settings
    data = json.loads(settings)
    assert sum("devkit" in h["command"] for gs in data["hooks"].values() for g in gs for h in g["hooks"]) == 5


def test_managed_files_are_refreshed(claude_dir):
    install.install("claude", EXE)
    agent = claude_dir / "agents/devkit-review-tests.md"
    agent.write_text(install.AGENT_MARKER + "\nold version\n")
    (claude_dir / "skills/devkit-review/SKILL.md").write_text("old skill\n")
    assert "installed the devkit-review skill" in install.install("claude", EXE)
    assert agent.read_text() == ASSETS["agents/devkit-review-tests.md"]
    assert (claude_dir / "skills/devkit-review/SKILL.md").read_text() == SKILL_TEXT


def test_foreign_skill_kept(claude_dir, capsys):
    skill = claude_dir / "skills/devkit-review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my own skill\n")
    with pytest.raises(install.PartialInstall) as e:
        install.install("claude", EXE)
    assert str(skill) in str(e.value) and "left unchanged" in str(e.value)
    assert skill.read_text() == "my own skill\n" and not (skill.parent / ".devkit-managed").exists()
    assert (claude_dir / "agents/devkit-review-correctness.md").read_text() == ASSETS[
        "agents/devkit-review-correctness.md"]  # the other files are still installed
    assert "devkit hook claude stop" in (claude_dir / "settings.json").read_text()

    assert main(["install", "--agent", "claude", "--exe", EXE]) == 1
    out, err = capsys.readouterr()
    assert "claude: already installed in" in out and f"devkit: claude: {skill} was not written by devkit" in err
    assert skill.read_text() == "my own skill\n"


def test_foreign_files_in_skill_dir_block_adopting_it(claude_dir):
    other = claude_dir / "skills/devkit-review/notes.md"
    other.parent.mkdir(parents=True)
    other.write_text("mine\n")
    with pytest.raises(install.PartialInstall):
        install.install("claude", EXE)
    assert not (other.parent / "SKILL.md").exists() and not (other.parent / ".devkit-managed").exists()


def test_foreign_agent_kept(claude_dir):
    agent = claude_dir / "agents/devkit-review-security.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\nname: devkit-review-security\n---\nmine\n")
    with pytest.raises(install.PartialInstall) as e:
        install.install("claude", EXE)
    assert e.value.problems == [f"claude: {agent} was not written by devkit (no devkit marker); left unchanged"]
    assert agent.read_text().endswith("mine\n")
    install.uninstall("claude")
    assert agent.read_text().endswith("mine\n")


def test_directory_in_place_of_an_agent_is_refused(claude_dir):
    blocker = claude_dir / "agents/devkit-review-tests.md"
    blocker.mkdir(parents=True)
    with pytest.raises(install.PartialInstall) as e:
        install.install("claude", EXE)
    assert any(str(blocker) in p for p in e.value.problems) and blocker.is_dir()
    install.uninstall("claude")
    assert blocker.is_dir()


def test_config_dir_named_skills(tmp_path, monkeypatch):
    target = tmp_path / "skills"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(target))
    install.install("claude", EXE)
    assert "already installed the devkit-review skill" in install.install("claude", EXE)
    assert all((target / rel).read_text() == text for rel, text in ASSETS.items())
    install.uninstall("claude")
    assert not any((target / rel).exists() for rel in ASSETS)


def test_uninstall_removes_only_managed_files(claude_dir):
    install.install("claude", EXE)
    mine = claude_dir / "skills/devkit-review/my-notes.md"
    mine.write_text("mine\n")
    other_agent = claude_dir / "agents/my-agent.md"
    other_agent.write_text("mine\n")
    out = install.uninstall("claude")
    assert f"removed {len(ASSETS)} devkit-review skill and agent files from {claude_dir}" in out
    assert not any((claude_dir / rel).exists() for rel in ASSETS)
    assert not (mine.parent / ".devkit-managed").exists()
    assert mine.read_text() == "mine\n" and other_agent.read_text() == "mine\n"


def test_uninstall_removes_empty_skill_dir(claude_dir):
    install.install("claude", EXE)
    install.uninstall("claude")
    assert not (claude_dir / "skills/devkit-review").exists()
    assert install.uninstall("claude") == f"claude: not installed in {claude_dir / 'settings.json'}"


def test_claude_config_dir(tmp_path, monkeypatch):
    target = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(target))
    install.install("claude", EXE)
    assert (target / "skills/devkit-review/SKILL.md").read_text() == SKILL_TEXT
    assert (target / "agents/devkit-review-checker.md").is_file()


def test_codex_gets_no_claude_assets(isolated):
    install.install("codex", EXE)
    assert not (isolated / ".claude").exists()


def test_partial_install_says_how_many_files(claude_dir):
    skill = claude_dir / "skills/devkit-review/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("my own skill\n")
    with pytest.raises(install.PartialInstall) as e:
        install.install("claude", EXE)
    assert f"claude: installed {len(ASSETS) - 1} of {len(ASSETS)} devkit-review skill and agent files" in e.value.done
    assert "installed the devkit-review skill" not in e.value.done


def test_uninstall_reports_what_it_removed_before_a_failure(claude_dir, capsys):
    install.install("claude", EXE)
    agents = claude_dir / "agents"
    agents.chmod(0o555)  # its files can't be unlinked
    try:
        assert main(["uninstall", "--agent", "claude"]) == 1
    finally:
        agents.chmod(0o755)
    out, err = capsys.readouterr()
    assert f"claude: removed from {claude_dir / 'settings.json'}" in out
    assert f"claude: removed 1 devkit-review skill and agent files from {claude_dir}" in out  # the skill
    assert err.count("devkit: claude: cannot remove") == len(AGENT_FILES)
    assert "devkit hook" not in (claude_dir / "settings.json").read_text()
    assert all((claude_dir / rel).exists() for rel in AGENT_FILES)
