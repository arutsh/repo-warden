# devkit/agent-hooks Specification

## Purpose

Defines how the devkit plugs into coding agents' hook systems (Claude Code and OpenAI Codex CLI), how it translates their inputs and outputs, and how a user opts each agent in or out.

## Requirements

### Requirement: Claude Code adapter
`devkit hook claude pre-tool-use` and `devkit hook claude post-tool-use` SHALL read Claude Code's hook JSON on stdin and turn it into events:
- `Bash` → shell;
- `Read`, `Grep` and `Glob` paths → read;
- `Write` → write;
- `Edit` and `MultiEdit` → edit, with the before-content read from disk.

A block SHALL be emitted as a PreToolUse `permissionDecision: "deny"` with the reasons. Warnings SHALL be returned as additional context for the agent. The adapter SHALL NOT emit `permissionDecision: "allow"`, so the user's own permission prompts still apply.

#### Scenario: Blocked Bash command
- **WHEN** Claude Code sends a PreToolUse payload for `Bash` with `cat .env`
- **THEN** the output is a deny decision whose reason names `.env` and the `secrets` rule

#### Scenario: Allowed command
- **WHEN** the payload is `Bash` with `ls src`
- **THEN** the adapter prints nothing and exits 0

#### Scenario: Edit judged on the resulting file
- **WHEN** an `Edit` payload replaces one line in a file with a block of four comment lines
- **THEN** `comment_brevity` sees the file before and after the replacement and reports the new block

### Requirement: Codex CLI adapter
`devkit hook codex pre-tool-use` and `devkit hook codex post-tool-use` SHALL read Codex's hook JSON on stdin and turn it into events:
- `Bash` → shell;
- `apply_patch` → write events (added files) and edit events (updated files). The before-content SHALL be read from disk and the patch applied to it in memory.

Deleted files SHALL produce no content event. If a patch does not apply cleanly to the file on disk, the adapter SHALL judge the patch's old and new hunk text instead. A block SHALL be emitted as a PreToolUse `permissionDecision: "deny"`, and warnings as additional context. The adapter SHALL NOT emit an allow decision.

#### Scenario: Patch adding a long comment
- **WHEN** Codex sends `apply_patch` with an `*** Update File: src/app.py` hunk that adds four `#` lines
- **THEN** the output is a deny decision from `comment_brevity`

#### Scenario: Patch touching a secret file
- **WHEN** an `apply_patch` adds or updates `.env.local`
- **THEN** it is denied by `secrets`

#### Scenario: Stale patch context
- **WHEN** the patch's context lines do not match the file on disk
- **THEN** the hunks are still judged, using their old and new text

### Requirement: Hooks fail open and stay quick
If the devkit itself fails while handling a hook, the adapter SHALL allow the action and print a one-line warning naming the error. Examples of a failure: an unreadable payload, an internal exception, or an unreadable config. This is because in-agent hooks are early feedback, not the security boundary. A hook invocation SHALL NOT start network access or run repository code. It SHALL finish within the hook timeout written at install time.

#### Scenario: Malformed payload
- **WHEN** the adapter receives invalid JSON
- **THEN** it exits 0 without a decision and prints a warning to stderr

### Requirement: Installing and removing agent hooks
`devkit install --agent <list>` SHALL register the devkit's hooks in each named agent's user-level config:
- Claude Code: `~/.claude/settings.json`;
- Codex: `~/.codex/hooks.json`.

Each registered command SHALL use the absolute path of the installed `devkit` executable. The installer SHALL:
- recognise devkit-managed entries by their command;
- leave every other key and hook untouched;
- write atomically;
- keep a one-time backup of the original file;
- refuse to modify a file that is not valid JSON.

Running install again SHALL change nothing. `devkit uninstall --agent <list>` SHALL remove only devkit-managed entries. For Codex, the installer SHALL tell the user to review and trust the new hooks with Codex's `/hooks` command, and SHALL warn if Codex's config disables hooks.

#### Scenario: Idempotent install
- **WHEN** `devkit install --agent claude` runs twice
- **THEN** `~/.claude/settings.json` holds exactly one set of devkit entries, and the user's other hooks and permissions are unchanged

#### Scenario: Uninstall keeps user hooks
- **WHEN** settings.json contains a user-written PreToolUse hook and devkit's entries, and `devkit uninstall --agent claude` runs
- **THEN** only devkit's entries are removed

#### Scenario: Invalid JSON
- **WHEN** `~/.codex/hooks.json` is not valid JSON
- **THEN** install exits non-zero and leaves the file unchanged
