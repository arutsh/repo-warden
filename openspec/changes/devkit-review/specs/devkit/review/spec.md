# Spec Delta

## Purpose

Defines devkit's end-of-turn review loop: when an agent's changes are reviewed before it stops, how many rounds run, how reviewers and fixes work, and how the user configures, installs and inspects it.

## ADDED Requirements

### Requirement: The review rule and its settings
devkit SHALL provide a style rule named `review`:
- its default level is `off`;
- it runs only in repos in scope, like every style rule;
- the repo layer may set it like any other style rule.

Its settings SHALL be:
- `max_rounds`: an integer from 1 to 5, default 3.
- `skip_paths`: glob patterns, default empty. A change that touches only matching paths is not reviewed.
- `reviewers`: a map from reviewer focus to model. The default is `correctness: opus`, `rules: sonnet`, `tests: sonnet`, `security: sonnet`, `history: sonnet`.
- `followup_reviewers`: the focuses that run in rounds 2 and later. The default is `[correctness]`.
- `checker`: the model that checks findings. The default is `opus`.
- `log`, `log_content` and `log_retention_days`: logging settings, defined by the `devkit/review-telemetry` capability.

The allowed reviewer focuses SHALL be `correctness`, `rules`, `tests`, `security` and `history`. The allowed model names SHALL be `opus`, `sonnet`, `haiku` and `inherit`. Any other focus, model name or out-of-range value SHALL be a config error that names the file and key.

#### Scenario: Off by default
- **WHEN** no config mentions `review` and the agent edits a file and stops
- **THEN** the stop is not blocked and no git command is run

#### Scenario: Invalid model
- **WHEN** `.devkit.yaml` sets `review: { level: block, checker: gpt }`
- **THEN** `devkit status` exits non-zero naming `.devkit.yaml` and `rules.review.checker`

#### Scenario: Not opted in
- **WHEN** the user config sets `review: block`, `repos` is `opted-in`, and the repo has no `.devkit.yaml`
- **THEN** no review is triggered in that repo

### Requirement: A review is triggered only for new, unreviewed changes
When `review` is at `block` and in scope, devkit SHALL compare snapshots of the working tree each time the agent tries to stop. The snapshot is the tracked and untracked, non-ignored files, except those matching the effective `secrets` patterns. It SHALL start a review only when all of these hold:
- the snapshot differs from the one taken when the current turn started;
- the snapshot has not already been reviewed in this repo;
- at least one changed path, compared with the last reviewed snapshot or else the session's baseline, does not match `skip_paths`.

Otherwise the stop SHALL go ahead with no output.

#### Scenario: Question-only turn
- **WHEN** the user asks a question and the agent answers without changing any file
- **THEN** the stop is not blocked and nothing is printed

#### Scenario: Turn that edits code
- **WHEN** the agent edits `src/app.py` during the turn and then stops
- **THEN** the stop is blocked and the agent is told to run review round 1 of 3

#### Scenario: Already reviewed
- **WHEN** a review loop has finished on the current snapshot, and in a later turn the agent changes a file and then restores it exactly
- **THEN** the stop is not blocked

#### Scenario: Pre-existing changes are not the trigger
- **WHEN** the working tree already had uncommitted changes when the session started, and the agent answers a question without editing
- **THEN** no review is triggered

#### Scenario: Docs-only change skipped
- **WHEN** `skip_paths` is `["**/*.md"]` and the turn changed only `README.md`
- **THEN** the stop is not blocked, and the snapshot counts as reviewed

### Requirement: Review rounds and how the loop ends
A review loop SHALL run at most `max_rounds` rounds:
- Round 1 SHALL cover the changes since the last reviewed snapshot in this session, or since the session's baseline when nothing has been reviewed yet.
- Each later round SHALL cover only the changes made since the previous round started.

When the agent tries to stop during a loop:
- if the round was recorded with zero fixes, the loop SHALL end and the stop SHALL go ahead;
- if the round was recorded with fixes and `max_rounds` hasn't been reached, the next round SHALL be requested;
- if `max_rounds` has been reached, the loop SHALL end and the stop SHALL go ahead.

If a requested round was never recorded, the loop SHALL end, the stop SHALL go ahead, and the user SHALL be told the round was not completed. A stop SHALL NOT be blocked more than `max_rounds` times in one turn. When a loop ends, its last snapshot SHALL count as reviewed, and the user SHALL see a one-line summary: rounds run, findings fixed, findings left for discussion.

