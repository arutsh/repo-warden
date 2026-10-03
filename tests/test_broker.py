from mcp_airlock.policy import Policy
from pydantic import BaseModel, ConfigDict, Field

from repo_warden.capabilities import Capability, HandlerError, Risk
from repo_warden.paths import resolve
from repo_warden.policy import PolicyGate, Verdict

from .conftest import records


class EchoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field("", max_length=100)


calls: list[str] = []


async def echo(ctx, inp):
    calls.append(inp.path)
    if inp.path == "boom":
        raise HandlerError("exploded with token ghp_" + "a" * 30)
    if inp.path == "crash":
        raise RuntimeError("secret detail")
    if inp.path == "big":
        return {"content": "x" * 100_000}
    resolve(ctx.repo, inp.path, ctx.sensitive)
    return {"echo": inp.path}


def cap(name, risk=Risk.READ):
    return Capability(name, EchoIn, echo, risk, "test", ("path",))


POLICY = """
output: { max_chars: 1000 }
tools:
  t.read: { tiers: { local: L0 } }
  t.write: { tiers: { local: L0 } }
  t.confirm: { tiers: { local: L2 } }
  shell.run: { tiers: { local: L0 } }
"""
CAPS = [cap("t.read"), cap("t.write", Risk.WRITE), cap("t.confirm"), cap("t.unlisted")]


async def test_allowed_call_writes_intent_and_outcome(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    res = await broker.execute("t.read", {"path": "src"})
    assert res.ok and res.data == {"echo": "src"}
    recs = [r for r in records(audit) if r["call_id"] == res.call_id]
    assert [r["phase"] for r in recs] == ["intent", "outcome"]
    assert recs[1]["detail"]["status"] == "ok" and recs[1]["latency_ms"] is not None
    assert recs[1]["args"] == {"path": "src"}


async def test_unknown_capability_denied_and_audited(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    res = await broker.execute("shell.run", {"cmd": "id"})
    assert not res.ok and res.rule_id == "capability.unknown"
    (rec,) = [r for r in records(audit) if r["call_id"] == res.call_id]
    assert rec["verdict"] == "deny" and rec["tool"] == "shell.run"
    assert rec["args"] == {"keys": ["cmd"], "size": 13}


async def test_extra_repo_field_denied(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    res = await broker.execute("t.read", {"path": "x", "repo": "/elsewhere"})
    assert not res.ok and res.rule_id == "input.invalid"
    assert "/elsewhere" not in res.error
    (rec,) = [r for r in records(audit) if r["call_id"] == res.call_id]
    assert rec["rule_id"] == "input.invalid" and "/elsewhere" not in str(rec)


async def test_non_read_capability_denied_despite_policy(make_broker):
    broker, _ = make_broker(POLICY, CAPS)
    calls.clear()
    res = await broker.execute("t.write", {"path": "x"})
    assert not res.ok and res.rule_id == "risk.v1_floor" and calls == []


async def test_l2_denied_without_running_handler(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    calls.clear()
    res = await broker.execute("t.confirm", {"path": "x"})
    assert not res.ok and res.rule_id == "approval.unavailable" and calls == []
    recs = [r for r in records(audit) if r["call_id"] == res.call_id]
    assert len(recs) == 1 and recs[0]["rule_id"] == "approval.unavailable" and recs[0]["tier"] == "L2"


async def test_no_policy_entry_denied(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    calls.clear()
    res = await broker.execute("t.unlisted", {})
    assert not res.ok and res.rule_id == "allowlist.deny" and calls == []
    assert any(r["call_id"] == res.call_id and r["verdict"] == "deny" for r in records(audit))


async def test_path_denial_audited_as_path_rule(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    res = await broker.execute("t.read", {"path": "../x"})
    assert not res.ok and res.rule_id == "path.escape"
    out = [r for r in records(audit) if r["call_id"] == res.call_id][-1]
    assert out["phase"] == "outcome" and out["rule_id"] == "path.escape" and out["detail"]["status"] == "denied"


async def test_handler_errors_are_scrubbed(make_broker):
    broker, audit = make_broker(POLICY, CAPS)
    res = await broker.execute("t.read", {"path": "boom"})
    assert not res.ok and "ghp_" not in res.error
    res = await broker.execute("t.read", {"path": "crash"})
    assert res.error == "internal error: RuntimeError" and res.rule_id == "handler.exception"
    assert "ghp_" not in audit.read_text() and "secret detail" not in audit.read_text()


async def test_output_cap_backstop(make_broker):
    broker, _ = make_broker(POLICY, CAPS)
    res = await broker.execute("t.read", {"path": "big"})
    assert not res.ok and res.rule_id == "output.cap_exceeded"


async def test_audit_failure_fails_closed(make_broker):
    broker, _ = make_broker(POLICY, CAPS)
    broker.auditor.close()
    calls.clear()
    res = await broker.execute("t.read", {"path": "src"})
    assert not res.ok and res.rule_id == "audit.failed" and calls == []


LIMITED = """
tools:
  t.read: { tiers: { local: L3 }, blast_radius: { max_per_principal: 1 } }
"""


async def test_denied_calls_do_not_use_up_the_window(make_broker):
    broker, _ = make_broker(LIMITED, CAPS)
    broker.overlays = (PolicyGate(Policy(environment="local")),)  # empty allowlist: denies everything
    for _ in range(3):
        res = await broker.execute("t.read", {"path": "src"})
        assert not res.ok and res.rule_id == "allowlist.deny"
    broker.overlays = ()
    assert (await broker.execute("t.read", {"path": "src"})).ok
    res = await broker.execute("t.read", {"path": "src"})
    assert not res.ok and res.rule_id == "blast_radius.per_principal"


async def test_overlays_do_not_charge_the_window(make_broker):
    broker, _ = make_broker(LIMITED, CAPS)
    overlay = PolicyGate(Policy(environment="local", tools={"t.read": {"tiers": {"local": "L3"},
                                                                     "blast_radius": {"max_per_principal": 1}}}))
    broker.overlays = (overlay,)
    assert (await broker.execute("t.read", {"path": "src"})).ok
    assert (await overlay.decide("t.read", {}, broker.repo.principal)).verdict is Verdict.ALLOW
