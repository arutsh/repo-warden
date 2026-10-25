# Design

## Context

This change builds on devkit-core (groups 1–3):
- **Rules** are classes in `devkit/rules/`, with `phases`, `kinds`, `guard` and `settings`.
- **`engine.evaluate`** loads the layered config per repo, skips rules that are `off` or out of scope, and collects findings.
- **The Claude adapter** maps `HOOK_EVENTS` to events and prints Claude's hook JSON. It never emits an allow decision.
- **The installer** recognises its entries by command, writes atomically and keeps a backup.
- **devkit-core D4** forbids git subprocesses in hooks, because they run on every tool call.
- **D2** forbids importing `repo_warden`.

Hook facts this design relies on, from the Claude Code hooks docs, checked 2026-10-24:
- A `Stop` hook can return `{"decision":"block","reason":...}`. Claude then continues, with the reason as its next instruction.
- The Stop input includes:
  - `stop_hook_active`;
  - `last_assistant_message`;
  - `background_tasks`, the in-flight tasks;
  - the common fields `session_id`, `cwd` and `permission_mode`.
- `hookSpecificOutput.additionalContext` on Stop also continues the conversation, so it can't be used as a warning that lets Claude stop.
- Claude Code overrides a block after 8 consecutive stop-hook continuations. The count resets on every tool call, so it is not a real limit on a loop.
- `type: "agent"` hooks are experimental and limited to read tools: Read, Grep, Glob.
- Subagents start with a fresh context, except forks. They load CLAUDE.md and AGENTS.md, and the Agent tool accepts a per-call `model` override.

For Codex (Codex hooks docs, 2026-10-24):
- `Stop` takes the same `decision: "block"` and `reason`, turning the reason into a new prompt.
- It also has `stop_hook_active` and `turn_id`.
- It must always print JSON when exiting 0.

## Goals / Non-Goals

**Goals:**
- No cost for turns that change nothing. In repos where `review` is off or out of scope, the hook does no more than config loading.
- The review loop is a deterministic state machine. The hook never judges code; the agent does the reviewing.
- Reviewers are independent of the author session, and every finding they report rests on evidence.
- Codex can be added later without changing the state machine or the config schema already shipped.

**Non-Goals:**
- Enforcement. The agent could run `devkit review record` without reviewing. As with every devkit hook, this is early feedback.
- Reviewing in CI, in pre-commit, or on GitHub PRs.
- Running linters or tests from the hook. The skill runs tests as part of fixing.

## Decisions

### D1. The hook only decides; the agent reviews
The `Stop` hook is a `type: "command"` hook, `devkit hook claude stop`. Its whole job is to decide whether to block, with "run review round N of M using the devkit-review skill" as the reason. The main session then starts the reviewers and makes the fixes.

**Alternatives rejected:**
- **A `type: "agent"` Stop hook:** it is experimental and Claude-only. Its subagent has read tools only, so it can't fix anything, and it can't start parallel reviewers.
- **A `type: "prompt"` hook:** it is a single LLM call that can't see the code.
- **Running `claude -p` from the hook:** a nested session that doesn't share the main context. It has its own cost, timeouts and sandbox problems, and it is Claude-only.
- **A pre-commit review:** too late, and slow on every commit.

### D2. Hook events and the event model
The adapter gains `session-start`, `user-prompt-submit` and `stop`. They map to `SessionStart`, `UserPromptSubmit` and `Stop`, with no matcher and a 30 s timeout.

They produce a new event kind, `turn`, with phase `session-start`, `prompt` or `stop`. `Event` gains two fields:
- `session_id`;
- `meta`: a read-only mapping holding `permission_mode`, `background_task_count` and `stop_hook_active`.

The `review` rule declares `kinds={"turn"}` and all three phases, so the engine's existing level and scope checks gate it before any git call. Its `check` records snapshots on `session-start` and `prompt`, and returns at most one finding on `stop`. Recording state inside `check` is a side effect. It is accepted so that `review` keeps the same config path, `devkit status` output and scope rules as every other rule.

**Output:**
- **Block at `stop`:** `{"decision":"block","reason":...}`. This is the existing non-PreToolUse branch of `response()`.
- **Warn at `stop`:** `systemMessage` only. It must not be `additionalContext`, because that would continue the turn.
- **End-of-loop summary and "round not completed" notices:** `systemMessage`.