#### Scenario: Clean second round
- **WHEN** round 1 is recorded with 2 fixes and round 2 is recorded with 0 fixes
- **THEN** the next stop goes ahead, and the summary says 2 rounds, 2 fixed

#### Scenario: Round limit
- **WHEN** `max_rounds` is 3 and rounds 1, 2 and 3 are each recorded with fixes
- **THEN** the stop after round 3 goes ahead, and no fourth round is requested

#### Scenario: Agent ignores the request
- **WHEN** round 1 is requested and the agent stops again without recording it
- **THEN** the stop goes ahead, and the user is told round 1 was not completed

#### Scenario: Follow-up scope
- **WHEN** round 2 is requested after round 1 changed `src/app.py` lines 10–12
- **THEN** `devkit review diff` for round 2 shows only the round-1 fixes

### Requirement: Stops that are never blocked
The review hooks SHALL let the stop go ahead, and run no review logic that could block, when any of these holds:
- the agent is in plan mode;
- the stop payload lists background tasks that are still running;
- the working directory is not in a git repository;
- the rule is `off` or not in scope.

All review hooks SHALL fail open. If a snapshot, the state file or the config can't be used, the stop SHALL go ahead and a one-line warning SHALL be printed.

#### Scenario: Plan mode
- **WHEN** the stop payload has `permission_mode: "plan"` and files changed
- **THEN** the stop is not blocked

#### Scenario: Snapshot failure
- **WHEN** git fails while taking the snapshot
- **THEN** the stop goes ahead and stderr carries a one-line devkit warning

### Requirement: Warn level
When `review` is at `warn`, a stop that would start a review SHALL go ahead. The user SHALL be shown, once per unreviewed snapshot, a message that there are unreviewed changes and that the `devkit-review` skill can review them. The agent SHALL NOT be made to continue.

#### Scenario: Warn does not continue the agent
- **WHEN** `review` is `warn` and the turn edited a file
- **THEN** the stop goes ahead, the user sees the unreviewed-changes message, and no block decision is printed

### Requirement: Snapshots don't leak secrets or run repository code
Snapshots and review diffs SHALL leave out every path that matches the effective `secrets` patterns. The git commands behind snapshots and `devkit review diff` SHALL:
- ignore system and global git config;
- turn off hooks, fsmonitor, pagers, network access, and every filter, diff and merge driver configured in the repo.

They SHALL NOT change any ref, the user's index or the working tree.

#### Scenario: Untracked env file
- **WHEN** an untracked, non-ignored `.env.local` is created during the turn
- **THEN** it is not in the snapshot, it doesn't trigger a review by itself, and it never appears in `devkit review diff`

#### Scenario: Hostile filter driver
- **WHEN** the repo's `.git/config` defines a `filter.x.clean` command and `.gitattributes` applies it to `*.py`
- **THEN** taking a snapshot does not run that command

#### Scenario: Index untouched
- **WHEN** the user has staged part of a file and a snapshot is taken
- **THEN** `git diff --cached` shows the same output before and after the snapshot

### Requirement: The devkit review command
`devkit review` SHALL provide these subcommands for the skill and the user:
- `plan`: prints the active round as JSON: round number, maximum rounds, the focuses to run with their models, the checker model, and whether a loop is active.
- `diff`: prints the diff the active round covers, with the `secrets` exclusions applied.
- `record`: reads the round's findings as JSON on stdin and records the round's outcome. Each finding gives its focus, model, file, line, claim, checker score and outcome (`fixed`, `discuss`, `question` or `dropped`, plus a reason when dropped). The fixed, open and dropped counts SHALL be derived from the findings. Input that doesn't parse SHALL be rejected with a non-zero exit, and nothing SHALL be recorded.
- `skip --reason TEXT`: marks the current snapshot as reviewed without a review. It is meant for when the user asked to skip, and the end-of-turn summary SHALL tell the user it was skipped and why.
- `reset`: clears this repo's review state.

`plan`, `diff`, `record` and `skip` SHALL exit non-zero, with a message, when no loop is active.

#### Scenario: Record outside a loop
- **WHEN** `devkit review record` runs with no active loop
- **THEN** it exits non-zero and records nothing

