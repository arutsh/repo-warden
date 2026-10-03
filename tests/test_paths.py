import os
from pathlib import Path

import pytest

from repo_warden.paths import DEFAULT_SENSITIVE, PathDenied, SafePath, SensitiveMatcher, open_nofollow, resolve

M = SensitiveMatcher()


def denied(repo, rel, rule=None):
    with pytest.raises(PathDenied) as ei:
        resolve(repo, rel, M)
    if rule:
        assert ei.value.rule_id == rule
    return ei.value


def read(repo, safe: SafePath) -> bytes:
    fd = open_nofollow(repo, safe)
    try:
        return os.read(fd, 1 << 16)
    finally:
        os.close(fd)


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    d = tmp_path / "outside"
    d.mkdir()
    (d / "file").write_text("outside secret")
    (d / "passwd").write_text("root:x")
    return d


@pytest.mark.parametrize("rel", ["../other-repo/file.txt", "src/../../x", ".."])
def test_parent_escape(repo, rel):
    denied(repo, rel, "path.escape")


def test_in_repo_dot_segments(repo, repo_dir):
    (repo_dir / "src").mkdir()
    (repo_dir / "README.md").write_text("hi")
    assert resolve(repo, "src/../README.md", M) == SafePath("README.md")
    assert resolve(repo, "./README.md", M) == SafePath("README.md")
    assert resolve(repo, "", M) == SafePath("")


def test_absolute(repo):
    denied(repo, "/etc/passwd", "path.absolute")


@pytest.mark.parametrize("rel", ["~/.ssh/id_rsa", "~root"])
def test_home(repo, rel):
    denied(repo, rel, "path.home")


def test_nul(repo):
    denied(repo, "a\x00b", "path.nul")


@pytest.mark.parametrize("rel", [".git/config", ".GIT/config", ".git", "sub/.Git/HEAD"])
def test_git_internals(repo, rel):
    denied(repo, rel, "path.git")


def test_symlink_into_git(repo, repo_dir):
    (repo_dir / "cfg").symlink_to(".git/config")
    denied(repo, "cfg", "path.git")


def test_direct_symlink_escape(repo, repo_dir, outside):
    (repo_dir / "link").symlink_to(outside)
    denied(repo, "link/passwd", "path.escape")


def test_chained_symlink_escape(repo, repo_dir, outside):
    (repo_dir / "c").symlink_to(os.path.relpath(outside, repo_dir))
    (repo_dir / "b").symlink_to("c")
    (repo_dir / "a").symlink_to("b")
    denied(repo, "a/file", "path.escape")


def test_nested_symlink_escape(repo, repo_dir, outside):
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "vendor").symlink_to(os.path.relpath(outside, repo_dir / "src"))
    denied(repo, "src/vendor/file", "path.escape")


def test_in_repo_symlink(repo, repo_dir):
    (repo_dir / "docs" / "v2").mkdir(parents=True)
    (repo_dir / "docs" / "v2" / "index.md").write_text("v2 docs")
    (repo_dir / "docs" / "latest").symlink_to("v2")
    safe = resolve(repo, "docs/latest/index.md", M)
    assert safe == SafePath("docs/v2/index.md")
    assert read(repo, safe) == b"v2 docs"


def test_symlink_to_env(repo, repo_dir):
    (repo_dir / ".env").write_text("API_KEY=abc")
    (repo_dir / "notes.txt").symlink_to(".env")
    denied(repo, "notes.txt", "path.sensitive")


@pytest.mark.parametrize("rel", [".env", "config/.env.production", "certs/server.pem", "deploy/id_ed25519",
                                 "a/b.key", "c.p12", "d.pfx", "X.PEM", ".ENV"])
def test_default_sensitive(repo, rel):
    denied(repo, rel, "path.sensitive")


def test_every_default_pattern_is_covered():
    samples = {".env": ".env", ".env.*": ".env.local", "*.pem": "a.pem", "*.key": "a.key",
               "*.p12": "a.p12", "*.pfx": "a.pfx", "id_*": "id_rsa"}
    assert set(samples) == set(DEFAULT_SENSITIVE)
    assert all(M.matches(v) for v in samples.values())
    assert not M.matches("src/app.py")
    assert not M.matches("environment.py")


def test_extra_sensitive_patterns():
    m = SensitiveMatcher(["*.tfstate", "secrets/**"])
    assert m.matches("infra/prod.tfstate")
    assert m.matches("secrets")
    assert m.matches("secrets/db/pass.txt")
    assert not m.matches("src/secrets.py")


def test_symlink_swapped_in_after_resolve(repo, repo_dir, outside):
    (repo_dir / "a").mkdir()
    (repo_dir / "a" / "file").write_text("inside")
    safe = resolve(repo, "a/file", M)
    os.rename(repo_dir / "a", repo_dir / "a_orig")
    (repo_dir / "a").symlink_to(outside)
    with pytest.raises(PathDenied) as ei:
        open_nofollow(repo, safe)
    assert ei.value.rule_id == "path.race"


def test_final_component_swapped_for_symlink(repo, repo_dir, outside):
    (repo_dir / "f").write_text("inside")
    safe = resolve(repo, "f", M)
    (repo_dir / "f").unlink()
    (repo_dir / "f").symlink_to(outside / "file")
    with pytest.raises(PathDenied) as ei:
        open_nofollow(repo, safe)
    assert ei.value.rule_id == "path.race"


def test_fifo_does_not_block(repo, repo_dir):
    os.mkfifo(repo_dir / "pipe")
    safe = resolve(repo, "pipe", M)
    with pytest.raises(PathDenied) as ei:
        open_nofollow(repo, safe)
    assert ei.value.rule_id == "path.not_regular"


def test_directory_is_not_a_file(repo, repo_dir):
    (repo_dir / "d").mkdir()
    with pytest.raises(PathDenied, match="not a regular file"):
        open_nofollow(repo, resolve(repo, "d", M))
    fd = open_nofollow(repo, resolve(repo, "d", M), directory=True)
    os.close(fd)


@pytest.mark.parametrize("pattern", ["secrets/", "/secrets/**", "./secrets/**", "secrets"])
def test_decorated_directory_patterns_match(pattern):
    m = SensitiveMatcher([pattern])
    assert m.matches("secrets/prod.yaml")
    assert not m.matches("src/app.py")


def test_globstar_prefix_matches_at_root():
    m = SensitiveMatcher(["**/creds.json", "**/conf/*.ini"])
    assert m.matches("creds.json") and m.matches("a/b/creds.json")
    assert m.matches("conf/db.ini") and m.matches("x/conf/db.ini")


def test_anchored_name_stays_anchored():
    m = SensitiveMatcher(["/creds.json"])
    assert m.matches("creds.json")
    assert not m.matches("sub/creds.json")


@pytest.mark.parametrize("pattern", ["", "/", "./", " "])
def test_empty_pattern_rejected(pattern):
    with pytest.raises(ValueError):
        SensitiveMatcher([pattern])


def test_file_used_as_directory_is_not_found(repo, repo_dir):
    (repo_dir / "README.md").write_text("x")
    with pytest.raises(FileNotFoundError):
        open_nofollow(repo, resolve(repo, "README.md/x", M))
