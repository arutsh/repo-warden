# repo-warden

repo-warden is a capability broker for AI coding agents. One process is bound to exactly one git
repository. It exposes development actions (read a file, and later list, search and git) as MCP
tools, and decides each call with a policy that lives outside the repository and outside the LLM.
Every decision, including denials, is written to an audit log outside the repository.

It does not provide OS isolation. In the intended setup the agent, the broker and every child
process run inside a `cplt` sandbox, and repo-warden adds the semantic layer
on top: which development action this is, and whether it is allowed.

> Status: early development. Only `filesystem.read` exists so far.

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

Without cplt, for development only:

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

## Capabilities

| MCP tool | Capability | Limits |
|---|---|---|
| `filesystem_read` | `filesystem.read` | 256 KiB per read by default (`max_bytes` up to 1 MiB), then the policy output cap |

## License

MIT
