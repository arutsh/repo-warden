"""Audit trail over mcp-airlock's sinks: one record per decision, never any file contents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp_airlock.audit import audit_from_env, scrub

from .policy import inside
from .repo import RepoContext


class AuditError(Exception):
    """The audit trail cannot be written; the broker must not start, and a call must fail closed."""


def default_audit_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return Path(base) / "repo-warden" / "audit.jsonl"


class Auditor:
    def __init__(self, sink: Any, repo: RepoContext, backend: str):
        self.sink = sink
        self.repo = repo
        self.backend = backend

    @classmethod
    def open(cls, path: str | os.PathLike[str], repo: RepoContext, backend: str) -> Auditor:
        real = os.path.realpath(path)
        if inside(real, repo.root):
            raise AuditError(f"audit path {real} is inside the repository; it must live outside it")
        try:
            sink = audit_from_env(real)
        except OSError as e:
            raise AuditError(f"cannot open audit log {real}: {e}") from None
        auditor = cls(sink, repo, backend)
        auditor.record("session", "", None, None, "allow", "session.start", None, "ok", 0)
        return auditor

    def record(self, phase: str, call_id: str, capability: str | None, args: dict[str, Any] | None,
               verdict: str, rule_id: str, tier: str | None, status: str, latency_ms: int | None,
               **detail: Any) -> None:
        message = detail.pop("message", None)
        rec = dict(
            phase=phase, call_id=call_id, principal=self.repo.principal, method="tools/call",
            tool=capability, args=args, verdict=verdict, rule_id=rule_id, tier=tier, latency_ms=latency_ms,
            # airlock's Postgres table types upstream_status as integer, so our string status lives in detail
            detail={"repo": self.repo.root, "session": self.repo.session_id, "backend": self.backend,
                    "status": status, **detail,
                    **({"message": scrub(str(message))} if message is not None else {})},
        )
        # Write each sink directly (not through MultiAudit, which logs and swallows failures) so a failed
        # write surfaces and the call fails closed.
        try:
            for sink in getattr(self.sink, "sinks", (self.sink,)):
                sink.write(**rec)
        except Exception as e:
            raise AuditError(f"audit write failed: {type(e).__name__}") from e

    def close(self) -> None:
        self.sink.close()
