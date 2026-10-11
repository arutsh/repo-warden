# Upstream gaps in mcp-airlock 0.2.0

repo-warden uses [mcp-airlock](https://github.com/arutsh/mcp-airlock-broker) as a pinned library
(`mcp-airlock==0.2.0`, byte-identical to commit `4e1793d` of the fork) for its policy model,
policy engine and audit sinks. These gaps were found while integrating it. Each one names the
workaround repo-warden uses for now.

## 1. No `where` argument rules in the released policy model

`ToolRule` in 0.2.0 has tiers, principals, `count_arg`, `output` and `blast_radius`, but no rules on
call arguments. Those exist only on unreleased main. `Policy` forbids extra keys, so an operator
cannot write rules such as "`filesystem.read` only under `src/`".

*Workaround:* path rules are fixed in repo-warden (path safety, sensitive patterns, nested
repositories). The only policy knob for them is `repo_warden.sensitive_paths_extra`, which
repo-warden pops from the YAML before handing the rest to `Policy.model_validate`.

*Wanted:* a release that includes `where` rules, which repo-warden would then evaluate per call.

## 2. Heavy import graph

`import mcp_airlock.<anything>` runs the package `__init__`, which imports the HTTP app.
`mcp_airlock.store` imports psycopg at module level. Using only `policy` and `audit` therefore
installs and imports starlette, uvicorn, httpx, opentelemetry, psycopg and pyjwt.

*Workaround:* accepted for V1. It costs install size and startup time, and widens the dependency
surface of a security component.

*Wanted:* lazy imports in `__init__` and `store`, or a `core` distribution/extra holding `policy`
and `audit` without the HTTP stack.

## 3. Approval tokens live inside the HTTP app

L2 (`confirm`) decisions are completed with HMAC-signed confirmation tokens. These are issued and
verified by methods of the HTTP app class in `mcp_airlock.app` (`issue_token`, `verify_token`),
with a per-process secret and the `/approve/{token}` routes. `mcp_airlock.approvals` only sends the
notification. There is no library API for them, so a stdio broker cannot reuse them without
building the HTTP app.

*Workaround:* repo-warden maps `confirm` to `APPROVAL`, and denies it with `approval.unavailable`
while no `Approver` is configured (always, in V1).

*Wanted:* an approval API in the library (issue, check, expire) that is independent of the
transport, so a host-side approver can be plugged into the broker's `Approver` seam.

## Smaller notes

- `Engine.evaluate` is async, and L3 calls are charged to a blast-radius window in the store, while
  L0 calls are not. repo-warden evaluates first and reserves only after its own checks pass. The
  example policy uses L0 for read capabilities.
- `audit.AuditLog` keeps only its fixed `FIELDS`. repo-warden puts its own fields (repo, session,
  backend, status, result size) into `detail`, and leaves `upstream_status` null because the
  Postgres table types it as `integer`.
- `MultiAudit` logs and swallows a failing sink. repo-warden writes each sink directly, so that a
  failed write fails the call closed.
