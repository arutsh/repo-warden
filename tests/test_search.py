import os
import shutil
import time

import pytest

from repo_warden.handlers import search as search_mod
from repo_warden.handlers.search import SEARCH_MAX_FILE, make_search

from .conftest import records

POLICY = "repo_warden:\n  sensitive_paths_extra: ['secrets/**']\ntools:\n  search.code: { tiers: { local: L0 } }\n"


@pytest.fixture(params=["rg", "python"])
def engine(request, monkeypatch, tmp_path):
    """Both engines, each selected the way startup selects it: python is chosen because rg is not on PATH."""
    if request.param == "rg":
        if shutil.which("rg") is None:
            pytest.skip("ripgrep is not installed")
    else:
        no_rg = tmp_path / "no-rg"  # a PATH holding only git, which the fixtures need
        no_rg.mkdir()
        (no_rg / "git").symlink_to(shutil.which("git"))
        monkeypatch.setenv("PATH", str(no_rg))
        assert shutil.which("rg") is None
    return request.param, make_search()


@pytest.fixture
def search(engine, make_broker):
    name, cap = engine
    broker, audit = make_broker(POLICY, [cap])

    async def run(**args):
        res = await broker.execute("search.code", args)
        if res.ok:
            assert res.data["engine"] == name
        return res

    run.audit = audit
    return run


def hits(res) -> list[tuple[str, int]]:
    assert res.ok, res.error
    return [(m["path"], m["line"]) for m in res.data["matches"]]


async def test_finds_matches_with_line_numbers(search, repo_dir):
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("a\nNEEDLE here\nb\nNEEDLE again\n")
    (repo_dir / "README.md").write_text("no match\n")
    res = await search(pattern="NEEDLE")
    assert hits(res) == [("src/app.py", 2), ("src/app.py", 4)]
    assert res.data["matches"][0]["text"] == "NEEDLE here" and res.data["truncated"] is False


async def test_sensitive_files_never_returned(search, repo_dir):
    (repo_dir / ".env").write_text("API_KEY=abc\n")
    (repo_dir / "config").mkdir()
    (repo_dir / "config" / ".env.production").write_text("API_KEY=abc\n")
    (repo_dir / "server.pem").write_text("API_KEY=abc\n")
    (repo_dir / "secrets").mkdir()
    (repo_dir / "secrets" / "prod.yaml").write_text("API_KEY=abc\n")
    (repo_dir / "app.py").write_text("API_KEY = os.environ['API_KEY']\n")
    assert hits(await search(pattern="API_KEY")) == [("app.py", 1)]
    # An agent glob naming the secret cannot re-include it.
    assert hits(await search(pattern="API_KEY", glob=".env")) == []


async def test_git_dir_never_searched(search, repo_dir):
    (repo_dir / ".git" / "NEEDLE_NOTES").write_text("NEEDLE\n")
    (repo_dir / ".git" / "description").write_text("NEEDLE\n")
    assert hits(await search(pattern="NEEDLE")) == []


async def test_match_limit_truncates(search, repo_dir):
    (repo_dir / "a.txt").write_text("NEEDLE\n" * 10)
    (repo_dir / "b.txt").write_text("NEEDLE\n" * 10)
    res = await search(pattern="NEEDLE", max_matches=3)
    assert hits(res) == [("a.txt", 1), ("a.txt", 2), ("a.txt", 3)] and res.data["truncated"] is True


@pytest.mark.parametrize("pattern", ["$(id)", "; rm -rf ~", "`touch pwned`", "-e", "--pre=sh"])
async def test_pattern_is_literal(search, repo_dir, pattern):
    (repo_dir / "a.txt").write_text(f"x {pattern} y\nuid=0(root)\n")
    assert hits(await search(pattern=pattern)) == [("a.txt", 1)]
    assert sorted(os.listdir(repo_dir)) == [".git", "a.txt"]


