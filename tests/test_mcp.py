import json

import pytest
from mcp import Client

from repo_warden.handlers import default_registry
from repo_warden.mcp_server import build_server

from .conftest import records

POLICY = "tools:\n  filesystem.read: { tiers: { local: L2 } }\n"
POLICY_ALLOW = "tools:\n  filesystem.read: { tiers: { local: L0 } }\n"


async def connect(make_broker, policy):
    reg = default_registry()
    broker, audit = make_broker(policy, list(reg))
    return Client(build_server(broker, reg)), audit


async def test_list_tools(make_broker):
    client, _ = await connect(make_broker, POLICY_ALLOW)
    async with client:
        tools = (await client.list_tools()).tools
    assert [t.name for t in tools] == ["filesystem_read"]
    assert tools[0].input_schema["properties"]["path"]["type"] == "string"


async def test_successful_read(make_broker, repo_dir):
    (repo_dir / "hello.txt").write_text("hello\n")
    client, _ = await connect(make_broker, POLICY_ALLOW)
    async with client:
        res = await client.call_tool("filesystem_read", {"path": "hello.txt"})
    assert not res.is_error
    assert json.loads(res.content[0].text)["content"] == "hello\n"


async def test_denied_call_is_error(make_broker, repo_dir):
    (repo_dir / "hello.txt").write_text("hello\n")
    client, audit = await connect(make_broker, POLICY)
    async with client:
        res = await client.call_tool("filesystem_read", {"path": "hello.txt"})
    assert res.is_error
    body = json.loads(res.content[0].text)
    assert body["rule_id"] == "approval.unavailable" and body["error"]
    assert any(r["rule_id"] == "approval.unavailable" for r in records(audit))


async def test_unlisted_tool_denied_and_audited(make_broker):
    client, audit = await connect(make_broker, POLICY_ALLOW)
    async with client:
        try:
            res = await client.call_tool("shell_run", {"cmd": "id"})
        except Exception as e:  # a client-side refusal would also be acceptable, but must not reach a handler
            pytest.fail(f"client refused before reaching the server: {e!r}")
    assert res.is_error and json.loads(res.content[0].text)["rule_id"] == "capability.unknown"
    assert any(r["tool"] == "shell_run" and r["rule_id"] == "capability.unknown" for r in records(audit))
