# Spec Delta

## Purpose

Defines the agent-neutral events and decisions the devkit rules work on, and the behaviour of the four built-in rules: secrets, comment_brevity, test_factories and branch_name.

## ADDED Requirements

### Requirement: Rules judge normalised events
Every rule SHALL take a normalised event and return a decision. The event SHALL be one of:
- a shell command;
- a file read (including search and glob paths);
- a file write, with the full new content;
- a file edit, with the content before and after.

The decision SHALL be one of allow, warn or deny, with a human-readable reason. A rule SHALL NOT depend on which agent, git hook or CI job produced the event. A rule SHALL NOT run any program from the repository being checked.

#### Scenario: Same verdict from any source
- **WHEN** the same file edit reaches the engine from the Claude adapter, the Codex adapter and `devkit check`
- **THEN** each rule returns the same decision and reason for all three

#### Scenario: Unrelated event
- **WHEN** a rule receives an event kind it does not judge (for example `branch_name` receives a file read)
- **THEN** it returns allow

### Requirement: Levels turn rule findings into outcomes
Each rule finding SHALL be turned into an outcome by that rule's effective level:
- `off`: the rule is not run;
- `warn`: the finding is reported and the action proceeds;
- `block`: the action is refused.

When several rules report findings on one event, the strictest outcome SHALL win, and every finding SHALL be reported.

#### Scenario: Warn does not block
- **WHEN** `comment_brevity` is at `warn` and an edit introduces a five-line comment
- **THEN** the edit proceeds and the finding is shown to the agent or user

#### Scenario: Strictest wins
- **WHEN** one rule warns and another blocks the same event
- **THEN** the event is blocked and both findings are reported

### Requirement: secrets rule
The `secrets` rule is a guard rule and SHALL default to `block`. It SHALL report a finding when any of these refer to a file matching a secret pattern:
- a shell command;
- a file read;
- a file write;
- a file edit.

Matching SHALL be on the file's basename and case-insensitive. The default patterns SHALL be the same list repo-warden uses for sensitive paths (`.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `id_*`). Configuration can add patterns, and only the user config can add exemptions.

In a shell command, a reference SHALL be detected as a separate word or a path component. The reason SHALL tell the agent not to print the file and to ask the user instead.

#### Scenario: Shell read of .env
- **WHEN** the agent runs `cat .env`, `grep KEY config/.env.local` or `head ./certs/server.KEY`
- **THEN** the command gets a `secrets` finding

#### Scenario: Agent read tool
- **WHEN** the agent's own file-read or search tool targets `.env.production`
- **THEN** the read gets a `secrets` finding

#### Scenario: Not a reference
- **WHEN** the command is `echo environment` or reads `docs/dotenv.md`
- **THEN** there is no finding

#### Scenario: User exemption
- **WHEN** the user config exempts `.env.example` and the agent reads `.env.example`
- **THEN** there is no finding

### Requirement: comment_brevity rule
The `comment_brevity` rule SHALL default to `block`. It SHALL report a finding when a write or edit introduces one of the following, in a file whose type it knows (Python, shell, YAML, Ruby, and C-family and JS/TS sources):
- a run of three or more consecutive line comments of the same style;
- a block comment of three or more lines;
- a Python docstring of three or more lines.

Comment runs and docstrings that already existed before the change SHALL NOT be reported. Python SHALL be analysed with a tokenizer, so that string literals are not mistaken for comments. For files that don't parse, matching SHALL fall back to patterns. The reason SHALL name the line and say that longer rationale belongs in the commit message, PR or docs.

#### Scenario: New long comment
- **WHEN** an edit to `app.py` adds four consecutive `#` comment lines
- **THEN** the edit gets a finding naming the first line

#### Scenario: Existing long comment untouched
- **WHEN** a file already has a five-line docstring and an edit changes code elsewhere
- **THEN** there is no finding

#### Scenario: String is not a comment
- **WHEN** a Python edit adds a multi-line string literal containing `#` lines that is not a docstring
- **THEN** there is no finding

#### Scenario: Unknown file type
- **WHEN** an edit adds a long comment to a `.md` file
- **THEN** there is no finding

### Requirement: test_factories rule
The `test_factories` rule SHALL default to `off`. When on, after a write or edit to a Python file under a `tests` directory, it SHALL report a finding in either case:
- the file constructs a model directly although a factory for that model exists in the nearest `tests/factories/` directory;
- the file calls a function listed in the rule's configured `discouraged_calls`.

It SHALL never block: `block` SHALL be treated as `warn`.

#### Scenario: Raw model construction
- **WHEN** `tests/factories/users.py` defines `UserFactory` for model `User`, and an edited test file calls `User(...)`
- **THEN** a warning suggests `UserFactory.build(...)`

#### Scenario: No factories directory
- **WHEN** the edited test file has no `tests/factories/` above it
- **THEN** there is no finding

### Requirement: branch_name rule
The `branch_name` rule SHALL default to `off`. When on, it SHALL report a finding in either case:
- a shell command creates a branch (`git checkout -b`, `git switch -c`, or `git branch <name>`) whose name does not match the configured pattern;
- under `devkit check`, the current branch does not match.

Branch deletion and other flags SHALL NOT be treated as names. `main` (or the configured base branch) SHALL be accepted.

#### Scenario: Non-conforming branch
- **WHEN** the pattern is `^(feat|fix)/.+$` and the agent runs `git switch -c tmp`
- **THEN** the command gets a finding quoting the pattern

#### Scenario: Deleting a branch
- **WHEN** the agent runs `git branch -d old`
- **THEN** there is no finding