#### Scenario: Counts derived from findings
- **WHEN** `record` receives three findings with outcomes `fixed`, `discuss` and `dropped`
- **THEN** the round is recorded as 1 fixed, 1 open, 1 dropped

#### Scenario: Skip is visible
- **WHEN** the agent runs `devkit review skip --reason "user asked"` during round 1
- **THEN** the next stop goes ahead, and the user sees that the review was skipped with the reason "user asked"

### Requirement: The review skill's process
The `devkit-review` skill SHALL instruct the agent to run each round as follows:
1. Run `devkit review plan` and `devkit review diff` to get the round, the reviewers and the scope.
2. Start one reviewer subagent per focus, in parallel, each on its configured model:
   - Reviewers SHALL start with a fresh context, never a fork of the main conversation.
   - Each SHALL receive only: the intent of the change, the scope command, the project rules, and the list of things that don't count as findings.
   - None SHALL receive the main session's own explanation of why the code is right.
   - Reviewer tools SHALL be limited to reading files and running read-only git and `devkit review` commands.
3. Each finding SHALL carry a file, a line, a quote of the code, the claim, and a concrete failure scenario. A finding without them SHALL be dropped.
4. Each remaining finding SHALL be checked by an independent checker on the `checker` model. The checker sees only the finding and the code, and scores it against a fixed rubric. Findings below the threshold SHALL be dropped, and borderline findings SHALL be presented as questions, never as facts.
5. The main session SHALL fix only obvious findings. A finding is obvious only when all of these hold:
   - the fix is local;
   - it has one evident correct form;
   - it changes no behaviour beyond the defect;
   - it is covered by an existing or newly added test.

   After fixing, it SHALL re-run the relevant tests. It SHALL NOT commit, push or stage anything.
6. The main session SHALL list every other surviving finding for discussion, each with its evidence. If it disagrees with a finding that passed the check, it SHALL list the finding with its objection, not drop it.
7. The main session SHALL pass every finding to `devkit review record`, dropped ones included, with its outcome.
8. When the user later accepts or rejects a finding that was up for discussion, the main session SHALL record that with `devkit review verdict`.

The skill SHALL be runnable on demand outside the hook. In that case it reviews the changes since the last reviewed snapshot.

#### Scenario: Reviewer gets no main-session reasoning
- **WHEN** the skill starts the `correctness` reviewer
- **THEN** the reviewer's prompt contains the change intent, the scope command and the rules, and none of the main conversation

#### Scenario: Unsupported claim dropped
- **WHEN** a reviewer reports a finding with no file and line
- **THEN** it is passed to `record` with outcome `dropped` and the reason `no evidence`

#### Scenario: Non-obvious fix goes to discussion
- **WHEN** a confirmed finding would need an API change to fix
- **THEN** the main session does not change the code, and lists the finding for discussion

#### Scenario: No commit
- **WHEN** a round applies fixes
- **THEN** `git log` and the index are unchanged by the round

### Requirement: Installing the skill and reviewer agents for Claude Code
`devkit install --agent claude` SHALL do three things:
- register the `SessionStart`, `UserPromptSubmit` and `Stop` hooks, with the same entry-recognition, atomic-write and backup rules as the tool hooks;
- install the `devkit-review` skill under `~/.claude/skills/` (or under `$CLAUDE_CONFIG_DIR`);
- install one reviewer agent definition per focus, plus a checker agent definition, under `agents/` in the same directory.

Installed files SHALL be marked as devkit-managed. Install SHALL refresh managed files and SHALL refuse to overwrite an unmanaged file of the same name, with a message naming it. Running install again SHALL change nothing. `devkit uninstall --agent claude` SHALL remove only managed hooks, skills and agents.

#### Scenario: Idempotent skill install
- **WHEN** `devkit install --agent claude` runs twice
- **THEN** the skill and agent files are identical after both runs, and `settings.json` holds one set of devkit entries

#### Scenario: Foreign skill kept
- **WHEN** `~/.claude/skills/devkit-review/SKILL.md` exists and is not devkit-managed
- **THEN** install leaves it unchanged, reports it, and exits non-zero

### Requirement: Review state is visible
`devkit status` SHALL show the review rule's effective settings and their sources. In a repo with review state, it SHALL also show whether a loop is active, the current round, and when a review last completed.

#### Scenario: Active loop shown
- **WHEN** round 2 of 3 is active in the current repo and `devkit status` runs
- **THEN** the output shows the review loop as active at round 2 of 3
