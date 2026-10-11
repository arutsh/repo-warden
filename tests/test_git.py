import pytest

from repo_warden.handlers import git as git_mod
from repo_warden.handlers.git import filter_diff, header_paths, parse_status
from repo_warden.paths import SensitiveMatcher

from .conftest import git, records

POLICY = ("repo_warden:\n  sensitive_paths_extra: ['secrets/**']\n"
          "output:\n  max_chars: 100000\n"
          "tools:\n  git.status: { tiers: { local: L0 } }\n  git.diff: { tiers: { local: L0 } }\n"
          "  git.log: { tiers: { local: L0 } }\n")


@pytest.fixture
def call(make_broker):
    broker, audit = make_broker(POLICY, [git_mod.STATUS, git_mod.DIFF, git_mod.LOG])

    async def run(name, **args):
        return await broker.execute(name, args)

    run.audit = audit
    return run


@pytest.fixture
def committed(repo_dir):
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("print('v1')\n")
    (repo_dir / "README.md").write_text("readme\n")
    (repo_dir / ".env").write_text("API_KEY=old\n")
    (repo_dir / "secrets").mkdir()
    (repo_dir / "secrets" / "db.txt").write_text("pw=old\n")
    git(repo_dir, "add", "-A")
    git(repo_dir, "commit", "-q", "-m", "init")
    return repo_dir


def by_path(res):
    assert res.ok, res.error
    return {e["path"]: e for e in res.data["entries"]}


# --- status -------------------------------------------------------------------------------------

async def test_status_modified_staged_untracked(call, committed):
    (committed / "src" / "app.py").write_text("print('v2')\n")
    (committed / "README.md").write_text("staged\n")
    git(committed, "add", "README.md")
    (committed / "new.txt").write_text("n\n")
    res = await call("git.status")
    entries = by_path(res)
    assert entries["src/app.py"]["status"] == ".M" and entries["src/app.py"]["kind"] == "changed"
    assert entries["README.md"]["status"] == "M."
    assert entries["new.txt"] == {"path": "new.txt", "status": "??", "kind": "untracked"}
    assert res.data["branch"]["head"] and res.data["branch"]["oid"] and res.data["truncated"] is False


async def test_status_hides_sensitive(call, committed):
    (committed / ".env").write_text("API_KEY=new\n")
    (committed / "secrets" / "db.txt").write_text("pw=new\n")
    (committed / "config").mkdir()
    (committed / "config" / ".env.local").write_text("X=1\n")
    git(committed, "add", "config/.env.local")
    (committed / "id_rsa").write_text("k\n")
    (committed / "src" / "app.py").write_text("print('v2')\n")
    res = await call("git.status")
    assert set(by_path(res)) == {"src/app.py"}
    outcome = records(call.audit)[-1]
    assert outcome["detail"]["hidden"] == 4 and "hidden" not in res.data


async def test_status_rename_to_sensitive_hidden(call, committed):
    git(committed, "mv", "README.md", ".env.copy")
    assert by_path(await call("git.status")) == {}


async def test_status_entry_cap(call, committed):
    for i in range(10):
        (committed / f"f{i}.txt").write_text("x\n")
    res = await call("git.status", max_entries=3)
    assert len(res.data["entries"]) == 3 and res.data["truncated"] is True


async def test_status_initial_repo(call, repo_dir):
    (repo_dir / "a.txt").write_text("a\n")
    res = await call("git.status")
    assert res.data["branch"]["oid"] is None and set(by_path(res)) == {"a.txt"}


def test_parse_status_rename_and_branch():
    raw = (b"# branch.oid abc\0# branch.head main\0# branch.upstream origin/main\0# branch.ab +2 -1\0"
           b"2 R. N... 100644 100644 100644 h1 h2 R100 new name.txt\0old name.txt\0"
           b"u UU N... 100644 100644 100644 100644 h1 h2 h3 both.txt\0? dir/\0")
    branch, entries = parse_status(raw)
    assert branch == {"oid": "abc", "head": "main", "upstream": "origin/main", "ahead": 2, "behind": 1}
    assert entries == [
        {"path": "new name.txt", "status": "R.", "kind": "renamed", "orig_path": "old name.txt"},
        {"path": "both.txt", "status": "UU", "kind": "unmerged"},
        {"path": "dir/", "status": "??", "kind": "untracked"},
    ]


# --- diff ---------------------------------------------------------------------------------------

async def test_diff_leaves_out_tracked_env(call, committed):
    (committed / ".env").write_text("API_KEY=new-secret\n")
    (committed / "secrets" / "db.txt").write_text("pw=new\n")
    (committed / "src" / "app.py").write_text("print('v2')\n")
    res = await call("git.diff")
    assert res.ok, res.error
    d = res.data["diff"]
    assert "diff --git a/src/app.py b/src/app.py" in d and "+print('v2')" in d
    assert ".env" not in d and "API_KEY" not in d and "secrets/" not in d and "pw=" not in d
    assert res.data["truncated"] is False


