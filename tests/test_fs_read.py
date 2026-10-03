import os

import pytest

from repo_warden.handlers import default_registry

from .conftest import records

POLICY = "tools:\n  filesystem.read: { tiers: { local: L0 } }\n"
SMALL_CAP = (
    "tools:\n  filesystem.read: { tiers: { local: L0 }, output: { max_chars: 10 } }\n"
)


@pytest.fixture
def broker(make_broker):
    return make_broker(POLICY, list(default_registry()))


async def test_normal_read(broker, repo_dir):
    b, _ = broker
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("print('hi')\n")
    res = await b.execute("filesystem.read", {"path": "src/app.py"})
    assert res.ok
    assert res.data["content"] == "print('hi')\n"
    assert (
        res.data["size"] == 12
        and res.data["truncated"] is False
        and res.data["binary"] is False
    )


async def test_truncation(broker, repo_dir):
    b, _ = broker
    (repo_dir / "big.txt").write_text("a" * 5000)
    res = await b.execute("filesystem.read", {"path": "big.txt", "max_bytes": 100})
    assert res.ok and res.data["content"] == "a" * 100 and res.data["truncated"] is True
    assert res.data["size"] == 5000


async def test_line_range(broker, repo_dir):
    b, _ = broker
    (repo_dir / "l.txt").write_text("one\ntwo\nthree\nfour\n")
    res = await b.execute(
        "filesystem.read", {"path": "l.txt", "start_line": 2, "end_line": 3}
    )
    assert res.data["content"] == "two\nthree\n"
    bad = await b.execute(
        "filesystem.read", {"path": "l.txt", "start_line": 3, "end_line": 2}
    )
    assert bad.rule_id == "input.invalid"


async def test_invalid_utf8_replaced(broker, repo_dir):
    b, _ = broker
    (repo_dir / "latin1.txt").write_bytes(b"caf\xe9\n")
    res = await b.execute("filesystem.read", {"path": "latin1.txt"})
    assert res.data["content"] == "caf�\n"


async def test_binary_file(broker, repo_dir):
    b, _ = broker
    (repo_dir / "img.bin").write_bytes(b"\x89PNG\x00\x01\x02")
    res = await b.execute("filesystem.read", {"path": "img.bin"})
    assert res.ok and res.data["binary"] is True and res.data["content"] is None


async def test_directory_refused(broker, repo_dir):
    b, _ = broker
    (repo_dir / "d").mkdir()
    res = await b.execute("filesystem.read", {"path": "d"})
    assert not res.ok and res.rule_id == "path.not_regular"


async def test_fifo_refused(broker, repo_dir):
    b, _ = broker
    os.mkfifo(repo_dir / "pipe")
    res = await b.execute("filesystem.read", {"path": "pipe"})
    assert not res.ok and res.rule_id == "path.not_regular"


async def test_missing_file(broker):
    b, _ = broker
    res = await b.execute("filesystem.read", {"path": "nope.txt"})
    assert not res.ok and "not found" in res.error


async def test_extra_repo_field_denied(broker):
    b, _ = broker
    res = await b.execute("filesystem.read", {"path": "x", "repo": "/other"})
    assert res.rule_id == "input.invalid"


async def test_policy_output_cap_smaller_than_byte_limit(make_broker, repo_dir):
    b, _ = make_broker(SMALL_CAP, list(default_registry()))
    (repo_dir / "f.txt").write_text("0123456789abcdef")
    res = await b.execute("filesystem.read", {"path": "f.txt"})
    assert (
        res.ok and res.data["content"] == "0123456789" and res.data["truncated"] is True
    )


async def test_audit_never_contains_contents(broker, repo_dir):
    b, audit = broker
    secret = "aws_access_key_id = AKIAIOSFODNN7EXAMPLE\nfile-marker-contents\n"
    (repo_dir / "creds.txt").write_text(secret)
    res = await b.execute("filesystem.read", {"path": "creds.txt"})
    assert res.ok and "AKIAIOSFODNN7EXAMPLE" in res.data["content"]
    text = audit.read_text()
    assert "AKIA" not in text and "file-marker-contents" not in text
    out = [r for r in records(audit) if r["call_id"] == res.call_id][-1]
    assert out["args"]["path"] == "creds.txt" and out["detail"]["result_bytes"] == len(
        secret
    )


async def test_sensitive_denied_without_contents(broker, repo_dir):
    b, audit = broker
    (repo_dir / ".env").write_text("API_KEY=supersecretvalue")
    res = await b.execute("filesystem.read", {"path": ".env"})
    assert not res.ok and res.rule_id == "path.sensitive"
    assert "supersecretvalue" not in audit.read_text()


async def test_line_range_splits_on_newline_only(broker, repo_dir):
    b, _ = broker
    (repo_dir / "l.txt").write_text("one\fx\ntwoy\nthree\r\nfour\n", newline="")
    res = await b.execute(
        "filesystem.read", {"path": "l.txt", "start_line": 3, "end_line": 4}
    )
    assert res.data["content"] == "three\r\nfour\n"


async def test_file_used_as_directory_is_not_a_race(broker, repo_dir):
    b, audit = broker
    (repo_dir / "README.md").write_text("x")
    res = await b.execute("filesystem.read", {"path": "README.md/x"})
    assert not res.ok and "not found" in res.error
    assert "path.race" not in audit.read_text()


async def test_long_path_at_output_cap(make_broker, repo_dir):
    b, _ = make_broker(SMALL_CAP, list(default_registry()))
    d = repo_dir
    for i in range(6):
        d = d / (str(i) * 200)
    d.mkdir(parents=True)
    (d / "f.txt").write_text("0123456789abcdef")
    rel = str((d / "f.txt").relative_to(repo_dir))
    assert len(rel) > 1100
    res = await b.execute("filesystem.read", {"path": rel})
    assert (
        res.ok and res.data["content"] == "0123456789" and res.data["truncated"] is True
    )
