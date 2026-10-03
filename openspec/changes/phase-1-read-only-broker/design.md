# Design

## Context

Greenfield repository: only openspec scaffolding exists. See proposal.md for motivation and specs/ for the requirements.

Fixed constraints:
- V1 deployment model: one cplt sandbox contains the agent, the broker and every child process. There is no host-side component.
- Reuse mcp-airlock as a pinned library without forking or copying.
- cplt is an external binary.
- stdio MCP only. No generic shell capability.
- Supported option, not a requirement: one linked worktree per change, each with its own cplt sandbox (project dir = the worktree) and broker. Worktrees go next to the main checkout, not inside it.

What we checked in `mcp-airlock==0.2.0`, which is byte-identical to commit `4e1793d` of the fork github.com/arutsh/mcp-airlock-broker:
- `Policy` is a pydantic model with `extra="forbid"`. `ToolRule` has tiers, principals, count_arg, output and blast_radius, and **no `where` rules** (those exist only on unreleased main). `Policy.load(path, environment)` reads the whole YAML.
- `Engine.evaluate(...)` is **async** and returns `Decision(verdict, rule_id, tier, ...)`. L3 calls are charged to the blast-radius window held in the store. L0 calls are not.
- `audit.AuditLog.write(**rec)` keeps only `FIELDS` (`phase, call_id, principal, method, tool, args, verdict, rule_id, tier, dry_run, latency_ms, upstream_status, trace_id, detail`), adds `ts`, and runs `redact()` on `args`. `audit_from_env(path)` adds Postgres when `AIRLOCK_AUDIT_DSN` is set.
- `import mcp_airlock.<anything>` runs the package `__init__`, which imports `app`. `store` imports psycopg at module level. The whole HTTP stack therefore installs and imports.

## Goals / Non-Goals

**Goals:**
- A single choke point (`Broker.execute`) that the MCP adapter cannot bypass.
- Defence in depth on paths: lexical checks, realpath containment, then race-safe opening.
- Git and ripgrep invocations that are safe to run on a hostile repository.
- Interfaces that later accept an approver, a repo-level policy overlay and write capabilities without changes to the broker's structure.

