# repo-warden

repo-warden is a capability broker for AI coding agents. One process is bound to exactly one git
repository. It exposes read-only development actions (read, list and search files, git status,
diff and log) as MCP tools, and decides each call with a policy that lives outside the repository and outside the LLM.
Every decision, including denials, is written to an audit log outside the repository.

It does not provide OS isolation. In the intended setup the agent, the broker and every child
process run inside a `cplt` sandbox, and repo-warden adds the semantic layer
on top: which development action this is, and whether it is allowed.

> Status: Phase 1 (read-only). Six capabilities: `filesystem.read`, `filesystem.list`,
> `search.code`, `git.status`, `git.diff` and `git.log`. See the [ROADMAP](#roadmap).

## Install

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/) and `git`.

```sh
git clone <this repo> repo-warden
cd repo-warden
uv sync
```

## Policy

The policy is a YAML file **outside** any repository. By default the broker reads
`$XDG_CONFIG_HOME/repo-warden/policy.yaml` (falling back to `~/.config/repo-warden/policy.yaml`),
or the file given with `--policy`. It refuses to start if the file is missing, invalid, or
resolves inside the repository it serves.

```sh
mkdir -p ~/.config/repo-warden
cp examples/policy.yaml ~/.config/repo-warden/policy.yaml
```

The copy does not follow later changes to the example. When a release adds a capability, it stays
denied (`allowlist.deny`, "tool '…' is not allowlisted") until you add it under `tools:`. For
example, `filesystem.list` and `search.code` arrived after `filesystem.read`, and the three `git.*`
capabilities after those. The policy is read at startup, so restart the broker after editing it.