`install.py` registers these three hook events only for Claude in this change.

### D3. Exception to "no git in hooks"
devkit-core D4 kept git out of hooks because tool hooks run on every tool call. The turn hooks run once per prompt and once per stop, and only after the engine has confirmed that `review` is on and the repo is in scope. So the review module may run git. The tool hooks are unchanged.

### D4. Snapshots are git tree objects built through a temporary index
To take a snapshot:
1. Copy the repo's index (`git rev-parse --git-path index`) to a temp file in the devkit cache directory.
2. With `GIT_INDEX_FILE` set to that copy, run `git add -A -- . <excludes>`.
3. Run `git write-tree`.

The tree id is the fingerprint, and it can be diffed directly with `git diff <tree> <tree>`. Because the index is a copy, its stat cache makes `add -A` cheap, and the user's index and refs are never touched. `git write-tree` adds unreferenced objects, which `git gc` removes eventually. If a stored tree id has been removed, the state falls back as if it had no baseline.

**Excludes:** every effective `secrets` pattern becomes `:(exclude,icase,glob)**/<pattern>`, plus the repo's ignore rules (which `add -A` already respects). So untracked `.env*` files never enter the object database or a diff.

**Hardening:** `devkit/gitsafe.py` copies `repo_warden.gitcmd`:
- the `GIT_ENV` variables;
- `BASE_OVERRIDES`;
- driver enumeration through `git config --list --name-only -z`, done on every call.

It runs synchronously with `subprocess`. Following D2, a parity test asserts that the env and overrides equal repo-warden's.

**Alternatives rejected:**
- **`git stash create`:** it leaves out untracked files and writes a commit object.
- **Hashing `git diff` output:** it can't be used to diff between rounds.
- **Copying files to the cache:** too slow, and it would copy secrets.

### D5. State file
Location: `${XDG_CACHE_HOME:-~/.cache}/devkit/review/<sha256(realpath(top))[:16]>.json`, outside the repo. Under cplt, `~/.cache` is writable.

```json
{"reviewed": ["<tree>", "..."],
 "sessions": {"<session_id>": {"baseline": "<tree>", "turn_start": "<tree>", "last_reviewed": "<tree>|null",
   "updated": "<iso>",
   "loop": {"round": 1, "max": 3, "from": "<tree>", "at": "<tree>",
            "record": {"fixed": 2, "open": 1, "dropped": 3} | {"skipped": "reason"} | null,
            "totals": {"fixed": 2, "open": 1}}}}}
```

- Access is serialised with an `fcntl` lock and written with temp file plus `os.replace`.
- `reviewed` keeps the last 100 trees. Sessions not updated for 7 days are pruned on write.
- A file that is unreadable or invalid is treated as empty, with a warning. This is the fail-open behaviour.
- `SessionStart` with `source: resume` keeps an existing entry for that session. Other sources reset `baseline`.

### D6. The state machine at `stop`
```
skip silently if: permission_mode == plan, background_task_count > 0
cur = snapshot()
s = session state (create with baseline = turn_start = cur if missing)
if s.loop:
    r = s.loop.record
    if r is None:                  end_loop(cur, note="round N not completed"); allow
    if r.skipped:                  end_loop(cur, note="review skipped: <reason>"); allow
    if r.fixed == 0 or s.loop.round >= s.loop.max: end_loop(cur, summary); allow
    s.loop = {round+1, from: s.loop.at, at: cur, record: None}; block(round+1)
if cur == s.turn_start or cur in reviewed: allow
base = s.last_reviewed or s.baseline
if every changed path in diff(base, cur) matches skip_paths: mark reviewed(cur); allow
level == warn: notify once for cur; allow
s.loop = {round 1, from: base, at: cur, record: None}; block(1)
```

`end_loop(cur)` does three things: it adds `cur` to `reviewed`, sets `last_reviewed = cur`, and clears the loop.

**Round scopes:**
- Round 1 covers `diff(base, at)`.
- Each later round covers `diff(previous at, new at)`, which is exactly the previous round's fixes.

Each request blocks once, and a missing record ends the loop. So a turn can be blocked at most `max` times, whatever the agent does. `stop_hook_active` is therefore not needed for correctness. It is logged for debugging only.

The `turn_start` check is what keeps question-only turns free. A turn that was interrupted never reaches Stop, so its changes are not reviewed then. They are picked up by the next turn that changes something, because round 1 covers everything since `last_reviewed`.

