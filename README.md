# repo-warden

repo-warden is a capability broker for AI coding agents. One process is bound to exactly one git
repository. It exposes development actions (read a file, and later list, search and git) as MCP
tools, and decides each call with a policy that lives outside the repository and outside the LLM.
Every decision, including denials, is written to an audit log outside the repository.

It does not provide OS isolation. In the intended setup the agent, the broker and every child
process run inside a `cplt` sandbox, and repo-warden adds the semantic layer
on top: which development action this is, and whether it is allowed.

> Status: early development. Only `filesystem.read` exists so far, and only the `direct` backend
> starts; the contained `cplt` backend comes next.

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

```sh
uv run repo-warden --repo /path/to/your/repo --backend direct
```

`--repo` must be the top level of a git working tree. The broker serves MCP over stdio.
`--backend direct` runs child processes without verified OS containment and prints a warning to
stderr saying so.

Options:

| Option | Default |
|---|---|
| `--repo PATH` | required |
| `--policy FILE` | `$XDG_CONFIG_HOME/repo-warden/policy.yaml` |
| `--audit FILE` | `$XDG_STATE_HOME/repo-warden/audit.jsonl` (`~/.local/state/...`) |
| `--backend {cplt,direct}` | `cplt` (not available yet) |

Set `AIRLOCK_AUDIT_DSN` to a Postgres DSN to also write audit records to Postgres.

## Capabilities

| MCP tool | Capability | Limits |
|---|---|---|
| `filesystem_read` | `filesystem.read` | 256 KiB per read by default (`max_bytes` up to 1 MiB), then the policy output cap |

## License

MIT