**Non-Goals:**
- OS isolation, network control, env sanitisation of the agent itself (cplt's job).
- Protecting against the agent's own native tools (documented limitation).
- A per-command `cplt exec` wrapping mode (not possible inside the V1 sandbox; recursion is refused).
- Windows support. Linux is the target; macOS should work for the direct backend but is untested.

## Decisions

### D1. Module layout
```
src/repo_warden/
  cli.py          argument parsing, startup checks, wiring
  repo.py         RepoContext (root, root_fd, session_id, principal)
  paths.py        resolve(), open_nofollow(), SensitiveMatcher
  capabilities.py Capability, Risk, Registry
  policy.py       load_policy(), PolicyGate (airlock Engine wrapper), Verdict, strictest()
  audit.py        Auditor (wraps airlock AuditLog / MultiAudit)
  broker.py       Broker.execute()
  execution.py    ExecutionBackend, DirectBackend, CpltBackend, verify_containment()
  gitcmd.py       hardened git argv/env construction
  handlers/       fs.py, search.py, git.py
  mcp_server.py   stdio adapter
```
`policy.py`, `audit.py` and `gitcmd.py` are additions to the prompt's layout. They keep the airlock coupling in one place each, and give `repo.py` (the top-level check) and `handlers/git.py` the same git hardening.

### D2. Interfaces
```python
class Risk(Enum): READ; WRITE; EXEC; EXTERNAL

@dataclass(frozen=True)
class Capability:
    name: str                                   # canonical, dotted: "filesystem.read"
    input: type[BaseModel]                      # extra="forbid", bounded fields
    handler: Callable[[HandlerCtx, BaseModel], Awaitable[dict]]
    risk: Risk
    description: str
    audit_fields: tuple[str, ...]               # validated args copied into the audit record

@dataclass(frozen=True)
class ExecResult:
    returncode: int | None; stdout: bytes; stderr: bytes
    timed_out: bool; truncated: bool; duration_ms: int

class ExecutionBackend(Protocol):
    name: str
    async def run(self, argv: Sequence[str], *, cwd: Path, timeout_s: float,
                  max_output: int, env: Mapping[str, str]) -> ExecResult: ...
```
`HandlerCtx` carries `RepoContext`, the backend, the sensitive matcher, and per-call limits (including the policy's output cap). Handlers never see the policy or the audit log.

### D3. Broker pipeline
`execute(name, raw_args)` runs these steps in order:
1. Look the name up in the registry. A miss is `deny/capability.unknown`.
2. Validate the input against the pydantic model. A failure is `deny/input.invalid`, with the error text scrubbed.
3. Apply the risk floor: anything other than `READ` is `deny/risk.v1_floor`.
4. Compute `PolicyGate.decide()`, which yields `Verdict.ALLOW | APPROVAL | DENY` plus a rule id. It is folded through `strictest(global, *overlays)`, where overlays is empty in V1.
5. Map `APPROVAL` to `deny/approval.unavailable` when `approver is None`.
6. Write the audit intent record, run the handler, cap the output, and write the audit outcome record.

Path denials (paths.py raises `PathDenied`) are raised inside handlers, after policy. They are audited as outcome `denied` with a `path.*` rule id. This keeps path rules per-capability, so each capability knows which fields are paths, while still being audited. Every step returns a `Result(ok, data | error, rule_id)`, and the broker never raises to the adapter.

Alternative considered: path checks in the broker before policy, driven by marking path fields in the schema. Rejected for now because it adds a schema convention. It can be revisited once write capabilities exist.

### D4. Policy file format
mcp-airlock's `Policy` forbids extra keys, and 0.2.0 has no argument rules. One YAML file is therefore split by repo-warden:
```yaml
repo_warden:                 # ours, optional
  sensitive_paths_extra: ["*.tfstate", "secrets/**"]
environment: local           # forced to "local" regardless
tools:
  filesystem.read: { tiers: { local: L0 } }
  ...
```
`load_policy` reads the YAML with `safe_load` and pops `repo_warden`, validating it with our own model (`extra="forbid"`). It validates the rest with `Policy.model_validate` after forcing `environment="local"`, and rejects L1 anywhere (tiers and principal overrides). The file's realpath must not be inside the repo. This is plain use of the public model, not monkeypatching. The missing `where` rules are recorded as an upstream gap.

### D5. Verdict mapping and stricter-only merge
Mapping from airlock's `Decision.verdict`:
- `deny` → DENY
- `confirm` (L2) → APPROVAL
- `allow` with tier L0 or L3 → ALLOW
- anything else → DENY with `policy.unexpected`

`strictest(*verdicts)` picks the maximum by DENY > APPROVAL > ALLOW. A future repo-level policy will be evaluated separately and passed only as an overlay, so it can never loosen. The example policy uses L0 for all read capabilities so they don't use up the blast-radius window. Airlock's per-principal overrides stay allowed in the global (operator) policy only.

`Approver` is a `Protocol` with `async request(call) -> bool`, and V1 passes `None`. That is the seam for a later host-side approver.

### D6. Audit
`Auditor.record(phase, call_id, capability, args, verdict, rule_id, tier, status, latency_ms, detail)` calls `sink.write(...)` with:
- `tool=capability`, `method="tools/call"`, `principal=ctx.principal`. `upstream_status` stays null, because airlock's Postgres table types it as `integer` and our status is a string.
- `args` = only the capability's `audit_fields` from validated input, or the raw top-level keys and a size for invalid input. Never contents.
- `detail` = `{repo, session, backend, status, result_bytes, truncated, message}`, with the message `scrub()`bed.

Each sink is written directly rather than through `MultiAudit`, which logs and swallows a failing sink, so that a failed write is seen and the call fails closed. Startup writes a `session.start` record, which also proves the sink is writable.

The sink is `audit_from_env(path)`, so Postgres is used when `AIRLOCK_AUDIT_DSN` is set. The env var name comes from airlock and is documented as-is. The default path is `${XDG_STATE_HOME:-~/.local/state}/repo-warden/audit.jsonl`, and a realpath inside the repo is refused. A failed write at startup is fatal. A failed write mid-call fails that call closed.

### D7. Paths (race safety)
`resolve(ctx, rel)` performs these checks in order:
1. Type and NUL check, `~` prefix check, absolute check.
2. `os.path.normpath`, then lexical `..` escape check.
3. Lexical `.git` component check (case-insensitive) and lexical sensitive check on each component's basename.
4. `os.path.realpath(root/rel)`, then a `commonpath([root, real]) == root` check.
5. Repeat the `.git` and sensitive checks on the real relative path.
6. Nested-boundary check: `lstat` each ancestor directory of `rel_real` below the root for a `.git` entry (file or directory). If one exists, the result is `path.nested_repo`.

The result is a `SafePath(rel_real)`. Step 6 adds one `lstat` per path component, which is negligible next to the read itself.

`open_nofollow(ctx, safe)` walks `rel_real` from `ctx.root_fd`, opening each directory component with `os.open(name, O_RDONLY|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC, dir_fd=fd)`. The final component is opened with `O_RDONLY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, and `fstat` then requires `S_ISREG` (`O_NONBLOCK` stops a FIFO from blocking). Because `rel_real` is already symlink-free, any symlink found during the walk means a swap happened, and ELOOP/ENOTDIR are reported as `path.race`. `openat2(RESOLVE_BENEATH)` would be stronger, but Python has no binding for it. The component walk gives the same guarantee for our purposes.

Listing uses `os.scandir` on an fd opened the same way, with `follow_symlinks=False`.

### D8. Execution
`DirectBackend.run` uses `asyncio.create_subprocess_exec(*argv, cwd=..., env=built_env, stdin=DEVNULL, start_new_session=True)`. Both pipes are read concurrently into buffers capped at `max_output`; once the cap is reached the reader keeps draining and discarding, so the child never blocks on a full pipe. Everything runs under `asyncio.wait_for(timeout)`, and a timeout leads to `os.killpg(SIGKILL)`. `built_env` = `{PATH, LANG, LC_ALL, TZ}` from the broker environment (when present) + the capability's explicit env. `HOME` is set to an empty temp dir owned by the backend, so tools that insist on HOME find nothing.

`CpltBackend` subclasses the direct runner. Its constructor calls `verify_containment()` and raises if any probe fails:
- (a) `__CPLT_WRAPPED` is present.
- (b) `/proc/self/status` shows `Seccomp: 2` on Linux.
- (c) An `O_CREAT|O_EXCL` attempt in the repo's parent directory fails with EACCES/EPERM/EROFS. If it unexpectedly succeeds, the file is removed and the probe fails.
- (d) If `~/.ssh` exists, `os.listdir` fails with a permission error.

Worktree behaviour, checked on the dev host on 2026-10-02:
- With cplt started inside a linked worktree (sibling or nested), `git status` and `git commit` work and the main checkout is not writable.
- With cplt started at the main checkout, a nested worktree is writable and a sibling one is not.
- Probe (c) therefore holds for a broker running in a worktree.

The exact probes will be confirmed against `cplt exec` on the dev host during implementation. If a probe doesn't hold under real cplt, it is replaced with one that does and the design is updated. The probes are not weakened.

### D9. Hardened git (gitcmd.py)
Base environment:
- `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`
- `GIT_TERMINAL_PROMPT=0`, `GIT_OPTIONAL_LOCKS=0`
- `GIT_NO_LAZY_FETCH=1`, `GIT_NO_REPLACE_OBJECTS=1`
- `GIT_PAGER=cat`, `PAGER=cat`
- `GIT_ATTR_NOSYSTEM=1`

Base argv: `git --no-pager` with these `-c` overrides:
- `core.fsmonitor=false`, `core.hooksPath=/dev/null`, `core.untrackedCache=false`
- `core.pager=cat`, `core.sshCommand=false`, `core.askPass=`
- `credential.helper=`, `protocol.allow=never`
- `diff.external=`, `core.attributesFile=/dev/null`

Repository-defined drivers are neutralised by first running `git config --list --name-only` with the same hardened env (listing config executes nothing; includes and a linked worktree's `config.worktree` are resolved). Every key matching `filter.<x>.(clean|smudge|process)`, `diff.<x>.(command|textconv)` or `merge.<x>.driver` is then overridden with `-c key=`, and `filter.<x>.required=false` is added. Diff commands always get `--no-ext-diff --no-textconv --no-color`.

Sensitive exclusion uses pathspec magic `:(exclude,glob)**/<pat>` per pattern, plus `:(exclude).git`. The diff output is post-filtered: hunks whose `diff --git a/X b/Y` header names a sensitive path are dropped. `git.status` uses `--porcelain=v2 -z --branch`, and `git.log` uses `--no-patch` with a NUL-separated `--format`. The repo top-level check in `repo.py` uses the same builder.

Alternative considered: `GIT_ATTR_SOURCE` set to the empty tree. It doesn't cover `.git/info/attributes` and needs git ≥ 2.40, so the key-override approach is used, with tests as the backstop.

### D10. Search
With ripgrep, the argv is `rg --no-config --json --no-follow --hidden --max-columns 500 --max-count <n> --glob '!.git' --glob '!<pat>'… [--fixed-strings] -e <pattern> -- <safe subpath>`. `--pre` and `-z` are never passed, and `RIPGREP_CONFIG_PATH` is not in the env allowlist. Results are parsed from JSON, re-checked against the sensitive matcher and the nested-boundary check, and capped. rg doesn't recognise a `.git` file as a boundary, so matches from nested worktrees are dropped in this post-filter. The fallback walker prunes those directories instead, and so does `filesystem.list`, which reports them as entries without descending.

The fallback is a pure-Python `os.walk(followlinks=False)` over the safe subpath, skipping `.git`, sensitive files, files over a size limit and files with NUL bytes in their first 8 KiB. It uses `re` (pattern-length bounded) or literal matching, with the same caps. The fallback is selected by `shutil.which("rg")` at startup, and the choice is recorded in audit `detail`.

Catastrophic-regex risk in the fallback is bounded by the wall-clock deadline checked per file. The rg path is bounded by the backend timeout.

### D11. MCP adapter
The low-level `mcp.server.lowlevel.Server` is used instead of FastMCP, so tool schemas come straight from the pydantic models and one generic `call_tool` dispatches everything. `list_tools` maps the registry to `Tool(name=cap.name.replace(".", "_"), inputSchema=cap.input.model_json_schema())`. `call_tool(name, args)` makes one `broker.execute(mcp_to_cap.get(name, name), args)` call and converts the `Result` into text content plus `isError`. An unlisted name is passed through unchanged, so the broker denies and audits it.

### D12. Packaging
uv project, `src/` layout, hatchling build, console script `repo-warden = repo_warden.cli:main`. Runtime dependencies:
- `mcp` (version that matches airlock's floor)
- `pydantic>=2`
- `pyyaml`
- `mcp-airlock==0.2.0`

A comment in pyproject names the fork fallback pin. Dev dependencies: `pytest`, `pytest-asyncio`. `examples/policy.yaml` ships in the repo.

## Risks / Trade-offs

- [Agent native tools bypass the broker] → The README states this plainly. Hard limits come from cplt and the agent host's user-level tool settings.
- [The audit log is writable from inside the sandbox] → The default path is outside the repo, the limitation is documented, and Postgres with an INSERT-only role is recommended as the stronger option.
- [The containment probe is heuristic] → Several independent probes are required. A marker alone is never trusted. The probe is described as a heuristic in the docs.
- [New git config keys could execute code] → A denylist of known executing keys and a key-override approach, plus fixture tests per vector. Unknown future vectors remain a risk, so git versions are tracked in tests.
- [The mcp-airlock import pulls in the HTTP stack, psycopg and otel] → Accepted for V1 and recorded as an upstream gap (lazy imports or a core extra).
- [Tier L3 is charged to the blast-radius window] → Read capabilities use L0 in the example policy, and the README explains this.
- [VS Code-hosted agents run MCP servers outside cplt] → The docs show `cplt exec -- repo-warden …` at user scope and state that the agent's native tools are then uncontained.
- [On Linux, cplt cannot block `.env` files inside the project directory (Landlock grants the project full read access; cplt warns about this, checked on 2026-10-03). A sibling checkout's `.env` is blocked] → The broker's sensitive-path rules deny them for broker calls, and the README states that native tools can still read them. Tracked files are considered part of the repo and are not guarded.
- [A nested worktree is writable from a sandbox started at the main checkout] → The docs recommend placing worktrees next to the main checkout and running one sandbox per worktree. The broker refuses to expose nested repositories and worktrees.
- [Python has no `openat2`] → The O_NOFOLLOW component walk from a root fd is used, with a test that swaps in a symlink after the check.

## Migration Plan

Not applicable (first release).

## Open Questions

- Exact cplt probe set: confirmed during implementation under `cplt exec`. Only the probes change, not the requirement.
- Default limits (read bytes, list entries, search matches, diff bytes, timeouts) are chosen in implementation and documented in the README. They can be tuned without spec changes.