### D7. The `devkit review` command
Each subcommand finds the repo from the cwd and the session from the state's single active loop for that repo. If more than one session has an active loop, `--session` is required, and the hook's reason text always includes it.

- **`plan`** prints JSON:
  ```json
  {"round": 2, "max": 3, "session": "...", "reviewers": {"correctness": "opus"},
   "checker": "opus", "scope": "devkit review diff --session ..."}
  ```
  Round 1 lists every configured focus; later rounds list `followup_reviewers`.
- **`diff`** prints `git diff --stat` followed by the full diff for the round's `from..at`, using the D4 excludes and hardening.
- **`record`** reads a JSON array of findings on stdin, validates it, derives the counts, writes the round's record, and emits the log records (D10).
- **`skip`** writes a skipped record for the round.
- **`verdict`** appends a verdict log record.
- **`reset`** deletes the state file.

The skill never reads the YAML config; everything it needs comes from `plan`.

### D8. The skill and the agent definitions
The files ship as package data in `devkit/src/devkit/claude_assets/`:
- `skills/devkit-review/SKILL.md`;
- `agents/devkit-review-{correctness,rules,tests,security,history,checker}.md`.

Each agent definition sets:
- `tools: Read, Grep, Glob, Bash`, with no Edit, Write or NotebookEdit;
- a default `model`;
- a narrow, single-focus system prompt.

The skill passes the model from `plan` through the Agent tool's `model` parameter. So a repo's config can change the models without reinstalling.

**What each focus covers:**
- **correctness:** bugs in the changed lines, plus the code they call or that calls them.
- **rules:**
  - `AGENTS.md`, `CLAUDE.md` and `.devkit.yaml`;
  - the specs and tasks of the openspec change in progress, when one is active (`openspec list --json`), checked against the diff.
- **tests:** whether changed behaviour is tested, and whether the tests can actually fail.
- **security:** injection, path handling, secrets, trust boundaries. This matters in repo-warden itself.
- **history:** `git log` and `git blame` of the changed lines, for regressions against past fixes.

**Reviewer prompt contract.** The prompt the skill writes for each reviewer contains:
1. the change intent, in 1–3 sentences on *what* the change does, never *why it is correct*;
2. the scope command;
3. the list of things that don't count as findings;
4. the output format: one JSON object per finding with `file`, `line`, `quote`, `claim`, `failure_scenario` and `focus`.

