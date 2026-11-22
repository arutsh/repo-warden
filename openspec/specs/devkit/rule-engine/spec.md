# devkit/rule-engine Specification

## Purpose

Defines the agent-neutral events and decisions the devkit rules work on, and the behaviour of the four built-in rules: secrets, comment_brevity, test_factories and branch_name.

## Requirements

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

### Requirement: self_guard rule
The `self_guard` rule is a guard rule and SHALL default to `block`. It SHALL report a finding when the agent tries to switch devkit off, which means either of these:
- a shell command that runs `devkit uninstall`;
- a file write or edit to a file devkit relies on: the user config (`~/.config/devkit/config.yaml`, honouring `XDG_CONFIG_HOME`), or the agent hook files devkit installs to (Claude Code's `settings.json` and Codex's `hooks.json`, honouring `CLAUDE_CONFIG_DIR` and `CODEX_HOME`).

Paths SHALL be compared after resolving symlinks. Reading these files, `devkit status` and `devkit install` SHALL NOT be reported. The rule has no settings, so a repo can only raise its level. The reason SHALL tell the agent to ask the user to make the change themselves.

Like `secrets`, this is early feedback and not a security boundary: a command that doesn't spell out `devkit uninstall` gets past it.

#### Scenario: Agent uninstalls devkit
- **WHEN** the agent runs `devkit uninstall --agent claude`
- **THEN** the command gets a `self_guard` finding

#### Scenario: Agent edits a hook file
- **WHEN** the agent's write or edit targets `~/.claude/settings.json` or the devkit user config
- **THEN** it gets a `self_guard` finding

#### Scenario: Status and reads allowed
- **WHEN** the agent runs `devkit status` or reads `~/.claude/settings.json`
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

### Requirement: protected_branches rule
The `protected_branches` rule is a guard rule and SHALL default to `block`. It SHALL judge the agent's shell commands only. It SHALL NOT run under `devkit check` or the git pre-commit hook, so the user's own commits are never judged.

The protected branches SHALL be `main` and `master`, plus any names added by the `branches` setting. The rule SHALL report a finding when a command, in any of its `&&`, `||`, `;`, `|` or newline-separated parts, does either of these:
- runs `git commit` (with any options, `--amend` included) while the branch it would commit to is protected;
- runs `git push` that would update or delete a protected branch on a remote. That is:
  - a refspec whose destination is a protected branch (`main`, `+main`, `HEAD:main`, `feature:main`, `:main`, `refs/heads/main`);
  - `--delete` naming a protected branch;
  - `--all` or `--mirror`;
  - a push that names no branch, or names only `HEAD`, while on a protected branch.

A script passed to `sh`, `bash`, `zsh` or `dash` with `-c` (as Codex runs every command, `bash -lc '...'`) SHALL be judged the same way. Redirections such as `2>&1` and `> file` SHALL NOT split a part or count as arguments.

The branch a command acts on SHALL be read from the repo it runs in. That repo is named by `git -C <dir>`, or by an earlier `cd <dir>` in the same command, or else is the event's working directory. An earlier `git switch`, `git switch -c`, `git checkout <branch>` or `git checkout -b` in the same command SHALL change the branch that later parts are judged against.

Text that git or the shell treats as data SHALL NOT be judged as a command: heredoc bodies, and the arguments of `-m` or `-F`.

These SHALL NOT be reported:
- pushes of tags only;
- commits and pushes on branches that aren't protected;
- `gh pr merge`;
- a detached HEAD;
- a directory that isn't a git repository;
- a repo listed in `exempt_repos`.

The reason SHALL name the branch, and SHALL tell the agent to create a branch, commit and push there, and open a pull request. If the change must land on the protected branch directly, the reason SHALL tell the agent to ask the user to do it themselves.

Like `secrets`, this is early feedback and not a security boundary. A command that hides `git` (an alias, a script file, `eval`) gets past it.

#### Scenario: Commit and push on main
- **WHEN** the current branch is `main` and the agent runs `git add -A && git commit -m "x" && git push origin main`
- **THEN** the command gets a `protected_branches` finding naming `main`, and it is blocked

#### Scenario: Bare push while on main
- **WHEN** the current branch is `main` and the agent runs `git push`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Push to main from a feature branch
- **WHEN** the current branch is `feat/x` and the agent runs `git push origin HEAD:main`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Codex-style wrapped command
- **WHEN** the current branch is `main` and the agent's command is `bash -lc 'git commit -m x && git push origin main'`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Feature branch work allowed
- **WHEN** the current branch is `feat/x` and the agent runs `git commit -m "fix main menu" && git push -u origin feat/x`
- **THEN** there is no finding

#### Scenario: Branching off main first
- **WHEN** the current branch is `main` and the agent runs `git switch -c feat/y && git commit -m "y" && git push -u origin feat/y`
- **THEN** there is no finding

#### Scenario: Merging through GitHub
- **WHEN** the agent runs `gh pr merge 12 --merge`
- **THEN** there is no finding

#### Scenario: Repo adds a protected branch
- **WHEN** `.devkit.yaml` sets `protected_branches: { branches: [release] }` and the agent runs `git push origin release`
- **THEN** the command gets a finding

#### Scenario: Exempt repo
- **WHEN** the user config lists `~/dotfiles` in `exempt_repos` and the agent commits on `main` there
- **THEN** there is no finding
