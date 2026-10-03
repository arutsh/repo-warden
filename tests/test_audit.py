import os
from pathlib import Path

import pytest

from repo_warden.audit import Auditor, AuditError, default_audit_path

from .conftest import records


def test_default_path_uses_xdg_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_audit_path() == tmp_path / "repo-warden" / "audit.jsonl"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert default_audit_path() == Path(os.path.expanduser("~/.local/state/repo-warden/audit.jsonl"))


def test_record_fields_and_redaction(repo, tmp_path):
    path = tmp_path / "state" / "audit.jsonl"
    a = Auditor.open(path, repo, "direct")
    a.record("outcome", "c1", "search.code", {"pattern": "ghp_" + "a" * 30, "path": "src"}, "allow",
             "tier.L0.read", "L0", "error", 12, message="failed near AKIAABCDEFGHIJKLMNOP", result_bytes=0)
    a.close()
    start, rec = records(path)
    assert start["rule_id"] == "session.start"
    assert rec["ts"] and rec["principal"] == repo.principal and rec["call_id"] == "c1"
    assert rec["tool"] == "search.code" and rec["verdict"] == "allow" and rec["rule_id"] == "tier.L0.read"
    assert rec["latency_ms"] == 12
    d = rec["detail"]
    assert d["session"] == repo.session_id and d["repo"] == repo.root and d["backend"] == "direct"
    assert d["status"] == "error"
    assert rec["args"] == {"pattern": "[REDACTED]", "path": "src"}
    assert "AKIA" not in d["message"] and "[REDACTED]" in d["message"]


def test_audit_inside_repo_refused(repo, repo_dir):
    with pytest.raises(AuditError, match="inside the repository"):
        Auditor.open(repo_dir / "audit.jsonl", repo, "direct")


def test_audit_unopenable_refused(repo, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(AuditError, match="cannot open"):
        Auditor.open(blocker / "audit.jsonl", repo, "direct")


def test_failed_write_raises(repo, tmp_path):
    a = Auditor.open(tmp_path / "audit.jsonl", repo, "direct")
    a.close()  # writes to a closed file now fail
    with pytest.raises(AuditError):
        a.record("intent", "c", "x", {}, "allow", "r", None, "ok", 0)
