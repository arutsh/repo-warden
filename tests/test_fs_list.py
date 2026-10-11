import os

import pytest

from repo_warden.handlers import fs

POLICY = "repo_warden:\n  sensitive_paths_extra: ['secrets/**']\ntools:\n  filesystem.list: { tiers: { local: L0 } }\n"


@pytest.fixture
def broker(make_broker):
    return make_broker(POLICY, [fs.LIST])[0]


def paths(res) -> list[str]:
    assert res.ok, res.error
    return [e["path"] for e in res.data["entries"]]


async def test_root_hides_git_and_secrets(broker, repo_dir):
    (repo_dir / "src").mkdir()
    (repo_dir / ".env").write_text("API_KEY=abc")
    (repo_dir / ".env.local").write_text("API_KEY=abc")
    (repo_dir / "secrets").mkdir()
    (repo_dir / "secrets" / "prod.yaml").write_text("x")
    assert (repo_dir / ".git").is_dir()
    res = await broker.execute("filesystem.list", {})
    assert paths(res) == ["src"]
    assert res.data["entries"][0]["type"] == "dir" and res.data["truncated"] is False


async def test_unreadable_dir_listed_not_descended(broker, locked):
    res = await broker.execute("filesystem.list", {"depth": 2})
    assert paths(res) == [locked]
    assert "nested_repo" not in res.data["entries"][0]


async def test_file_removed_during_listing_skipped(broker, repo_dir, monkeypatch):
    (repo_dir / "gone.txt").write_text("x")
    (repo_dir / "kept.txt").write_text("x")
    real_scandir = os.scandir

    class Vanished:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def stat(self, **kw):
            raise FileNotFoundError(2, "gone", self._entry.name)

    class Scan:
        def __init__(self, fd):
            self._it = real_scandir(fd)

        def __enter__(self):
            return (Vanished(e) if e.name == "gone.txt" else e for e in self._it)

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(os, "scandir", Scan)
    assert paths(await broker.execute("filesystem.list", {})) == ["kept.txt"]


async def test_entry_limit_truncates(broker, repo_dir):
    for i in range(10):
        (repo_dir / f"f{i}.txt").write_text("x" * i)
    res = await broker.execute("filesystem.list", {"max_entries": 4})
    assert paths(res) == ["f0.txt", "f1.txt", "f2.txt", "f3.txt"]
    assert res.data["truncated"] is True
    assert res.data["entries"][3] == {"path": "f3.txt", "type": "file", "size": 3}


async def test_depth_is_bounded(broker, repo_dir):
    (repo_dir / "a" / "b" / "c").mkdir(parents=True)
    (repo_dir / "a" / "b" / "c" / "deep.txt").write_text("x")
    assert paths(await broker.execute("filesystem.list", {"depth": 2})) == ["a", "a/b"]
    assert paths(await broker.execute("filesystem.list", {"depth": 4}))[-1] == "a/b/c/deep.txt"
    assert paths(await broker.execute("filesystem.list", {"path": "a/b"})) == ["a/b/c"]


async def test_symlinked_dir_is_reported_not_descended(broker, repo_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (repo_dir / "out").symlink_to(outside)
    (repo_dir / "real").mkdir()
    (repo_dir / "real" / "x.txt").write_text("x")
    (repo_dir / "alias").symlink_to("real")
    res = await broker.execute("filesystem.list", {"depth": 3})
    assert paths(res) == ["alias", "out", "real", "real/x.txt"]
    assert {e["path"]: e["type"] for e in res.data["entries"]}["out"] == "symlink"
    assert {e["path"]: e["type"] for e in res.data["entries"]}["alias"] == "symlink"


async def test_nested_boundaries_listed_not_descended(broker, nested):
    res = await broker.execute("filesystem.list", {"depth": 8})
    listed = {e["path"]: e for e in res.data["entries"]}
    for d in nested.values():
        assert listed[d]["nested_repo"] is True
        assert not any(p.startswith(d + "/") for p in listed)
    assert listed["lib"]["type"] == "symlink"


async def test_listing_inside_nested_boundary_denied(broker, nested):
    res = await broker.execute("filesystem.list", {"path": nested["worktree"]})
    assert not res.ok and res.rule_id == "path.nested_repo"


async def test_listing_a_file_is_refused(broker, repo_dir):
    (repo_dir / "a.txt").write_text("x")
    res = await broker.execute("filesystem.list", {"path": "a.txt"})
    assert not res.ok and res.error == "directory not found: a.txt"


async def test_listing_a_sensitive_dir_is_denied(broker, repo_dir):
    (repo_dir / "secrets").mkdir()
    res = await broker.execute("filesystem.list", {"path": "secrets"})
    assert not res.ok and res.rule_id == "path.sensitive"


async def test_policy_output_cap_truncates(make_broker, repo_dir):
    small = POLICY.replace("L0 }", "L0 }, output: { max_chars: 100 }")
    broker = make_broker(small, [fs.LIST])[0]
    for i in range(20):
        (repo_dir / f"file-{i:02}.txt").write_text("x")
    res = await broker.execute("filesystem.list", {})
    assert res.ok and res.data["truncated"] is True
    assert 0 < len(res.data["entries"]) < 20