async def test_nested_boundaries_never_searched(search, engine, repo_dir, nested):
    (repo_dir / "top.py").write_text("NEEDLE = 0\n")
    res = await search(pattern="NEEDLE")
    assert hits(res) == [("top.py", 1)]
    # rg searches nested repos and the post-filter drops their matches; the walker never enters them.
    # The count goes to the audit record only.
    assert "_filtered" not in res.data and "filtered" not in res.data
    rec = [r for r in records(search.audit) if r["call_id"] == res.call_id][-1]
    nested_drops = len(nested) if engine[0] == "rg" else 0
    assert rec["detail"]["filtered"] == {"excluded": 0, "nested": nested_drops}


async def test_symlinks_not_followed(search, repo_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("NEEDLE\n")
    (repo_dir / "out").symlink_to(outside)
    (repo_dir / "link.txt").symlink_to(outside / "x.txt")
    assert hits(await search(pattern="NEEDLE")) == []


async def test_binary_and_large_files_skipped(search, repo_dir):
    (repo_dir / "bin.dat").write_bytes(b"NEEDLE\x00\x01\x02")
    (repo_dir / "big.txt").write_text("NEEDLE\n" + "x" * SEARCH_MAX_FILE)
    (repo_dir / "ok.txt").write_text("NEEDLE\n")
    assert hits(await search(pattern="NEEDLE")) == [("ok.txt", 1)]


async def test_glob_path_regex_and_case(search, repo_dir):
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "a.py").write_text("def foo_1():\n")
    (repo_dir / "src" / "b.ts").write_text("function foo_2() {}\n")
    (repo_dir / "docs").mkdir()
    (repo_dir / "docs" / "c.py").write_text("FOO_3\n")
    assert hits(await search(pattern="foo", glob="*.py")) == [("src/a.py", 1)]
    assert hits(await search(pattern="foo", glob="src/**/*.py")) == [("src/a.py", 1)]
    assert hits(await search(pattern="foo", path="src")) == [("src/a.py", 1), ("src/b.ts", 1)]
    assert hits(await search(pattern="foo", path="src/b.ts")) == [("src/b.ts", 1)]
    assert hits(await search(pattern=r"foo_\d", regex=True, ignore_case=True)) == [
        ("docs/c.py", 1), ("src/a.py", 1), ("src/b.ts", 1)]
    assert hits(await search(pattern=r"foo_\d")) == []


async def test_invalid_regex_is_an_error(search, repo_dir):
    (repo_dir / "a.txt").write_text("x\n")
    res = await search(pattern="(", regex=True)
    assert not res.ok and res.error.startswith("search failed")


async def test_unreadable_dir_skipped(search, repo_dir, locked):
    (repo_dir / "a.txt").write_text("NEEDLE\n")
    assert hits(await search(pattern="NEEDLE")) == [("a.txt", 1)]
    assert hits(await search(pattern="nothing matches this")) == []


async def test_backtracking_regex_times_out(search, engine, repo_dir, monkeypatch):
    if engine[0] != "python":
        pytest.skip("ripgrep's regex engine does not backtrack")
    monkeypatch.setattr(search_mod, "SEARCH_TIMEOUT_S", 0.5)
    (repo_dir / "a.txt").write_text("a" * 40 + "!\n")
    start = time.monotonic()
    res = await search(pattern="(a|aa)+$", regex=True)
    assert not res.ok and res.error == "search timed out"
    assert time.monotonic() - start < 5


async def test_missing_path_is_an_error(search):
    res = await search(pattern="x", path="nope")
    assert not res.ok and "path not found" in res.error


async def test_negated_glob_rejected(search):
    res = await search(pattern="x", glob="!*.py")
    assert not res.ok and res.rule_id == "input.invalid"


async def test_unsafe_path_denied(search, nested):
    res = await search(pattern="NEEDLE", path=nested["nested"])
    assert not res.ok and res.rule_id == "path.nested_repo"
    res = await search(pattern="NEEDLE", path="../")
    assert not res.ok and res.rule_id == "path.escape"


async def test_audit_has_engine_and_redacted_pattern(search, engine, repo_dir):
    (repo_dir / "a.txt").write_text("x\n")
    res = await search(pattern="ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert res.ok
    rec = [r for r in records(search.audit) if r["call_id"] == res.call_id][-1]
    assert rec["detail"]["engine"] == engine[0]
    assert rec["args"]["pattern"] == "[REDACTED]"
    assert "ghp_" not in search.audit.read_text()
