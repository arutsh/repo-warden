# Design

## Context

GrandFlow has four Python hook scripts under `~/repos/GrandFlow/.claude/hooks/`. Each reads Claude's PreToolUse/PostToolUse JSON and prints a Claude-specific decision, and they are registered in GrandFlow's project `.claude/settings.json` by absolute path. The scripts mix three concerns: parsing Claude's input, the rule logic, and formatting Claude's output. Their behaviour is the baseline for the ported rules. Two things change in the port:
- the secret file list becomes patterns;
- the factory rule's hardcoded `make_valid_user` becomes config.

Codex CLI hooks (checked on 2026-10-11 against the Codex hooks docs) use the same structure as Claude's:
- They are configured in `~/.codex/hooks.json`.
- The events are `PreToolUse` and `PostToolUse`, with regex matchers.
- Input arrives on stdin, with `tool_name` and `tool_input`.
- A hook denies with `hookSpecificOutput.permissionDecision: "deny"` or by exiting 2.

File edits arrive as `tool_name: "apply_patch"`, with the patch text in `tool_input.command`. Non-managed hooks must be reviewed by the user with `/hooks` before they run.

repo-warden is a hatchling package at the repo root. It depends on mcp-airlock, which brings in a heavy import graph.

## Goals / Non-Goals

**Goals:**
- Hook start-up fast enough to run on every tool call: stdlib plus PyYAML, imported lazily where possible.
- The rule logic is written once and shared by the hook adapters and `devkit check`.
- Installing or removing the devkit never damages a user's agent config.

**Non-Goals:**
- Being a security boundary. Shell-string matching is bypassable by construction (D9).
- A plugin system for third-party rules.
- Rules that run project tools (linters, tests).
- Other agents. Adding one should only need a new adapter module.

## Decisions

### D1. Package layout
```
devkit/
  pyproject.toml            # name: repo-warden-devkit, script: devkit = devkit.cli:main
  src/devkit/
    events.py               # Event, Decision, Finding, Level
    engine.py               # run(events, config) -> Outcome
    config.py               # load + layer + validate; Scope
    repo.py                 # find top level by walking up for .git (no git subprocess in hooks)
    rules/{secrets,comment_brevity,test_factories,branch_name}.py
    adapters/{claude,codex}.py
    patch.py                # Codex apply_patch parser + in-memory apply
    install.py              # agent JSON configs + git pre-commit
    check.py                # devkit check (git subprocess allowed here)
    initcmd.py
    cli.py
  templates/                # devkit.yaml, AGENTS.md, openspec-rules.yaml, devkit-ci.yml
  tests/
```
The root `pyproject.toml` gains `[tool.uv.workspace] members = ["devkit"]`. The distribution name is `repo-warden-devkit`, which avoids a clash with existing PyPI names, and the import package is `devkit`. The alternative was an optional extra, `repo-warden[devkit]`. It was rejected because the hooks would then import from a package that pulls in mcp-airlock. It would also tie the devkit's release cycle to the broker's.

### D2. No import of repo_warden; parity by test
The `secrets` defaults copy `repo_warden.paths.DEFAULT_SENSITIVE`. A devkit test imports both (they share the workspace dev environment) and asserts the lists are equal, so they cannot drift. Importing repo_warden at hook time was rejected because of the start-up cost and the dependency direction.

### D3. Events and outcomes
`Event(kind, path=None, command=None, before=None, after=None, phase)`:
- `kind` is one of shell, read, write or edit;
- `phase` is pre or post.

Rules declare the phases they run in:
- `test_factories` runs at post (in hooks) and in check;
- the others run at pre and in check.

A rule returns findings (rule, message, line?). The engine applies levels and returns `Outcome(blocked: bool, findings)`. Adapters only translate. Every adapter treats "warn" the same way: a message reaches the agent, and there is never an allow decision, because emitting allow in Claude or Codex skips the user's permission prompt.

### D4. Config resolution
- **User file:** `${XDG_CONFIG_HOME:-~/.config}/devkit/config.yaml`.
- **Repo file:** `<top>/.devkit.yaml`.
- **Finding the top level:** walk up from the hook's `cwd`, or from the file's path for file events, to the first directory containing `.git` (a file or a directory). No git subprocess is run in hooks.

