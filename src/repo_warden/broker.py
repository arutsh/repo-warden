"""The single choke point: validate -> policy -> audit (intent) -> handler -> audit (outcome)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mcp_airlock.audit import scrub
from pydantic import ValidationError

from .audit import Auditor, AuditError
from .capabilities import HandlerCtx, HandlerError, Registry, Risk
from .execution import ExecutionBackend
from .paths import PathDenied, SensitiveMatcher
from .policy import Approver, PolicyDecision, PolicyGate, Verdict, strictest
from .repo import RepoContext

ENVELOPE_CHARS = 4096 + 1024  # slack on top of the policy's output cap: a PATH_MAX-long echoed path + metadata


@dataclass(frozen=True)
class Result:
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    rule_id: str | None = None
    call_id: str = ""


def _raw_summary(raw: Any) -> dict[str, Any]:
    """What we may audit about input that failed validation: its top-level keys and size, never values."""
    keys = sorted(str(k)[:64] for k in raw)[:20] if isinstance(raw, dict) else []
    try:
        size = len(json.dumps(raw, default=str))
    except (TypeError, ValueError):
        size = None
    return {"keys": keys, "size": size}


def _validation_message(e: ValidationError) -> str:
    # pydantic's str(e) includes the offending input values; keep only locations and messages.
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'input'}: {err['msg']}" for err in e.errors())


def _string_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(k)) + _string_chars(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_string_chars(v) for v in value)
    return 0


class Broker:
    def __init__(self, *, registry: Registry, gate: PolicyGate, auditor: Auditor, repo: RepoContext,
                 backend: ExecutionBackend, sensitive: SensitiveMatcher,
                 approver: Approver | None = None, overlays: Sequence[PolicyGate] = ()):
        self.registry = registry
        self.gate = gate
        self.auditor = auditor
        self.repo = repo
        self.backend = backend
        self.sensitive = sensitive
        self.approver = approver
        self.overlays = tuple(overlays)

    async def execute(self, name: str, raw_args: Any) -> Result:
        call_id = uuid.uuid4().hex
        start = time.monotonic()
        try:
            return await self._execute(call_id, start, name, raw_args)
        except AuditError:
            return Result(False, error="audit trail unavailable; call refused", rule_id="audit.failed",
                          call_id=call_id)

    async def _execute(self, call_id: str, start: float, name: str, raw_args: Any) -> Result:
        def ms() -> int:
            return int((time.monotonic() - start) * 1000)

        def deny(rule_id: str, message: str, args: dict[str, Any] | None, tier: str | None = None) -> Result:
            message = scrub(message)
            self.auditor.record("outcome", call_id, name, args, "deny", rule_id, tier, "denied", ms(),
                                message=message)
            return Result(False, error=message, rule_id=rule_id, call_id=call_id)

        if raw_args is None:
            raw_args = {}
        cap = self.registry.get(name)
        if cap is None:
            return deny("capability.unknown", f"unknown capability {name!r}", _raw_summary(raw_args))
        if not isinstance(raw_args, dict):
            return deny("input.invalid", "arguments must be an object", _raw_summary(raw_args))
        try:
            inp = cap.input.model_validate(raw_args)
        except ValidationError as e:
            return deny("input.invalid", f"invalid input: {_validation_message(e)}", _raw_summary(raw_args))
        args = inp.model_dump()
        audit_args = {k: args.get(k) for k in cap.audit_fields}

        if cap.risk is not Risk.READ:
            return deny("risk.v1_floor", f"{cap.risk.value} capabilities are not available in this version",
                        audit_args)

        # Nothing is charged to the blast-radius window until every check below has passed.
        primary, raw = await self.gate.evaluate(name, args, self.repo.principal)
        decisions = [primary]
        for overlay in self.overlays:
            decisions.append((await overlay.evaluate(name, args, self.repo.principal))[0])
        decision: PolicyDecision = strictest(*decisions)
        if decision.verdict is Verdict.APPROVAL:
            if self.approver is None:
                return deny("approval.unavailable", "approval required but no approver is configured",
                            audit_args, decision.tier)
            if not await self.approver.request({"call_id": call_id, "capability": name, "args": audit_args}):
                return deny("approval.denied", "approval was not granted", audit_args, decision.tier)
        elif decision.verdict is Verdict.DENY:
            return deny(decision.rule_id, decision.message or "denied by policy", audit_args, decision.tier)
        if primary.verdict is Verdict.ALLOW:
            charged = await self.gate.reserve(name, self.repo.principal, raw)
            if charged.verdict is Verdict.DENY:
                return deny(charged.rule_id, charged.message or "denied by policy", audit_args, charged.tier)

        self.auditor.record("intent", call_id, name, audit_args, "allow", decision.rule_id, decision.tier,
                            "pending", None)
        cap_chars = self.gate.output_cap(name)
        ctx = HandlerCtx(repo=self.repo, backend=self.backend, sensitive=self.sensitive, output_cap=cap_chars)

        def outcome(status: str, rule_id: str, **detail: Any) -> None:
            self.auditor.record("outcome", call_id, name, audit_args, "allow" if status != "denied" else "deny",
                                rule_id, decision.tier, status, ms(), **detail)

        try:
            data = await cap.handler(ctx, inp)
        except PathDenied as e:
            outcome("denied", e.rule_id, message=str(e))
            return Result(False, error=str(e), rule_id=e.rule_id, call_id=call_id)
        except HandlerError as e:
            msg = scrub(str(e))
            outcome("error", decision.rule_id, message=msg)
            return Result(False, error=msg, rule_id=decision.rule_id, call_id=call_id)
        except Exception as e:  # noqa: BLE001 - the broker never raises to the adapter
            msg = f"internal error: {type(e).__name__}"
            outcome("error", "handler.exception", message=msg)
            return Result(False, error=msg, rule_id="handler.exception", call_id=call_id)

        chars = _string_chars(data)
        if chars > cap_chars + ENVELOPE_CHARS:
            outcome("error", "output.cap_exceeded", result_bytes=chars)
            return Result(False, error="result exceeds the policy output cap", rule_id="output.cap_exceeded",
                          call_id=call_id)
        outcome("ok", decision.rule_id, result_bytes=data.get("bytes", chars),
                truncated=bool(data.get("truncated", False)))
        return Result(True, data=data, rule_id=decision.rule_id, call_id=call_id)
