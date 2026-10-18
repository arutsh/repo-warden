import pytest

from devkit.engine import evaluate
from devkit.events import Event
from devkit.rules.secrets import DEFAULT_PATTERNS, RULE

SETTINGS = {"patterns": [], "exempt": []}


def shell(cmd, **settings):
    return RULE.check(Event("shell", command=cmd), {**SETTINGS, **settings})


def read(path, **settings):
    return RULE.check(Event("read", path=path), {**SETTINGS, **settings})


@pytest.mark.parametrize("cmd", [
    "cat .env",
    "grep KEY config/.env.local",
    "head ./certs/server.KEY",
    "less ~/.ssh/id_rsa",
    "source .env && run",
    "python app.py --env-file=.env.production",
    "cat < .env",
    "cat $HOME/proj/.env",
    "cp {a,.env} /tmp",
    "cat '.env",  # broken quoting still checked
    "tar czf x.tgz secrets.p12",
    'bash -c "cat .env"',  # a quoted sub-command is one shell word
    "python -c \"open('.env').read()\"",
    "ssh host 'cat .env'",
])
def test_shell_references_flagged(cmd):
    (f,) = shell(cmd)
    assert f.rule == "secrets" and "ask the user" in f.message


@pytest.mark.parametrize("cmd", [
    "echo environment", "cat docs/dotenv.md", "ls src", "git status", "pytest -k env", "cat .envrc.md",
])
def test_not_a_reference(cmd):
    assert shell(cmd) == []


def test_read_tool_path():
    assert read("/repo/.env.production")
    assert read("/repo/src/app.py") == []


@pytest.mark.parametrize("glob,hit", [
    ("**/.env*", True), (".env.*", True), ("*.pem", True), ("**/*.key", True),
    ("*", False), ("**/*", False), ("*.*", False), ("**/*.py", False), ("src/**", False),
])
def test_search_globs(glob, hit):
    assert bool(read(glob)) is hit


def test_user_exemption():
    assert read("/repo/.env.example", exempt=[".env.example"]) == []
    assert read("/repo/.env.local", exempt=[".env.example"])


def test_extra_patterns():
    assert read("/repo/prod.tfvars") == []
    assert read("/repo/prod.tfvars", patterns=["*.tfvars"])


def test_write_and_edit_flagged():
    assert RULE.check(Event("write", path="/r/.env", after="X=1"), SETTINGS)
    assert RULE.check(Event("edit", path="/r/conf/id_ed25519", before="", after=""), SETTINGS)


def test_finding_never_contains_file_contents(repo):
    (repo / ".env").write_text("API_KEY=sekrit\n")
    out = evaluate([Event("edit", path=str(repo / ".env"), before="API_KEY=sekrit\n", after="x", cwd=str(repo))])
    assert out.blocked and all("sekrit" not in f.message for _, f in out.findings)


def test_defaults_match_repo_warden():
    from repo_warden.paths import DEFAULT_SENSITIVE

    assert DEFAULT_PATTERNS == DEFAULT_SENSITIVE
