# devkit

devkit applies one set of dev-workflow rules across all your repos and coding agents. The rules
cover things like not reading secret files and keeping code comments short. The same rule code runs as:

- an **agent hook** in Claude Code and OpenAI Codex CLI;
- a **git pre-commit hook** and a **CI check** (coming in a later group).

devkit is a separate package that lives in the repo-warden repository. repo-warden (the broker) does not depend on it,
and installing repo-warden installs nothing from devkit. Everything is opt-in.

> Status: groups 1–2 of `devkit-core`. One rule (`secrets`), and the Claude Code and Codex CLI adapters.

## Not a security boundary

In-agent hooks give **early feedback**. They don't contain anything:

- Matching a shell command against file names can be evaded, for example with `cat .e""nv`,
  a short Python script, or a variable.
- Hooks **fail open**: if devkit itself breaks, the action is allowed with a warning, so a bug
  never freezes your agent.

The layers that enforce are:

- the sandbox (cplt's deny rules);
- repo-warden's sensitive paths, which are the same default patterns as the `secrets` rule;
- in later groups, the pre-commit hook and CI.

## Install

```sh
uv tool install ./devkit                 # from a repo-warden checkout; adds `devkit` to ~/.local/bin
devkit install --agent claude            # registers the hooks in ~/.claude/settings.json
devkit install --agent codex             # registers the hooks in ~/.codex/hooks.json
devkit status                            # what applies here and why
```

For development, use `uv tool install --editable ./devkit`, or `uv run devkit ...` inside the checkout.

`devkit install` writes the absolute path of the `devkit` it was run as. It changes only its own
entries: your other hooks, permissions and settings are kept. It writes atomically, keeps the
original file as `<file>.devkit-backup` (for example `settings.json.devkit-backup`) the first time, and refuses to touch a file
that isn't valid JSON. Running it again changes nothing. Claude Code reads hooks at start-up, so
restart open sessions (or check them with `/hooks`).

If `CLAUDE_CONFIG_DIR` is set, devkit uses `$CLAUDE_CONFIG_DIR/settings.json`. If `CODEX_HOME`
is set, it uses `$CODEX_HOME/hooks.json`.

**Codex only runs hooks you have trusted.** After `devkit install --agent codex`, open Codex and run
`/hooks` to review and trust the devkit entries. Until you do, Codex skips them. Trust is tied to
the hook's content, so you need to trust again after a reinstall that changes the devkit path.
`devkit install` and `devkit status` warn if Codex's `config.toml` (in `~/.codex` or `$CODEX_HOME`) turns hooks off with
`[features] hooks = false`.

## Opting in and out

| Level | In | Out |
|---|---|---|
| Package | `uv tool install ./devkit` | `uv tool uninstall repo-warden-devkit` |
| Agent | `devkit install --agent claude,codex` | `devkit uninstall --agent claude,codex` |
| Repo | add `.devkit.yaml` at the repo's top level (or set `repos: all`) | delete it |
| Rule | `level: warn` or `block` | `level: off` |

Guard rules (`secrets`) run in every repo, and outside repos too, once the agent hook is installed.
Style rules (from a later group) run only in opted-in repos, unless the user config sets `repos: all`.

There is deliberately **no environment variable** that switches devkit off. The agent could set it
for its own commands. To switch devkit off, use the user config or `devkit uninstall`.

## Configuration

Settings are layered:

1. built-in defaults;
2. the user config, `$XDG_CONFIG_HOME/devkit/config.yaml` (default `~/.config/devkit/config.yaml`);
3. the repo config, `.devkit.yaml` at the repo's top level.

```yaml
# ~/.config/devkit/config.yaml
repos: opted-in            # or: all (user config only)
rules:
  secrets:
    level: block           # off | warn | block; a bare level is shorthand: `secrets: block`
    patterns: ['*.tfvars', '.devrc']   # added to the defaults
    exempt: ['.env.example']           # user config only
```

```yaml
# <repo>/.devkit.yaml (its presence opts the repo in)
rules:
  secrets:
    patterns: ['*.age']
```

The agent can edit the repo file, so **a repo can only tighten a guard rule**. It can raise
the level and add patterns. It can't lower the level, add exemptions or change `repos`. Such
settings are ignored, and `devkit status` lists them.

If a config file is invalid, `devkit status` exits non-zero and names the file and key. Hooks
skip the broken file, say so in their output, and keep enforcing with the remaining layers.

`devkit status` shows each rule's effective level, its settings, and the layer each one came from,
plus whether the repo is opted in and which agents have hooks installed.

## Rules

### `secrets` (guard, default `block`)

Refuses shell commands, file reads (including search paths and globs), writes and edits that
refer to a file matching a secret pattern. Matching is on any path component, case-insensitive.
The defaults are the same as repo-warden's sensitive paths: `.env`, `.env.*`, `*.pem`, `*.key`,
`*.p12`, `*.pfx`, `id_*`. A search glob counts as a reference when it could select such a file,
for example `**/.env*`, but `*` or `**/*` on its own does not. The block message tells the agent to
ask you instead. It never suggests a "presence check" such as `grep -c`, because that command would
itself reference the file.

A Grep over a whole directory can still read a secret file the search tool doesn't skip. The
sandbox and repo-warden cover that case.

## Claude Code mapping

| Claude tool | devkit event |
|---|---|
| `Bash` | shell command |
| `Read`; `Grep` `path`/`glob`; `Glob` `pattern`/`path` | read |
| `Write` | write (before-content read from disk) |
| `Edit`, `MultiEdit` | edit (before and after the replacement) |

A block is returned as a PreToolUse `deny` with the reason. A warning is returned as a
`systemMessage` plus `additionalContext`. devkit never answers `allow`, because that would skip your
own permission prompts.

## Codex CLI mapping

| Codex tool | devkit event |
|---|---|
| `Bash` | shell command (an argv list is joined first) |
| `apply_patch`, `*** Add File` | write |
| `apply_patch`, `*** Update File` | edit: the patch is applied in memory to the file on disk |
| `apply_patch`, `*** Move to` | edit at the new path; `secrets` also checks the old path |
| `apply_patch`, `*** Delete File` | no content; `secrets` checks the path |

If an update's context lines don't match the file on disk, rules see the hunks' own old and new
lines instead. If the patch can't be parsed at all, `secrets` still checks the file names in its
text. Codex can't read files except through `Bash`, so there is no separate read event.

devkit registers PreToolUse for `^(Bash|apply_patch)$`. Output is the same JSON as for Claude Code:
a PreToolUse `deny` with the reason, or `additionalContext` for a warning, and never `allow`.

## PostToolUse

devkit registers a PostToolUse hook (Claude `Write|Edit|MultiEdit`, Codex `^apply_patch$`) only
when a rule runs after the tool. The `secrets` rule doesn't, so today only PreToolUse is
installed. Each registered hook starts a process on every matching tool call. When a later version
adds such a rule, run `devkit install` again.

## Development

```sh
uv sync --all-packages
uv run --package repo-warden-devkit pytest devkit/tests
```
