import pytest

from devkit import config
from devkit.config import ConfigError, load
from devkit.engine import evaluate
from devkit.events import Event, Finding, Level


@pytest.fixture
def style(stub_rules):
    return stub_rules("style", findings=[Finding("style", "s")], default=Level.WARN)


def test_defaults_without_files(repo):
    cfg = load(repo, strict=True)
    assert cfg.rules["secrets"].level is Level.BLOCK and cfg.rules["secrets"].sources["level"] == "default"
    assert cfg.repos == "opted-in" and not cfg.opted_in and not cfg.in_scope


def test_repo_overrides_style_rule(style, repo, user_config):
    user_config("rules:\n  style: warn\n")
    (repo / ".devkit.yaml").write_text("rules:\n  style: block\n")
    rc = load(repo, strict=True).rules["style"]
    assert rc.level is Level.BLOCK and rc.sources["level"] == "repo: .devkit.yaml"


def test_repo_relaxes_style_rule(style, repo, user_config):
    user_config("rules:\n  style: block\n")
    (repo / ".devkit.yaml").write_text("rules:\n  style: off\n")  # YAML reads bare off as false
    assert load(repo, strict=True).rules["style"].level is Level.OFF


@pytest.mark.parametrize("text,key", [
    ("rules:\n  secrets: maybe\n", "rules.secrets.level"),
    ("rules:\n  secrets: true\n", "rules.secrets.level"),
    ("rules:\n  nope: block\n", "rules.nope"),
    ("rules:\n  secrets: { patterns: '*.x' }\n", "rules.secrets.patterns"),
    ("rules:\n  secrets: { colour: red }\n", "rules.secrets.colour"),
    ("other: 1\n", "other"),
    ("repos: some\n", "repos"),
    ("- a\n", "(top)"),
    ("rules: [\n", "(file)"),
])
def test_invalid_repo_config_names_file_and_key(repo, text, key):
    (repo / ".devkit.yaml").write_text(text)
    with pytest.raises(ConfigError) as e:
        load(repo, strict=True)
    assert e.value.key == key and e.value.path == repo / ".devkit.yaml"


def test_lenient_skips_invalid_layer(repo, user_config):
    user_config("rules:\n  secrets: { patterns: ['*.tfvars'] }\n")
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: maybe\n")
    cfg = load(repo, strict=False)
    assert len(cfg.errors) == 1 and cfg.rules["secrets"].settings["patterns"] == ["*.tfvars"]
    assert cfg.opted_in  # a broken file still opts the repo in


def test_deeply_nested_repo_config_keeps_secrets_guard(repo):
    (repo / ".devkit.yaml").write_text("[" * 5000)
    cfg = load(repo, strict=False)
    assert len(cfg.errors) == 1 and cfg.rules["secrets"].level is Level.BLOCK
    assert evaluate([Event("shell", command="cat .env", cwd=str(repo))]).blocked
    with pytest.raises(ConfigError):
        load(repo, strict=True)


def test_repo_cannot_switch_off_secrets(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: off\n")
    cfg = load(repo, strict=True)
    assert cfg.rules["secrets"].level is Level.BLOCK
    assert any("rules.secrets.level: off" in i for i in cfg.ignored)
    assert evaluate([Event("shell", command="cat .env", cwd=str(repo))]).blocked


def test_repo_cannot_add_exemptions_or_set_scope(repo):
    (repo / ".devkit.yaml").write_text("repos: all\nrules:\n  secrets: { exempt: ['.env'] }\n")
    cfg = load(repo, strict=True)
    assert cfg.rules["secrets"].settings["exempt"] == [] and cfg.repos == "opted-in"
    assert len(cfg.ignored) == 2
    assert evaluate([Event("shell", command="cat .env", cwd=str(repo))]).blocked


def test_repo_can_tighten_guard(repo, user_config):
    user_config("rules:\n  secrets: warn\n")
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: { level: block, patterns: ['*.tfvars'] }\n")
    rc = load(repo, strict=True).rules["secrets"]
    assert rc.level is Level.BLOCK and rc.settings["patterns"] == ["*.tfvars"]
    assert evaluate([Event("read", path=str(repo / "prod.tfvars"), cwd=str(repo))]).blocked


def test_user_can_switch_off_secrets(repo, user_config):
    user_config("rules:\n  secrets: off\n")
    assert not evaluate([Event("shell", command="cat .env", cwd=str(repo))]).findings


def test_patterns_accumulate_across_layers(repo, user_config):
    user_config("rules:\n  secrets: { patterns: ['*.tfvars'] }\n")
    (repo / ".devkit.yaml").write_text("rules:\n  secrets: { patterns: ['*.age', '*.tfvars'] }\n")
    assert load(repo, strict=True).rules["secrets"].settings["patterns"] == ["*.tfvars", "*.age"]


def test_scope_all(style, repo, user_config):
    user_config("repos: all\n")
    cfg = load(repo, strict=True)
    assert cfg.in_scope and cfg.repos_source.startswith("user: ")


def test_no_environment_bypass(repo, monkeypatch):
    for var in ("DEVKIT_DISABLE", "DEVKIT", "DEVKIT_OFF", "DEVKIT_SKIP"):
        monkeypatch.setenv(var, "1")
    assert evaluate([Event("shell", command="cat .env", cwd=str(repo))]).blocked


def test_user_config_path_follows_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
    assert config.user_config_path() == tmp_path / "x" / "devkit" / "config.yaml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert config.user_config_path().parts[-3:] == (".config", "devkit", "config.yaml")