The format is [mcp-airlock](https://github.com/arutsh/mcp-airlock-broker) 0.2.0's policy model
plus an optional `repo_warden:` block (see [examples/policy.yaml](examples/policy.yaml)):

- Anything not listed under `tools:` is denied.
- `environment` is always forced to `local`.
- Tier `L0` allows. Read capabilities should use it, because it is not charged to the
  blast-radius window.
- Tier `L3` allows and is charged to the blast-radius window.
- Tier `L2` needs approval. There is no approver yet, so `L2` calls are denied.
- Tier `L1` is rejected when the policy is loaded.
- `output.max_chars` (global or per tool) caps what a call returns.
- `repo_warden.sensitive_paths_extra` adds to the built-in sensitive patterns (`.env`, `.env.*`,
  `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_*`). Sensitive paths and `.git/` are never exposed.
- `repo_warden.allow_direct_backend` authorises `--backend direct`, and is honoured only in the
  account policy (see [Startup checks](#startup-checks)).

### Startup checks

Two checks stop a rewritten command line (for example a repo-local MCP config edited by the
agent) from swapping the policy or downgrading the backend.

**Policy protection (cplt backend).** The sandbox, not file permissions, must stop the broker from
changing its policy. At startup the broker tries to open the policy file for writing, and to create
a file in its directory. Both must be refused with a permission or read-only error, **and** the
directory's mode bits must grant you write access. The last condition exists because the agent can
`chmod` files it created: a policy at 0444 in a 0555 directory under `/tmp` is refused, since the
denial then comes from modes the agent controls. These are real attempts, not `access(2)` queries,
which do not see Landlock. A probe file that gets created is removed. In practice, keep the policy
in `~/.config/repo-warden/` and grant that directory to cplt with `--allow-read` only, as in
[Run](#run). A policy anywhere the sandbox can write (`/tmp`, `~/.cache`, the repository) is
refused with `policy-protection check failed`.

**Direct-backend authorisation.** `--backend direct` starts only if
`repo_warden.allow_direct_backend: true` is set in `<home>/.config/repo-warden/policy.yaml`, where
`<home>` comes from the password database. That is the *account policy*. It is read separately from
the policy in use, and `--policy`, `XDG_CONFIG_HOME` and `HOME` cannot point it elsewhere, because a
rewritten command controls all three. Inside cplt that file is read-only, so a sandboxed process
cannot grant itself the downgrade. Outside cplt you own the file anyway. It defaults to false.

One-time setup for direct mode (development without cplt). Add this to
`~/.config/repo-warden/policy.yaml`:

```yaml
repo_warden:
  allow_direct_backend: true
```

Without it the broker refuses with `--backend direct is not authorised`.

## Run

Inside a cplt sandbox (the default `cplt` backend):

```sh
cd /path/to/your/repo
cplt --allow-exec /path/to/repo-warden \
     --allow-read ~/.config/repo-warden \
     --allow-write ~/.local/state/repo-warden \
     exec -- uv run --project /path/to/repo-warden repo-warden --repo .
```

cplt's project directory is the repository you serve, so the sandbox has to be told about
everything else the broker needs:

- `--allow-exec` lets it read and run the repo-warden checkout and its `.venv`, without being able to
  modify it.
- `--allow-read` lets it read the policy.
- `--allow-write` lets it write the audit log.

The repository must not live under a path the sandbox can write outside the project, such as
`/tmp`: the containment probe would then see a writable parent and refuse to start.

Inside cplt, a path under `~/.cache` is writable too, so neither the repository's parent nor the
policy may live there.

Without cplt, for development only (needs the [one-time authorisation](#startup-checks)):

```sh
uv run repo-warden --repo /path/to/your/repo --backend direct
```

`--repo` must be the top level of a git working tree. The broker serves MCP over stdio. The default
backend refuses to start unless containment is verified (see below). `--backend direct` runs child
processes without verified OS containment and prints a warning to stderr saying so.

Options:

| Option | Default |
|---|---|
| `--repo PATH` | required |
| `--policy FILE` | `$XDG_CONFIG_HOME/repo-warden/policy.yaml` |
| `--audit FILE` | `$XDG_STATE_HOME/repo-warden/audit.jsonl` (`~/.local/state/...`) |
| `--backend {cplt,direct}` | `cplt` |

Set `AIRLOCK_AUDIT_DSN` to a Postgres DSN to also write audit records to Postgres.

## Containment: broker vs cplt

repo-warden and cplt do different jobs, and the intended setup
uses both. The agent, the broker and every process the broker starts run in one cplt sandbox.

| | cplt (OS level) | repo-warden (semantic level) |
|---|---|---|
| Decides | which files, network and executables any process may touch | which development action an agent may take through the broker |
| Enforced by | the kernel (Landlock + seccomp on Linux, Seatbelt on macOS) | the broker's policy, path rules and audit |
| Covers the agent's native tools | yes | no: native shell and file tools bypass the broker |
| Repo-local secrets (`.env`) | not on Linux: the project dir is fully readable (cplt warns about this) | denied for every broker call |
| Audit trail | network log only | every call and decision |

The broker runs children (git, rg) directly. They inherit the sandbox, and each also gets an
allowlisted environment, an empty temporary `HOME`, stdin from `/dev/null`, a timeout that kills
its whole process group, capped output, and a working directory inside the repository.

### The containment probe

With the default `cplt` backend, the broker refuses to start unless all of these hold:

| Probe | Passes when |
|---|---|
| `marker` | `__CPLT_WRAPPED` is set |
| `seccomp` | `prctl(PR_GET_SECCOMP)` reports filter mode (2). Linux only. cplt denies `/proc`, so `/proc/self/status` cannot be used |
| `parent_write` | creating a file in the repository's parent directory is refused |
| `ssh` | `~/.ssh` (from the password database, not `$HOME`) is unreadable, if it exists |

The marker alone is never trusted. The probe is a **heuristic**: it shows that this process looks
like it is in a cplt sandbox with the expected restrictions. It does not prove the full policy.
Run it yourself with:

```sh
cplt exec -- uv run python -m repo_warden.execution --probe
```

It prints each probe and exits 0 only if all of them pass. `cplt check` shows what the sandbox
itself enforces.

### Optional: one worktree per change

To work on several changes at once, give each one a linked worktree **next to** the main checkout
(not inside it), with its own cplt sandbox and its own broker:

```sh
git -C ~/repos/app worktree add ../app-feature-x -b feature-x
cd ~/repos/app-feature-x
cplt --allow-exec /path/to/repo-warden --allow-read ~/.config/repo-warden \
     --allow-write ~/.local/state/repo-warden \
     exec -- uv run --project /path/to/repo-warden repo-warden --repo .
```

cplt's project directory is then the worktree, so the main checkout and sibling worktrees are not
writable from it, and the broker binds to the worktree. A worktree nested inside the main checkout
would be writable from a sandbox started at the main checkout, which is why worktrees go beside it.

## Registering with an MCP host

Register repo-warden at **user scope** only. Never put it in a workspace config such as
`.vscode/mcp.json` or a root `.mcp.json`:

- The agent can write to those files from inside the sandbox.
- VS Code starts workspace servers on the host, outside cplt, without a separate prompt once the
  workspace is trusted (Workspace Trust). An edited entry can therefore run any command
  uncontained, whatever server it claims to be.
- Do not trust workspaces that an agent writes to.

**CLI agents under cplt** (Claude Code, Copilot CLI, OpenCode, …). The agent runs inside cplt and
starts the broker itself, so the broker inherits the sandbox. For Claude Code:

```sh
claude mcp add --scope user repo-warden -- \
    uv run --project /path/to/repo-warden repo-warden --repo .
```

Then start the agent in the repository with the same grants as in [Run](#run):

```sh
cd /path/to/your/repo
cplt --agent claude --allow-exec /path/to/repo-warden \
     --allow-read ~/.config/repo-warden --allow-write ~/.local/state/repo-warden
```

`--repo .` is the agent's working directory, which is the repository. Even if the agent rewrote
the command line, the [startup checks](#startup-checks) keep it from swapping the policy or
falling back to `--backend direct`.

**VS Code.** VS Code runs MCP servers on the host, outside any sandbox the agent is in. Wrap the
broker in its own cplt sandbox in your user `mcp.json` (*MCP: Open User Configuration*):

```json
{
  "servers": {
    "repo-warden": {
      "type": "stdio",
      "command": "cplt",
      "args": ["--project-dir", "${workspaceFolder}",
               "--allow-exec", "/path/to/repo-warden",
               "--allow-read", "${userHome}/.config/repo-warden",
               "--allow-write", "${userHome}/.local/state/repo-warden",
               "exec", "--",
               "uv", "run", "--project", "/path/to/repo-warden", "repo-warden", "--repo", "${workspaceFolder}"]
    }
  }
}
```

The caveat: this contains the broker and its children, but **not** the VS Code agent's own native
tools (terminal, file edits), which stay uncontained. VS Code's per-server sandbox
(`sandboxEnabled` with `sandbox.filesystem` / `sandbox.network`, macOS and Linux) is a possible
alternative. The cplt backend refuses to start there (no `__CPLT_WRAPPED`), so it would mean
`--backend direct` plus `sandboxEnabled`. That combination is **unverified**: no probe checks what
VS Code's sandbox enforces. A `vscode` probe set is on the [ROADMAP](#roadmap).

## Threat model

repo-warden assumes that the agent, and any content in the repository, may be hostile. It
protects against an agent that uses the broker's tools to:

- reach files outside the repository (`..`, absolute paths, `~`, symlinks, including a symlink
  swapped in after the check), git internals, nested repositories, or sensitive files
- run code through the repository: git hooks, fsmonitor, clean/smudge/process filters, textconv,
  external diff, pager, credential helpers, SSH commands, signature programs, and per-worktree
  config (see [Git hardening](#git-hardening))
- pick another repository or session, which no capability can do
- pass shell syntax, which is never interpreted (argv only)
- get unbounded output, or leave no trace (every call, including denials, is audited)

The broker's policy is the per-call gate in every host, not the host's own controls:

- VS Code auto-approves tool calls to servers that run with `sandboxEnabled`.
- Client-side tool toggles and confirmations can be turned off by the user.
- Enterprise MCP controls (server allowlists) decide *which servers may run*, not *what a
  server's tools may do*. They complement the policy and do not replace it.

OS-level isolation (filesystem, network, the agent's own processes) is cplt's job, not
repo-warden's.

## Limitations

- **Native tools bypass the broker.** An agent with its own shell or file tools can do anything
  cplt allows, without going through the policy or the audit. Hard limits come from cplt and from
  the agent host's user-level tool settings.
- **The audit log is writable from inside the sandbox** (cplt has to grant it for writing). For a
  log the agent cannot alter, set `AIRLOCK_AUDIT_DSN` and use a Postgres role that may only
  `INSERT`.
- **Repo-local MCP configs are agent-writable.** See [Registering with an MCP
  host](#registering-with-an-mcp-host). repo-warden cannot fix this. The startup checks only limit
  what a rewritten broker command line can do.
- **On Linux, cplt cannot block `.env` files inside the project directory.** Landlock grants the
  project full read access, and cplt warns about this itself. Gitignored secrets in the repository
  are therefore readable by native tools; only broker calls deny them. (A sibling checkout's
  `.env` is blocked.) Tracked files are part of the repository and are not guarded by cplt.
- **The containment probe is a heuristic** (see [The containment probe](#the-containment-probe)).
- **ripgrep and symlink swaps.** See [Capabilities](#capabilities). With `--backend direct`, a
  concurrent swap can make ripgrep read outside the repository.
- **Git hardening is a denylist.** It covers the known executing config keys, with a test per
  vector. A future git version could add a new one.

## Capabilities

| MCP tool | Capability | Limits |
|---|---|---|
| `filesystem_read` | `filesystem.read` | 256 KiB per read by default (`max_bytes` up to 1 MiB), then the policy output cap |
| `filesystem_list` | `filesystem.list` | 500 entries by default (`max_entries` up to 5000), `depth` 1 by default (up to 8), then the policy output cap |
| `search_code` | `search.code` | 200 matching lines by default (`max_matches` up to 2000), 500 characters per line excerpt, files over 1 MiB skipped, 20 s timeout, then the policy output cap |
| `git_status` | `git.status` | 500 entries by default (`max_entries` up to 5000), 20 s timeout, then the policy output cap |
| `git_diff` | `git.diff` | 256 KiB by default (`max_bytes` up to 1 MiB), up to 32 `paths`, 20 s timeout, then the policy output cap |
| `git_log` | `git.log` | 20 commits by default (`max_count` up to 200), 20 s timeout, then the policy output cap |

Every capability takes paths relative to the repository root and refuses paths that leave it, go
through `.git`, match a sensitive pattern, or pass through a nested repository, worktree or
submodule (a directory below the root that holds a `.git` entry).

- `filesystem.list` walks breadth first, sorted by name, so a cut keeps the shallowest entries. It
  leaves out `.git` and sensitive entries, reports symlinks as `symlink` without following them, and
  reports a nested repository as an entry with `nested_repo: true` without listing its contents.
- `search.code` is literal unless `regex` is true, and takes an optional sub-path and file `glob`
  (a glob cannot start with `!`). It skips `.git`, sensitive, binary (a NUL in the first 8 KiB) and
  large files, never follows symlinks, and drops matches from nested repositories. It uses ripgrep
  when `rg` is on `PATH` at startup (with `--no-config`, so no repository or user ripgrep config,
  preprocessor or decompression applies), and a built-in walker otherwise, with a warning on
  stderr at startup. The audit record names the engine and counts the matches the post-filter
  dropped (`filtered`: `excluded` for git or sensitive paths, `nested` for nested repositories).
  These counts are never returned to the agent. ripgrep searches nested repositories and the
  post-filter drops their matches, so a high `nested` count next to `search timed out` or
  `truncated` means a large nested repository is eating the budget. ripgrep honours `.gitignore`
  and the walker does not, so the walker can return matches from ignored files. Glob syntax also
  differs: the walker uses `fnmatch`, where `*` crosses `/` and `{a,b}` is not expanded, though
  `**/` also matches zero directories as in ripgrep. Regex syntax is ripgrep's or that of the Python `regex` package
  (compatible with `re`), depending on the engine. The walker gives each line match the time left
  before the 20 s deadline, so a backtracking pattern ends as `search timed out`.
- ripgrep opens directories and files by path and follows a path that became a symlink after it
  was listed. The walker opens everything from the repository's root fd without following
  symlinks. So if something swaps a path for a symlink while ripgrep runs, ripgrep can read outside
  the repository, and that is held back only by the backend's containment. Under cplt the sandbox
  limits what it can reach. With `--backend direct` nothing does, so a process that writes to the
  repository while a search runs could expose files outside it.

- `git.status` returns the branch (`head`, `oid`, `upstream`, `ahead`, `behind`) and entries with
  the porcelain v2 `status` (`XY`, for example `.M` modified, `M.` staged, `??` untracked) and a
  `kind` (`changed`, `renamed`, `copied`, `unmerged`, `untracked`). Untracked directories are
  shown collapsed (`dir/`). Entries whose path, or original path for a rename, is sensitive are
  left out. Only the audit record counts them (`hidden`).
- `git.diff` diffs the working tree against the index, or with `staged: true` the index against
  `HEAD`. It is optionally limited to `paths`, each checked like any other path and passed to git
  literally, so pathspec magic such as `:(glob)` in a name is not interpreted. Sensitive files are
  excluded twice: by `:(exclude)` pathspecs, and by a post-filter that drops any file block whose
  header names a sensitive path. Submodule changes are not shown.
- `git.log` returns `hash`, `author`, `date` (ISO 8601) and `subject` for each commit, newest
  first, optionally for one `path`, and never patches.

### Git hardening

Every git call runs with the repository's and the user's executable configuration neutralised:

- No system or global config (`GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL=/dev/null`), no terminal
  prompt, no optional locks (so status does not rewrite the index), no lazy fetch, no replace
  objects, no pager.
- `-c` overrides on every call: `core.fsmonitor=false`, `core.hooksPath=/dev/null`,
  `core.sshCommand=false`, `credential.helper=`, `protocol.allow=never`, `diff.external=`,
  `core.attributesFile=/dev/null`, `log.showSignature=false`, and no submodule recursion.
- Before each call, `git config --list --name-only` (which executes nothing, and resolves includes
  and a linked worktree's `config.worktree`) finds every `filter.*.clean/smudge/process`,
  `diff.*.command/textconv` and `merge.*.driver` key. Each is overridden with an empty value, and
  every filter gets `required=false`. A driver name that `-c` cannot express makes the call fail
  closed.
- Diffs always use `--no-ext-diff --no-textconv --no-color`, and log uses `--no-show-signature`.

The tests build a repository in which each of these vectors creates a marker file, including a
linked worktree whose `config.worktree` sets `core.fsmonitor`. They check that each vector fires
with plain git, and that no marker appears through `git.status`, `git.diff` or `git.log`.

## Roadmap

Not in Phase 1:

- Write capabilities (`write_file`), then test, lint and typecheck runners, project inspection and
  dependency installation, each behind the policy and the audit.
- Approvals: a host-side approver for L2 calls (the `Approver` seam exists; V1 denies L2).
- A repo-level policy that can only tighten the global one (the stricter-only merge seam exists).
- `git commit` / `push`, and migrations.
- An HTTP transport, and Model B (the broker outside the agent's sandbox).
- A `vscode` probe set and backend for VS Code's per-server sandbox (`sandboxEnabled`), measured the
  same way the cplt probes were, so that it can replace "`--backend direct`, unverified".
- `where` argument rules in the policy once mcp-airlock releases them (see
  [docs/upstream-gaps.md](docs/upstream-gaps.md)).

## devkit

This repository also holds [devkit](devkit/README.md). It is a separate, opt-in package of
dev-workflow rules (secret files, comment style, …) that run as coding-agent hooks, git hooks and in
CI. repo-warden does not depend on it.

## License

MIT