async def test_diff_staged(call, committed):
    (committed / "README.md").write_text("staged\n")
    git(committed, "add", "README.md")
    (committed / "src" / "app.py").write_text("print('unstaged')\n")
    staged = (await call("git.diff", staged=True)).data["diff"]
    assert "+staged" in staged and "unstaged" not in staged
    assert "unstaged" in (await call("git.diff")).data["diff"]


async def test_diff_paths(call, committed):
    (committed / "README.md").write_text("changed\n")
    (committed / "src" / "app.py").write_text("print('v2')\n")
    d = (await call("git.diff", paths=["src"])).data["diff"]
    assert "src/app.py" in d and "README.md" not in d


async def test_diff_pathspec_magic_is_literal(call, committed):
    (committed / "README.md").write_text("changed\n")
    res = await call("git.diff", paths=[":(glob)*"])
    assert res.ok and res.data["diff"] == ""


@pytest.mark.parametrize("path,rule", [(".env", "path.sensitive"), ("../x", "path.escape"), (".git/config", "path.git")])
async def test_diff_unsafe_path_denied(call, committed, path, rule):
    res = await call("git.diff", paths=[path])
    assert not res.ok and res.rule_id == rule


async def test_diff_byte_cap_truncates(call, committed):
    (committed / "src" / "app.py").write_text("".join(f"line {i}\n" for i in range(5000)))
    res = await call("git.diff", max_bytes=1000)
    assert res.ok and res.data["truncated"] is True and len(res.data["diff"]) <= 1000


async def test_diff_policy_output_cap(make_broker, committed):
    broker, _ = make_broker("output:\n  max_chars: 200\ntools:\n  git.diff: { tiers: { local: L0 } }\n",
                            [git_mod.DIFF])
    (committed / "src" / "app.py").write_text("".join(f"line {i}\n" for i in range(500)))
    res = await broker.execute("git.diff", {})
    assert res.ok and res.data["truncated"] is True and len(res.data["diff"]) == 200


def test_filter_diff_drops_sensitive_blocks():
    m = SensitiveMatcher()
    text = ("diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/x b/.env\nsimilarity index 90%\nrename from x\nrename to .env\n@@ -1 +1 @@\n-a\n+SECRET\n"
            'diff --git "a/q\\tz" "b/dir/.env"\n--- "a/q\\tz"\n@@ -1 +1 @@\n+SECRET2\n'
            "diff --git a/odd b/.env b/odd b/.env\n@@ -1 +1 @@\n+SECRET3\n")
    out, dropped = filter_diff(text, m)
    assert dropped == 3 and "SECRET" not in out and "+y" in out


def test_filter_diff_cr_does_not_split_block():
    text = "diff --git a/.env b/.env\n@@ -1 +1 @@\n+a\rdiff --git a/ok b/ok\n+SECRET\n"
    out, dropped = filter_diff(text, SensitiveMatcher())
    assert dropped == 1 and "SECRET" not in out


def test_header_paths_quoted_octal():
    assert "café/.env" in header_paths(['diff --git "a/caf\\303\\251/.env" "b/caf\\303\\251/.env"'])


# --- log ----------------------------------------------------------------------------------------

async def test_log_metadata_only_and_max_count(call, committed):
    for i in range(7):
        (committed / "README.md").write_text(f"v{i}\n")
        git(committed, "commit", "-q", "-am", f"change {i}")
    res = await call("git.log", max_count=5)
    assert res.ok, res.error
    commits = res.data["commits"]
    assert len(commits) == 5
    assert [c["subject"] for c in commits] == [f"change {i}" for i in (6, 5, 4, 3, 2)]
    for c in commits:
        assert set(c) == {"hash", "author", "date", "subject"} and len(c["hash"]) == 40 and c["author"] == "t"
    assert "diff" not in str(res.data) and "+v6" not in str(res.data)


async def test_log_path(call, committed):
    (committed / "README.md").write_text("v2\n")
    git(committed, "commit", "-q", "-am", "readme only")
    res = await call("git.log", path="src/app.py")
    assert [c["subject"] for c in res.data["commits"]] == ["init"]


async def test_log_sensitive_path_denied(call, committed):
    res = await call("git.log", path=".env")
    assert not res.ok and res.rule_id == "path.sensitive"


async def test_log_empty_repo(call, repo_dir):
    res = await call("git.log")
    assert res.ok and res.data == {"commits": [], "truncated": False}


async def test_log_output_cap(make_broker, committed):
    broker, _ = make_broker("output:\n  max_chars: 300\ntools:\n  git.log: { tiers: { local: L0 } }\n",
                            [git_mod.LOG])
    for i in range(10):
        git(committed, "commit", "-q", "--allow-empty", "-m", f"c{i}")
    res = await broker.execute("git.log", {"max_count": 10})
    assert res.ok and res.data["truncated"] is True and 0 < len(res.data["commits"]) < 10