Schema:
```yaml
repos: opted-in            # user file only
rules:
  secrets: block           # shorthand for { level: block }
  comment_brevity: { level: warn, max_lines: 2 }
  test_factories: { level: warn, discouraged_calls: [make_valid_user] }
  branch_name: { level: block, pattern: '^(feat|fix|chore)/.+$', base: main }
  secrets:
    level: block
    patterns: ['*.tfvars']   # added to the defaults (both layers)
    exempt: ['.env.example'] # user layer only
```
Every rule's settings live under `rules.<name>`, so all rules share one shape. A bare level is shorthand for `{ level: ... }`. YAML 1.1 reads a bare `off` as `false`, so that is accepted as `off`.
Each rule has a `guard: bool` class attribute, and only `secrets` is a guard. Merging is per key:
- for guard rules, the repo layer can raise `level` (ordered off < warn < block) and extend `patterns`;
- any other guard-rule key in the repo layer is recorded as ignored;
- style rules merge normally.

`repos` and `exempt` in the repo file are recorded as ignored.

### D5. Claude adapter mapping
- **Bash:** `tool_input.command` becomes a shell event.
- **Read:** `file_path` becomes a read event. For **Grep** and **Glob**, the `path` and `glob`/`pattern` become a read event. A glob is matched against the secret patterns as text, so `**/.env*` is caught.
- **Write:** `content` becomes a write event.
- **Edit:** the before-content is read from disk. `old_string`→`new_string` is applied once, or everywhere with `replace_all`. If `old_string` is not found, the before-content is empty and the after-content is just `new_string`, as in GrandFlow.
- **MultiEdit:** each edit is applied in sequence.
- **Output:** a block is printed as `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":...}}`. A warning is printed as `{"systemMessage":..., "hookSpecificOutput":{"hookEventName":<event>,"additionalContext":...}}`.

Install writes:
- PreToolUse with matcher `Bash|Read|Grep|Glob|Write|Edit|MultiEdit`;
- PostToolUse with matcher `Write|Edit|MultiEdit`, only when a registered rule runs in the `post` phase (each hook is a process per tool call; re-run install after adding such a rule);
- timeout 15s.

File content (before/after) is read from disk only when a rule that reads it (`content = True`) is enabled for the event's repo by the effective config: level not `off`, and in scope for style rules. Otherwise write and edit events carry just the path. Hook registration still depends on the registered rules, not the config, so changing the config never needs a reinstall.

If an edit can't be placed in the file (a Claude `old_string` not found, a stale Codex hunk), the event is marked `fragment`. Rules then judge only the edit's own text, and line numbers in findings are left out because they wouldn't be the file's.

### D6. Codex adapter and apply_patch
`patch.py` parses the envelope: `*** Begin Patch`, then `*** Add File:`, `*** Update File:` (with an optional `*** Move to:`), `*** Delete File:`, hunks starting with `@@`, and the end marker. Each part is turned into events:
- **Add** becomes a write event.
- **Update** reads the file, locates each hunk by its context and removed lines in order, and applies it to get the after-content; the result is an edit event. If a hunk can't be located, the event falls back to before = the hunk's old side and after = the hunk's new side. comment_brevity's "introduced" comparison still works on those fragments.
- **Move** is judged for `secrets` on both paths.
- **Delete** becomes a shell-like path reference for `secrets` only.
- **An envelope that doesn't parse** becomes a shell event over the whole text, so `secrets` still sees the file names. The envelope is found anywhere in `tool_input.command`, which covers a heredoc around it.

Output uses the same JSON as Claude. Install writes these entries to `~/.codex/hooks.json` (or `$CODEX_HOME/hooks.json`):
- PreToolUse with matcher `^(Bash|apply_patch)$`;
- PostToolUse with `^apply_patch$`, under the same condition as for Claude (D5).

Install and `devkit status` warn when `config.toml` has `[features] hooks = false`, or the deprecated `codex_hooks = false`. The file is read with `tomllib`; nothing is written to the TOML.

