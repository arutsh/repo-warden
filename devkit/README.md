# devkit

devkit applies one set of dev-workflow rules across all your repos and coding agents. The rules
cover things like not reading secret files and keeping code comments short. The same rule code runs as:

- an **agent hook** in Claude Code and OpenAI Codex CLI;
- a **git pre-commit hook** and a **CI check** (`devkit check`).

devkit is a separate package that lives in the repo-warden repository. repo-warden (the broker) does not depend on it,
and installing repo-warden installs nothing from devkit. Everything is opt-in.

> Status: `devkit-core` complete. Five rules (`secrets`, `self_guard`, `comment_brevity`,
> `test_factories`, `branch_name`), the Claude Code and Codex CLI adapters, `devkit check` with a
> git pre-commit hook and a CI workflow, and `devkit init`.

> [!WARNING]
> **CODEX CLI SUPPORT IS INCOMPLETE.** The Codex adapter has never been run against a real
> Codex session, only against Codex's documented hook format. Some protections that work in
> Claude Code don't work in Codex yet. See [Codex: known gaps](#codex-known-gaps). Use Claude
> Code if you need devkit's rules to hold.

## Not a security boundary

In-agent hooks give **early feedback**. They don't contain anything:

- Matching a shell command against file names can be evaded, for example with `cat .e""nv`,
  a short Python script, or a variable.
- Hooks **fail open**: if devkit itself breaks, the action is allowed with a warning, so a bug
  never freezes your agent.

The layers that enforce are:

- the sandbox (cplt's deny rules);
- repo-warden's sensitive paths, which are the same default patterns as the `secrets` rule;
- the pre-commit hook and CI ([`devkit check`](#repo-checks-devkit-check)), which fail closed.

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
| Git hook (per repo) | `devkit install --git-hooks` | `devkit uninstall --git-hooks` |
| Repo | `devkit init`, which writes `.devkit.yaml` (or set `repos: all`) | delete `.devkit.yaml` |
| Rule | `level: warn` or `block` | `level: off` |

Guard rules (`secrets`, `self_guard`) run in every repo, and outside repos too, once the agent hook is installed.
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
plus whether the repo is opted in, which agents have hooks installed, and whether the repo's git
pre-commit hook is devkit's.

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

**Known limitation: folders above the repo count too.** For reads, writes, edits and `devkit check`,
every folder in the file's absolute path is matched, not only the part inside the repo. So a clone at
`~/id_projects/app` makes every file match `id_*`: each commit is refused, each CI run fails and
each agent edit is denied. A Python virtualenv named `.env` inside the repo also matches, which is
arguably intended. Until this is fixed, clone into a folder whose path matches no secret pattern,
or add the folder's name to `exempt` in your user config. A later patch will match only the
repo-relative part (or only the basename, as the spec words it).

| Setting | Default | Notes |
|---|---|---|
| `patterns` | `[]` | added to the defaults; the repo config can add patterns too |
| `exempt` | `[]` | basenames never treated as secret, e.g. `.env.example`; user config only |

### `self_guard` (guard, default `block`)

Stops the agent switching devkit off. It refuses:

- a shell command that runs `devkit uninstall`;
- a write or edit to the devkit user config, Claude Code's `settings.json` or Codex's `hooks.json`.
  These are the paths devkit itself uses, so `XDG_CONFIG_HOME`, `CLAUDE_CONFIG_DIR` and `CODEX_HOME`
  are honoured, and symlinks are resolved. **In Codex, only updates are refused, not deletes or
  moves.** See [Codex: known gaps](#codex-known-gaps).

Reading those files, `devkit status` and `devkit install` are fine. To uninstall devkit or change
its config, do it yourself from your own terminal. The rule has no settings, and a repo can't turn
it off.

Like `secrets`, this is string matching. A command that doesn't spell out `devkit uninstall`, such as
`python -m devkit.cli uninstall` or a `sed` on the file, gets past it. For a hard lock, add a deny
rule to Claude Code's own permissions, for example `"deny": ["Bash(devkit uninstall*)"]`.

### `comment_brevity` (style, default `block`)

Refuses a write or edit that **introduces** a comment longer than `max_lines`:

- a run of consecutive line comments of the same style (`#` or `//`); banner lines such as `###` don't count.
  As a side effect, `///` lines don't count either, so Rust doc comments and TypeScript triple-slash
  comments are never checked;
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
`git -C repo checkout -b x` are not checked. `devkit check` also checks the current branch's name,
so the pre-commit hook or CI catches these later. A name that doesn't match `pattern` gets a finding
quoting the pattern.

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

### Codex: known gaps

> [!WARNING]
> **Codex is not fully supported.** These are known holes, not bugs that will surprise you later:
>
> - **Not tested against real Codex.** The hook format, the `/hooks` trust step and the
>   `apply_patch` envelope come from Codex's documentation. Nobody has yet checked them end to end.
> - **`self_guard` misses deletes and moves.** An `apply_patch` that runs `*** Delete File` on
>   `hooks.json`, or moves it away with `*** Move to`, is not refused. Only an update to the file is.
> - **`config.toml` is not protected.** The agent can add `[features] hooks = false` to Codex's
>   `config.toml` and switch every devkit hook off. `devkit status` warns about it afterwards, but
>   nothing stops it happening.

## Repo checks: `devkit check`

`devkit check` runs the same rules outside any agent:

```sh
devkit check --staged                    # staged changes against HEAD (the pre-commit hook runs this)
devkit check --range origin/main..HEAD   # the files changed on HEAD since it left main (CI)
```

Each added, modified or renamed file is judged by its content before and after the change, so a
file's old long docstring is not blamed on the commit that touches other code. A renamed file is
compared with its old content. A staged or changed file whose name matches a secret pattern gets a
`secrets` finding. When `branch_name` is on, the current branch's name is checked too. On a
detached HEAD, such as a pull request checkout in GitHub Actions, devkit uses `GITHUB_HEAD_REF`
and skips the check when that isn't set either. `--range` counts from the merge base, as a pull
request does, so commits that landed on the base branch afterwards are not included.

While a merge is in progress (when you commit after resolving conflicts), `--staged` compares with
only one parent, so the merged branch's changes look new. devkit therefore reports style findings
as warnings for that commit; guard rules such as `secrets` still block. A merge without conflicts
doesn't run the pre-commit hook at all.

Each finding is printed as `level  path:line  [rule] message`. The exit code is:

| Exit | Meaning |
|---|---|
| 0 | no finding at `block` (warnings may be printed) |
| 1 | at least one finding at `block` |
| 2 | the config is invalid (the file and key are named) or git failed |

Unlike the agent hooks, `devkit check` **fails closed**: a broken config stops the commit or fails
the CI job, because enforcing is what these entry points are for. `self_guard` doesn't run here,
because it is about what the agent does, not what is committed.

## Git pre-commit hook

```sh
devkit install --git-hooks      # in the repo; writes the repo's pre-commit hook
devkit uninstall --git-hooks    # removes it, only if devkit wrote it
```

The hook is a three-line shell script (shebang, a `# devkit-managed` marker, an `exec` line) that runs `devkit check --staged` with
the absolute path of the `devkit` it was installed with. It goes into the directory
`git rev-parse --git-path hooks` names, so in a linked worktree it lands in the shared hooks
directory and covers every worktree of the repo.

devkit only treats the hook as its own while it is exactly the script devkit wrote. If you edit it,
for example to add `npm test`, devkit treats it like any other hook: install refuses, uninstall
leaves it in place, and `devkit status` says it was edited. Remove or restore it yourself.

devkit refuses to install, and changes nothing, when:

- `core.hooksPath` is set, as with husky or a shared hooks directory;
- a `pre-commit` hook that devkit didn't write, or that you edited, already exists, as with the pre-commit framework.

In both cases, add `devkit check --staged` to your existing setup instead. For the pre-commit
framework, use a `local` hook with `entry: devkit check --staged`, `language: system`,
`pass_filenames: false`.

`git commit --no-verify` skips the hook, as it skips any hook. CI is the check you can't skip.

## Opting a repo in: `devkit init`

```sh
devkit init                     # writes a commented .devkit.yaml, which opts the repo in
devkit init --agents-md         # also AGENTS.md, and CLAUDE.md containing `@AGENTS.md`
devkit init --openspec          # also generic rules in openspec/config.yaml
devkit init --ci                # also .github/workflows/devkit.yml
```

The flags combine. `init` never overwrites a file: when one exists, it says so and leaves it alone.
With `--agents-md`, a `CLAUDE.md` you already have is kept, and `init` tells you to add `@AGENTS.md`
to it if it doesn't import it. With `--openspec`, a `rules:` block is appended only when
`openspec/config.yaml` has none. Otherwise, or when there is no openspec config yet, the suggested
rules are printed for you to merge by hand.

The CI workflow runs `devkit check --range "origin/<base>..HEAD"` on every pull request. It
installs devkit with `uvx` from this repository at `DEVKIT_REF`, which is `main` in the template.
Pin it to a tag or commit you trust. The repo config comes from the pull request itself, so if a
pull request relaxes a style rule in `.devkit.yaml`, the reviewer sees that change in the diff.

## Migrating GrandFlow

GrandFlow's four Claude hooks in `.claude/hooks/` are the rules devkit started from. To switch
GrandFlow over:

1. Install devkit and the agent hooks, if you haven't yet:
   `uv tool install ./devkit`, then `devkit install --agent claude,codex`, then `/hooks` in Codex.
2. In the GrandFlow checkout, run `devkit init` and `devkit install --git-hooks`.
3. Edit GrandFlow's `.devkit.yaml` to match its old hooks:

   ```yaml
   rules:
     comment_brevity: { level: block, max_lines: 2 }
     test_factories: { level: warn, discouraged_calls: [make_valid_user] }
     branch_name: { level: block, pattern: '<GrandFlow branch regex>', base: main }
     secrets: { patterns: ['.devrc', '.env.github-secrets'] }
   ```

   `.env.github-secrets` is already covered by `.env.*`. Add it anyway if you want the list to read
   like the old hook's. `.devrc` is not covered by the defaults.
4. Remove the four hook entries from GrandFlow's `.claude/settings.json`. Otherwise every rule runs
   twice.
5. Check with `devkit status` in GrandFlow: the repo is opted in and each rule shows `repo: .devkit.yaml`.

To roll back: `devkit uninstall --agent claude,codex --git-hooks` (run it in the repo for the git
hook), or restore `<file>.devkit-backup`.

## PostToolUse and file content

devkit registers a PostToolUse hook (Claude `Write|Edit|MultiEdit`, Codex `^apply_patch$`) because
`test_factories` runs after the tool. The hook is registered even while that rule is `off`, so
switching the rule on needs no reinstall. Each registered hook starts a process on every matching
tool call. If you installed with an earlier version that registered only PreToolUse, run
`devkit install` again.

A hook reads a file's content (before and after the edit) only when a rule that is enabled for that
repo needs it. That means `comment_brevity` in an opted-in repo, or `test_factories` when switched
on. Otherwise the event carries just the path, which is all `secrets` and `self_guard` use.

## Development

```sh
uv sync --all-packages
uv run --package repo-warden-devkit pytest devkit/tests
```
