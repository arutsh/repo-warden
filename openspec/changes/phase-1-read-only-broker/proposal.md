# Proposal

## Why

AI coding agents reach repositories through generic tools (shell, raw file access) that carry no notion of "which development action is this, and should it be allowed?". repo-warden adds a semantic layer: a capability broker bound to exactly one repository that decides, with a policy held outside the LLM and outside the repo, whether each development action may run, and audits every decision. OS-level containment stays with cplt. Phase 1 builds the read-only core that every later capability (writes, tests, approvals) will rely on.

## What Changes

- New Python package `repo-warden` (Python >= 3.11, uv, MIT) with a console script `repo-warden --repo <path> [--policy] [--audit] [--backend {cplt,direct}]`.
- One process is bound to one git top level (a main checkout or a linked worktree) and one session for its lifetime. No capability can change the repo or workspace. Nested repositories and worktrees inside it are not exposed.
- Every call goes through one broker pipeline: validate → policy → audit (intent) → handler → audit (outcome). Unknown capabilities, invalid input and anything the policy does not allow are denied and audited.
- Safe repo-relative path handling, which rejects escapes, symlinks that leave the repo, `.git/` internals and a configurable list of sensitive paths. It also holds up against a symlink swapped in after the check.
- Policy reuses `mcp_airlock.policy` (pinned `mcp-airlock==0.2.0`). The policy file must live outside the repo. L2 (approval) is denied in V1, L1 is a load error, and there is a seam so a later repo-level policy can only tighten.
- Audit reuses `mcp_airlock.audit` (JSONL by default in the XDG state dir, Postgres optional). The audit path must be outside the repo, and file contents are never logged.
- An execution backend for subprocesses: argv only, mandatory timeout, bounded output, environment built from an allowlist. The `cplt` backend refuses to start unless containment is verified. The `direct` backend must be chosen explicitly, must be authorised by the account's own policy file, and prints a loud warning. Under cplt, the policy file must be one the sandbox prevents the broker from changing, so a rewritten command line cannot swap the policy or downgrade the backend.
- Six read-only capabilities: `filesystem.read`, `filesystem.list`, `search.code`, `git.status`, `git.diff`, `git.log`. Git runs with repository-controlled config neutralised (hooks, fsmonitor, filters, textconv, external diff, pager, lazy fetch), and sensitive files never appear in listings, search results or diffs.
- A thin stdio MCP adapter (official Python MCP SDK) exposing one tool per capability. It contains no policy or subprocess logic.
- README covering the threat model, broker vs cplt responsibilities, limitations, user-scope setup, and a ROADMAP. An example policy file ships with the package.

## Capabilities

### New Capabilities
- `repo-session`: binding a broker process to one git repository and one session at startup, with no way to change either later.
- `path-safety`: turning agent-supplied relative paths into safe in-repo targets, and the sensitive-path rules.
- `policy-enforcement`: loading the global policy from outside the repo, mapping tiers to allow or deny, default deny, and the stricter-only merge seam.
- `audit-trail`: what is recorded for every call (including denials), where it is stored, and what must never be recorded.
- `execution-backend`: running subprocesses with timeouts, bounded output, an environment allowlist, and verified containment.
- `read-only-capabilities`: behaviour of filesystem.read/list, search.code and git.status/diff/log, including output bounds and secret hiding.
- `mcp-stdio-adapter`: exposing capabilities as MCP tools over stdio without bypassing the broker.

### Modified Capabilities
<!-- none: greenfield project -->

## Impact

- New code: `src/repo_warden/` and `tests/`, plus `pyproject.toml`, `uv.lock`, `README.md`, `LICENSE`, `examples/policy.yaml`.
- Dependencies: `mcp`, `pydantic`, `pyyaml`, `mcp-airlock==0.2.0` (fallback source: github.com/arutsh/mcp-airlock-broker at commit `4e1793d`). Importing mcp-airlock transitively installs starlette, uvicorn, httpx, opentelemetry, psycopg and pyjwt. This is recorded as an upstream gap.
- External tools at runtime: `git` (required), `rg` (optional), `cplt` (for the default backend).
- Known upstream gaps to record: no `where` argument rules in 0.2.0, the heavy import graph, and approval tokens still embedded in the HTTP app.
- Out of scope (ROADMAP only): write_file, test/lint/typecheck capabilities, project inspection, dependency install, approvals, repo-level policy, git commit/push, migrations, HTTP transport, Model B, a VS Code sandbox backend.