### D7. Installer safety
- **Recognising devkit entries:** an entry is devkit's when its `command` starts with the absolute devkit path followed by ` hook <agent>`, or matches the regex `/devkit hook (claude|codex) ` to catch stale paths after a reinstall.
- **Writing:**
  1. read and parse the file, refusing on invalid JSON;
  2. drop the devkit entries;
  3. add the fresh ones;
  4. write to a temp file in the same directory, `fsync`, then `os.replace`;
  5. save `<file>.devkit-backup` on the first modification only.
- **git hooks:**
  - resolve the hooks directory with `git rev-parse --git-path hooks`, which handles linked worktrees;
  - refuse when `core.hooksPath` is set or a foreign `pre-commit` exists;
  - the script carries a `# devkit-managed` marker line and `exec`s the absolute devkit path.

### D8. Failure semantics differ by entry point
Hooks fail open: on any exception they print a warning on stderr and exit 0. This is because a broken devkit must not freeze every agent session, and the real enforcement is elsewhere. `devkit check` fails closed (exit 2), because pre-commit and CI exist to enforce. A config error therefore becomes visible in `devkit status` and `devkit check`. A hook only reports it, and keeps running with the remaining valid layers.

### D9. Secrets matching in shell strings
The command is split with `shlex`. If the quoting is broken, it falls back to splitting on whitespace and quotes. Each word is split on `/`, `=`, `:` and `<>|;&()` into candidate path components, and the basename of each candidate is matched case-insensitively against the patterns with `fnmatch`. This improves on GrandFlow's literal list: `.env.*` now covers the whole family, and `config/.env.local` is matched. It is still evadable (`cat .e""nv`, `python -c ...`, variables), and that is accepted. The README says so, and points to cplt's deny rules and repo-warden's sensitive paths as the enforcing layers.

Unlike GrandFlow, the block message no longer suggests `grep -c '^VAR=' .env`, because that command itself references `.env` and would be blocked. It tells the agent to ask the user.

### D10. Repo scope evaluation
For each event, the engine:
1. decides `in_scope` from `repos` and whether `.devkit.yaml` exists at the top level;
2. runs guard rules always;
3. runs style rules only when the event is in scope.

If an event has no repo (a path outside any git tree), only user-level config applies, and only guard rules run unless `repos: all`.

## Risks / Trade-offs

- [Agents change hook formats; Codex hooks are recent] → Adapters are small, and their tests use recorded payload fixtures. `devkit status` prints the detected agent versions where it can. A format drift fails open with a warning, not by blocking.
- [False positives from `secrets` interrupt work, e.g. `id_*` matching `id_generator.py`] → The user config can add `exempt` entries. The defaults stay aligned with repo-warden's choice to err toward hiding.
- [Per-call Python start-up (~50–100 ms) on every tool use] → Imports are lazy, there is no git subprocess in hooks, and rules for an event kind are only imported when needed.
- [The installer touches files the user edits by hand] → Atomic writes, a backup, refusing invalid JSON, and leaving other entries alone.
- [The repo can relax style rules, so an agent could edit `.devkit.yaml` to silence comment_brevity] → This is accepted: style rules are conventions. `devkit check` in CI reads the repo config from the PR, so a reviewer sees the change in the diff.

## Migration Plan

1. Install the devkit:
   - `uv tool install ./devkit` (or `uv tool install --editable ./devkit` while developing);
   - `devkit install --agent claude,codex`;
   - in Codex, approve the hooks with `/hooks`.
2. For GrandFlow, done by the user and documented in the README:
   - run `devkit init`;
   - set `branch_name` to GrandFlow's pattern and `test_factories` to `warn` with `discouraged_calls: [make_valid_user]`;
   - add `.devrc` and `.env.github-secrets` patterns if they aren't already covered;
   - remove the four hooks from GrandFlow's `.claude/settings.json`.
3. Rollback: `devkit uninstall --agent claude,codex --git-hooks`, or restore `<file>.devkit-backup`.

## Open Questions

- Whether a later adapter for Cursor or Gemini needs an extra event kind (for example MCP tool calls). Adding one should not change the existing rules.
