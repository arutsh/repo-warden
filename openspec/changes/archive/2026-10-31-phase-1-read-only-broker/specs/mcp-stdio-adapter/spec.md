# Spec Delta

## Purpose

Exposes the broker's capabilities to any MCP-capable agent over stdio, without adding a path around the broker or any agent-specific behaviour.

## ADDED Requirements

### Requirement: One tool per capability
The MCP server SHALL list exactly one tool per registered capability, using an MCP-safe name derived from the capability name (dots replaced by underscores) and the capability's input schema. It SHALL list no other tools.

#### Scenario: Tool list
- **WHEN** an MCP client lists tools
- **THEN** it sees `filesystem_read`, `filesystem_list`, `search_code`, `git_status`, `git_diff` and `git_log`, each with a JSON input schema

### Requirement: All calls go through the broker
Each tool call SHALL be handed to the broker as one execution request carrying the canonical capability name and the raw arguments. The adapter SHALL NOT make policy decisions, touch the filesystem or start processes itself.

#### Scenario: Denied call over MCP
- **WHEN** a client calls a tool whose capability the policy denies
- **THEN** the client receives a tool error result naming the denial reason and rule id, and the denial is audited

#### Scenario: Unknown tool name
- **WHEN** a client calls a tool name that was not listed
- **THEN** the call is denied by the broker and audited

### Requirement: Agent-neutral stdio transport
The server SHALL use the stdio MCP transport and SHALL NOT behave differently based on the client's identity.

#### Scenario: Any client
- **WHEN** two different MCP clients connect
- **THEN** both receive the same tools and the same decisions for the same calls
