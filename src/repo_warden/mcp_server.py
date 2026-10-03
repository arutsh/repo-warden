"""stdio MCP adapter: one tool per capability, every call is one Broker.execute. No policy or I/O here."""

from __future__ import annotations

import json
from typing import Any

import mcp_types as types
from mcp.server import Server

from . import __version__
from .broker import Broker, Result
from .capabilities import Registry


def tool_name(capability: str) -> str:
    return capability.replace(".", "_")


def to_call_result(res: Result) -> types.CallToolResult:
    if res.ok:
        text = json.dumps(res.data, ensure_ascii=False)
    else:
        text = json.dumps({"error": res.error, "rule_id": res.rule_id, "call_id": res.call_id})
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=not res.ok)


def build_server(broker: Broker, registry: Registry) -> Server[Any]:
    tools = [types.Tool(name=tool_name(c.name), description=c.description, input_schema=c.input.model_json_schema())
             for c in registry]
    to_capability = {tool_name(c.name): c.name for c in registry}

    async def list_tools(ctx: Any, params: types.PaginatedRequestParams | None) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        # An unlisted name goes through unchanged so the broker denies and audits it.
        res = await broker.execute(to_capability.get(params.name, params.name), params.arguments)
        return to_call_result(res)

    return Server("repo-warden", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool)
