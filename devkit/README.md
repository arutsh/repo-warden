# devkit

devkit applies one set of dev-workflow rules across all your repos and coding agents. The rules
cover things like not reading secret files and keeping code comments short. The same rule code runs as:

- an **agent hook** in Claude Code and OpenAI Codex CLI;
- a **git pre-commit hook** and a **CI check** (coming in a later group).

devkit is a separate package that lives in the repo-warden repository. repo-warden (the broker) does not depend on it,
and installing repo-warden installs nothing from devkit. Everything is opt-in.

> Status: groups 1–3 of `devkit-core`. Four rules (`secrets`, `comment_brevity`, `test_factories`,
> `branch_name`), and the Claude Code and Codex CLI adapters.

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
Style rules (`comment_brevity`, `test_factories`, `branch_name`) run only in opted-in repos, unless the
user config sets `repos: all`.

There is deliberately **no environment variable** that switches devkit off. The agent could set it
for its own commands. To switch devkit off, use the user config or `devkit uninstall`. The `self_guard` rule
stops the agent doing either of those for you.

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
  comment_brevity: warn
```

```yaml
# <repo>/.devkit.yaml (its presence opts the repo in)
rules:
  secrets:
    patterns: ['*.age']
  comment_brevity: { level: block, max_lines: 2 }
  test_factories: { level: warn, discouraged_calls: [make_valid_user] }
  branch_name: { level: block, pattern: '^(feat|fix|chore)/.+$', base: main }
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

| Setting | Default | Notes |
|---|---|---|
| `patterns` | `[]` | added to the defaults; the repo config can add patterns too |
| `exempt` | `[]` | basenames never treated as secret, e.g. `.env.example`; user config only |

### `self_guard` (guard, default `block`)

Stops the agent switching devkit off. It refuses:

- a shell command that runs `devkit uninstall`;
- a write or edit to the devkit user config, Claude Code's `settings.json` or Codex's `hooks.json`.
  These are the paths devkit itself uses, so `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME`
  are honoured, and symlinks are resolved.

Reading those files, `devkit status` and `devkit install` are fine. To uninstall devkit or change
its config, do it yourself from your own terminal. The rule has no settings, and a repo can't turn
it off.

Like `secrets`, this is string matching. A command that doesn't spell out `devkit uninstall`, such as
`python -m devkit.cli uninstall` or a `sed` on the file, gets past it. For a hard lock, add a deny
rule to Claude Code's own permissions, for example `"deny": ["Bash(devkit uninstall*)"]`.

### `comment_brevity` (style, default `block`)

Refuses a write or edit that **introduces** a comment longer than `max_lines`:

- a run of consecutive line comments of the same style (`#` or `//`); banner lines such as `###` don't count;
- a block comment (`/* ... */`);
- a Python docstring (module, class or function).

Comments that were already in the file are not reported, so editing near an old long docstring is
fine. They are matched by text with indentation ignored, so moving or re-indenting one doesn't count
as introducing it. A shebang (`#!` on line 1) isn't a comment. Python is read with the tokenizer, so a string literal containing `#` lines is not a comment.
If a Python file doesn't parse, devkit falls back to matching patterns. Alembic migration headers
(module docstrings under `migrations/versions/`) are skipped. Known file types: Python, shell,
YAML, Ruby, and C-family and JS/TS sources (`.c .h .cpp .hpp .go .java .rs .js .jsx .ts .tsx`;
`.css` for block comments). Other files, such as Markdown, are never checked. The message names
the line and says that longer rationale belongs in the commit message, PR description or docs.
When the edit couldn't be placed in the file (see the mappings below), only the edit's own text is
judged, and the message leaves out the line number.

| Setting | Default | Notes |
|---|---|---|
| `max_lines` | `2` | the longest comment allowed; at least 1 |

### `test_factories` (style, default `off`, never blocks)

Runs **after** the tool (PostToolUse), on Python files under a `tests` directory (but not in
`factories`). It warns in two cases:

- the file constructs a model directly, as `User(...)`, while the nearest `tests/factories/`
  above the file declares a factory for it. The warning suggests `UserFactory.build(...)`;
- the file calls a function listed in `discouraged_calls`.

`block` is treated as `warn`: the edit has already happened, so the warning is feedback for the
agent's next step.

Paths count from the repo's top level, so a checkout under `~/tests/` or `~/factories/` is judged by
its own layout. The `tests/factories/` lookup stops at the repo's top level.

Factories are found with GrandFlow's pattern, which recognises only this form: a single-dot
`factory.*` base, with `class Meta:` directly after the class line and a bare model name.

```python
class UserFactory(factory.Factory):      # also factory.DjangoModelFactory etc.
    class Meta:
        model = User
```

It doesn't recognise `factory.django.DjangoModelFactory`, an imported base such as
`DjangoModelFactory`, a docstring before `Meta`, or `model = models.User`. A factory written in one
of those forms is silently not found.

| Setting | Default | Notes |
|---|---|---|
| `discouraged_calls` | `[]` | function names to warn about, e.g. `[make_valid_user]` |

### `branch_name` (style, default `off`)

Checks the name of a branch the agent creates with `git checkout -b`/`-B`, `git switch -c`/`-C` or
`git branch <name>`. Flags such as `git branch -d old` are not names, and neither are redirections
such as `git branch 2>/dev/null` or text on the next line. The base branch is always accepted.

Detection uses GrandFlow's pattern, so only these exact forms are recognised: the short flag right
after the subcommand, and the subcommand right after `git`. Branches created with
`git switch --create x`, `git checkout -q -b x`, `git checkout --track -b x` or
`git -C repo checkout -b x` are not checked. `devkit check` on the branch name catches these later
(planned in a later group). A name that doesn't match `pattern` gets a finding quoting the pattern.

| Setting | Default | Notes |
|---|---|---|
| `pattern` | `^(feat\|fix\|chore)/.+$` | a Python regular expression, searched in the name |
| `base` | `main` | always accepted |

An invalid setting (an unknown key, a wrong type, `max_lines: 0`, a `pattern` that isn't a valid
regular expression) is a config error, handled like any other: `devkit status` fails and a hook
skips that file.

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

## PostToolUse and file content

devkit registers a PostToolUse hook (Claude `Write|Edit|MultiEdit`, Codex `^apply_patch$`) because
`test_factories` runs after the tool. The hook is registered even while that rule is `off`, so
switching the rule on needs no reinstall. Each registered hook starts a process on every matching
tool call. If you installed with an earlier version that registered only PreToolUse, run
`devkit install` again.

A hook reads a file's content (before and after the edit) only when a rule that is enabled for that
repo needs it. That means `comment_brevity` in an opted-in repo, or `test_factories` when switched
on. Otherwise the event carries just the path, which is all `secrets` uses.

## Development

```sh
uv sync --all-packages
uv run --package repo-warden-devkit pytest devkit/tests
```