**Not findings** (adapted from Anthropic's code-review plugin):
- issues that already existed before the change;
- issues on lines the change didn't touch;
- anything a linter, type checker or the tests would catch;
- nitpicks;
- changes that are clearly intentional;
- rules that the code silences on purpose.

**Checker.** One `devkit-review-checker` runs per finding, in parallel, at most 10 at a time. It gets the finding JSON and nothing from the reviewer's reasoning. It reads the code and scores the finding:

| Score | Meaning |
|---|---|
| 0 | doesn't hold up |
| 25 | couldn't confirm |
| 50 | real, but minor or rare |
| 75 | confirmed and will be hit |
| 100 | certain, and the code shows it |

What happens to a finding by score:
- 75 or more: it counts as confirmed.
- 50: it is shown as a *question*.
- Below 50: it is dropped.

The plugin uses a single threshold of 80. Splitting it here keeps uncertain findings visible without presenting them as facts.

**What the main session does:**
- merges duplicates;
- fixes the obvious findings (spec definition);
- re-runs the relevant tests;
- lists the rest, each with its evidence and any objection it has;
- pipes every finding, with its outcome, into `devkit review record`;
- shows each finding's id in the To discuss list, so a later `verdict` can refer to it.

Its final message has three parts: **Fixed**, **To discuss**, **Dropped: N** (with reasons grouped). The skill also works on demand (`/devkit-review`). With no active loop, it starts one through `devkit review plan --start`, so manual and hook-started runs share the same state.

### D9. Installing the skill and agents
`install --agent claude` copies the files into `$CLAUDE_CONFIG_DIR` or `~/.claude`, under `skills/` and `agents/`.

**Marking files as devkit's:**
- the skill directory holds a `.devkit-managed` file;
- each agent file carries the line `<!-- devkit-managed -->` after its frontmatter.

**Writing:**
- Each file is written atomically. A file whose content is unchanged is not rewritten, which keeps install idempotent.
- An existing file without the marker makes install fail for that file, naming it. Other files are still installed.
- Uninstall removes only marked files, and the skill directory when it is empty.

### D10. Review logs: OTel-shaped JSONL, no SDK
Claude Code already exports OTel (checked 2026-10-24 against its monitoring docs):
- `claude_code.cost.usage` and `claude_code.token.usage`, broken down by `model`, `query_source` (main or subagent), `agent.name` and `skill.name`;
- events such as `api_request`, `tool_result` and `subagent_completed`;
- `agent_id` and `parent_agent_id` on subagent events.

So devkit does **not** measure cost, tokens or durations. It records only what Claude can't know: loops, rounds, findings, scores, outcomes and verdicts.

**Format.** Records follow the OTel log data model as JSON lines: `timestamp` (RFC 3339 with nanoseconds), `severity_text`, `body`, `attributes`, `resource`. They are appended to a daily file under `${XDG_STATE_HOME:-~/.local/state}/devkit/review/`.

Each record is written as one line, in a single `write()` call, to a file opened with `O_APPEND`, while holding an `fcntl` lock. The lock keeps records from concurrent sessions from interleaving.

**Ids:**
- `finding_id` is `f-` followed by the first 8 hex characters of sha256 over loop_id, round, file, line and claim. It is stable, and short enough to type in `verdict`.
- `session.id` is the hook's `session_id`. If it matches Claude's own OTel `session.id`, which a task verifies, the dashboard can join devkit records to Claude's cost data.

**Alternatives rejected:**
- **Depending on `opentelemetry-sdk` and exporting OTLP from devkit:** it adds an import graph to processes that start on every stop, and it puts an endpoint into config that a repo could point somewhere else.
- **Writing into Claude's transcript:** not supported, and Claude-only.

The OTel Collector's `filelog` receiver with a `json_parser` operator is the standard way to ship JSON log files, so no code is needed on the devkit side.

**Settings that only the user config may set.** `log_content` must not be enabled by the repo layer: an agent-writable file shouldn't be able to widen what is logged. Today only guard rules support `repo_settings` (the settings the repo layer may set). The config gains a per-rule `user_only` set for style rules. A repo-layer value for a key in `user_only` is recorded as ignored, the same way as `exempt`.

### D11. Telemetry stack and dashboard
`devkit/telemetry/` holds:
- `compose.yaml`;
- `collector.yaml`;
- `dashboards/devkit-review.json`;
- `README.md`.

The compose file runs the `grafana/otel-lgtm` image, pinned to a tag at least two weeks old. That single container bundles an OTel Collector, Prometheus, Loki, Tempo and Grafana. It mounts:
- the devkit log directory, read-only;
- `collector.yaml`;
- the dashboard, provisioned.

Ports are published on `127.0.0.1` only:
- 3000 for Grafana;
- 4317 and 4318 for OTLP.

**Collector pipelines:**
- `filelog` (devkit JSONL) → Loki;
- OTLP from Claude Code: metrics → Prometheus, logs → Loki.

There is no other exporter.

**Dashboard panels:**
- **From the devkit records**, using LogQL over the parsed attributes:
  - loops, rounds per loop, and how loops ended;
  - findings by focus, by model and by outcome;
  - checker confirmation rate per focus and model: (confirmed + questions) out of all checked;
  - user acceptance rate: accepted verdicts out of all verdicts.
- **From Claude's metrics:** `claude_code_cost_usage` and `claude_code_token_usage`, filtered to `agent_name=~"devkit-review-.*"` and grouped by `agent_name` and `model`.

**Privacy trade-off.** Claude Code reports custom agent names as `"custom"` unless `OTEL_LOG_TOOL_DETAILS=1` is set. That same setting also exports Bash commands and file paths. The README shows both env blocks:
- **minimal:** the cost panels show one combined `custom` series;
- **detailed:** a cost series per reviewer.

The user chooses. devkit doesn't write Claude's `env` settings for the user, because the user's settings are theirs to change. The README gives the snippet for `settings.json` `env`.

### D12. Codex (deferred until Codex credentials exist)
This part is designed now so that the shipped schema and state machine don't have to change later. Two separate pieces.

**(a) Codex as the main agent.**

The adapter would map:
- Codex `Stop` → `stop`;
- `SessionStart` and `UserPromptSubmit` → the other two phases, if Codex provides them. To be checked against the Codex docs at the time.

Codex Stop must print JSON on exit 0, so "allow" becomes `{}`. The `devkit-review` SKILL.md follows the open Agent Skills format, so Codex can load it from its skills directory.

Codex subagents are defined in TOML, and I've only seen them described by third parties. Until that is confirmed, the skill runs the focuses one after another in the same session on Codex. That is less independent, and the skill says so in its output. The state machine and `devkit review` need no changes.

**(b) Codex as an external reviewer from a Claude session.**

- A new `review` setting, `external: [codex]`. It is not accepted until it is built: today it is an "unknown setting" error, so it can never be silently ignored.
- When it is set, the skill runs `codex exec review --base <base> --json` in round 1, or `--uncommitted` where that fits better. It runs in the background, because Bash's default timeout is 2 minutes. The exact subcommand and flags must be checked with `codex exec review --help` first, because third-party sources disagree.
- Codex findings are turned into the reviewer JSON format and go through the same checker. Nothing from Codex is trusted unchecked.
- A missing `codex` binary or missing authentication skips this step, with a notice in the round's output.
- **Recursion:** a Codex review doesn't change the working tree, so if Codex has devkit hooks installed, its own Stop decision sees `cur == turn_start` and doesn't trigger. As a second safeguard, the decision won't start a loop in a repo where a different session has an active loop.
- **Sandbox:** inside cplt, Codex needs network access to OpenAI, read access to `~/.codex`, and its own Linux sandbox, which may clash with cplt. This needs a live check.
- The same "external reviewer command" shape also allows `claude -p` as the external reviewer of a Codex session.

## Risks / Trade-offs

- [Model cost per triggered turn: several reviewers, plus one checker per finding, for up to 3 rounds] → Rounds 2 and later run only `followup_reviewers` on the fix diff. The number of parallel checkers is capped. `skip_paths` keeps out changes that don't need a review. The rule is off by default and opt-in per repo.
- [Snapshot latency on large repos or with large untracked files] → The copied index's stat cache keeps `add -A` incremental. The 30 s timeout fails open. Ignored files are excluded. Very large untracked files are a known cost: users should ignore them.
- [The agent can claim a round without reviewing, or `skip` on its own] → Accepted, since this is early feedback. `skip` and "round not completed" are always shown to the user, so it can't happen silently.
- [Reviewer subagents have Bash and could change files] → They have no edit tools. Their prompt limits them to read-only commands, and devkit's `secrets` tool hooks still apply to subagents. A round's fixes are shown in round 2's diff, so an unexpected edit is visible.
- [A turn that edits and then asks the user a question gets reviewed before the question reaches them] → Accepted. The review finishes, and the question remains in the final message.
- [The user's own edits between turns are included in the next round 1 scope] → Accepted. The scope is "changes since the last review", which is the safe direction.
- [Concurrent sessions in one repo] → State is kept per session under a file lock. The second-session guard from D12(b) applies to all agents.
- [Git objects pruned by `gc` invalidate stored trees] → A missing tree resets that session's baseline to the current snapshot, which means no review for that turn.
- [Claude's hook format changes] → Recorded-payload tests, and failing open.
- [Logs reveal what the user works on: repo names, file paths, models] → Logs are local, and code text is off by default and can only be turned on by the user. Records hash the repo path and keep the home path out. The stack listens on loopback only and exports nowhere.
- [The OTel GenAI conventions are still marked "Development"] → devkit uses its own `devkit.review.*` event names, and only the generic `code.*` and `session.id` attributes, so a change in the GenAI conventions doesn't affect it.
- [`otel-lgtm` is a single container meant for development] → It is meant to run locally. The collector config is plain OTel and can be pointed at a production backend unchanged, but doing that is the user's decision.

## Migration Plan

1. Run `uv tool install --editable ./devkit`, then `devkit install --agent claude`. This adds the three turn hooks, the skill and the agents. Restart Claude sessions.
2. Opt a repo in by setting `rules: { review: block }` in `.devkit.yaml`, or `warn` to try it out without forcing rounds.
3. Rollback: set `review: off`, or run `devkit uninstall --agent claude`. Run `devkit review reset` to clear the state.

## Open Questions

- The checker thresholds (75 and 50) and the default model for each focus may need tuning after real use. They are config or skill-text changes, not spec changes.
- Whether the `history` focus is worth its cost in small repos. It can be removed from `reviewers` per repo.
