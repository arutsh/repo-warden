# Tasks

Depends on devkit-core groups 1–3 being merged (rule engine, config, Claude adapter, installer). It doesn't depend on devkit-core group 4.

## 1. Config and event model

- [ ] 1.1 Add the `turn` event kind and the phases `session-start`, `prompt` and `stop` (D2). Add `session_id` and a read-only `meta` mapping to `Event`. Verify that existing engine tests still pass, and that a new test shows a `pre`-phase rule never sees `turn` events.
- [ ] 1.2 Add per-rule `user_only` settings to the config (D10): a repo-layer value for one of these keys is recorded as ignored and shown by `devkit status`. Verify with config tests where `log_content: true` in `.devkit.yaml` is ignored and reported, and the same key in the user config is applied.
- [ ] 1.3 Add the `review` rule's class and settings, without the snapshot logic yet:
  - `max_rounds` (1–5);
  - `skip_paths`;
  - `reviewers` (allowed focuses and models);
  - `followup_reviewers`;
  - `checker`;
  - `log`, `log_content` (user only) and `log_retention_days`.

  Validation goes through `Rule.validate`. Verify with tests for the spec scenarios "Off by default" (no git subprocess, checked by patching `subprocess`), "Invalid model" and "Not opted in". Also test that `max_rounds: 0`, `max_rounds: 6` and an unknown focus are each a config error.

## 2. Snapshots and git hardening

- [ ] 2.1 Add `devkit/gitsafe.py`, copied from `repo_warden.gitcmd`: the env, the base overrides, and driver enumeration on every call (D4). It runs synchronously with `subprocess`. Verify with a parity test asserting that `GIT_ENV` and `BASE_OVERRIDES` equal repo-warden's, and a test that a `filter.x.clean` key produces the blanking overrides.
- [ ] 2.2 Implement `snapshot(top, secret_patterns) -> tree_id`: a temp copy of the index, `add -A` with `:(exclude,icase,glob)` excludes, then `write-tree`. Verify with temp-repo tests:
  - identical trees give the same id;
  - an edit changes the id;
  - an untracked, non-ignored `.env.local` is left out of the tree (`git ls-tree -r`);
  - `git diff --cached` and `git rev-parse HEAD` are unchanged ("Index untouched");
  - a hostile `filter.x.clean` that writes a marker file never runs ("Hostile filter driver").
- [ ] 2.3 Implement `changed_paths(a, b)` and `diff(a, b)` over tree ids, with the same excludes and hardening. Verify with tests that the diff between two snapshots shows only the edit, and that a secret path never appears.
- [ ] 2.4 Check that snapshots work inside cplt. Under cplt, run the 2.2 tests against a scratch repo, with the devkit cache in `~/.cache`. Verify that objects can be written to `.git/objects`, and that no ref is updated. Record the result in the devkit README's review section. If cplt blocks object writes, stop and report before continuing.

## 3. Review state, the stop decision and the Claude turn hooks

- [ ] 3.1 Implement the state file (D5): per-repo path, `fcntl` lock, atomic replace, a cap of 100 `reviewed` trees, pruning of sessions older than 7 days, keeping state on `resume`, and treating an invalid file as empty with a warning. Verify with unit tests for each of these, including two writers racing on the lock.
- [ ] 3.2 Implement the stop state machine (D6) as a pure function from state, the current tree, settings and meta to a decision and new state. Verify with table tests for every spec scenario under "A review is triggered only for new, unreviewed changes", "Review rounds and how the loop ends" and "Stops that are never blocked". Also test that a turn is blocked at most `max_rounds` times when the agent records fixes every round.
- [ ] 3.3 Wire the `review` rule's `check` for the `session-start`, `prompt` and `stop` phases to the snapshot (group 2) and the state machine (3.2). A missing stored tree means the baseline is reset (D5 or D6). Verify with temp-repo tests driving the three phases in order.
- [ ] 3.4 Add `session-start`, `user-prompt-submit` and `stop` to the Claude adapter's `HOOK_EVENTS`, and map them to `turn` events with `meta`:
  - a stop block prints `decision: "block"`;
  - a warn prints `systemMessage` only;
  - the end-of-loop summary and the not-completed and skipped notices print `systemMessage`.

  Verify with recorded-payload tests for:
  - the question-only turn, where nothing is printed;
  - an edit turn, which blocks with round 1;
  - plan mode, which isn't blocked;
  - `background_tasks` not empty, which isn't blocked;
  - warn, which prints no block;
  - a snapshot failure, which exits 0 with a warning.
- [ ] 3.5 Make install register the three turn hooks for Claude, with no matcher and a 30 s timeout, using the existing recognition and backup rules. Verify with installer tests on a temp `HOME`: idempotent, user hooks kept, uninstall removes only devkit's entries.

## 4. The `devkit review` command and status

- [ ] 4.1 Implement these subcommands, with `--session` when more than one loop is active (D7):
  - `plan` (and `--start` for on-demand runs);
  - `diff`;
  - `skip --reason`;
  - `reset`.

  Verify with CLI tests:
  - `plan` JSON for round 1 lists every focus, and round 2 lists only `followup_reviewers`;
  - `diff` for round 2 shows only the round-1 fixes ("Follow-up scope");
  - "Skip is visible";
  - each subcommand exits non-zero when no loop is active.
- [ ] 4.2 Implement `record`: it reads a findings JSON array on stdin, validates the fields and outcomes, and derives the counts. Verify with tests for "Counts derived from findings", invalid JSON rejected with nothing recorded, and "Record outside a loop".
- [ ] 4.3 Extend `devkit status` with the review settings, their sources, the active loop and round, and the last completed review. Verify with the "Active loop shown" scenario.
- [ ] 4.4 Add the review section to the devkit README: what triggers a review, what doesn't, rounds, the config keys with an example, the `devkit review` subcommands, state and cache locations, and "not a security boundary". Verify that the README's example config loads with `devkit status`.

## 5. Review logs

- [ ] 5.1 Implement the log writer (D10):
  - daily JSONL under `XDG_STATE_HOME`, in the OTel log data model shape;
  - `O_APPEND` writes under an `fcntl` lock;
  - repo name plus path hash, with no home path;
  - retention cleanup on write;
  - a failed write gives a warning and never fails the hook.

  Verify with tests for "A round produces finding records", "Log write failure", "Retention", and a check that no record contains `$HOME`.
- [ ] 5.2 Emit `loop_started`, `round_requested`, `round_recorded`, `finding` and `loop_ended` from the state machine and `record`, with a stable `finding_id`. Verify that a temp-repo run of two rounds produces the expected record sequence and that all of them share the same `loop_id`.
- [ ] 5.3 Enforce the content controls: quote, claim, failure scenario and note are logged only when the user config sets `log_content`, and `log: false` writes nothing. Verify with "Default records leave out text", "Repo can't turn on content logging", and a `log: false` test.
- [ ] 5.4 Implement `devkit review verdict <id> accepted|rejected [--note]`. Verify with "Rejecting a finding" and "Unknown finding".
- [ ] 5.5 Add the logging section to the devkit README: the file location, the record fields, what `log_content` adds, retention, and how `verdict` is used. Verify that the README's sample record matches a record written by the 5.2 test (same keys).

## 6. Review skill, reviewer agents and their install

- [ ] 6.1 Write `claude_assets/agents/devkit-review-{correctness,rules,tests,security,history,checker}.md`. Each has a single-focus prompt, `tools: Read, Grep, Glob, Bash`, no edit tools, a default model, the managed marker, and the finding JSON output contract (D8). The checker carries the 0–100 rubric and the thresholds. Verify with a test that parses each file's frontmatter and asserts the tools, model and marker.
- [ ] 6.2 Write `claude_assets/skills/devkit-review/SKILL.md`, following the spec's eight-step process:
  - `plan` and `diff`;
  - parallel reviewers that are never forks, passing `model` from `plan`;
  - the prompt contract with no main-session reasoning;
  - dropping findings without evidence;
  - the checker, at most 10 at a time;
  - the definition of an obvious fix, no commit, re-running tests;
  - Fixed, To discuss (with finding ids) and Dropped;
  - piping findings into `record`, and `verdict` on user answers;
  - on-demand use through `plan --start`.

  Verify by reviewing it against each spec scenario under "The review skill's process", and with a test that every `devkit review` command the skill mentions exists in the CLI parser.
- [ ] 6.3 Extend `install|uninstall --agent claude` to copy the skill and agents into `$CLAUDE_CONFIG_DIR` or `~/.claude`, with the marker files, atomic writes, no rewrite when unchanged, and refusal on an unmanaged file of the same name (D9). Make sure the assets ship as package data. Verify with tests for "Idempotent skill install" and "Foreign skill kept", uninstall removing only managed files, and `uv build` including the assets.
- [ ] 6.4 Document the skill, the reviewer focuses, the models and how to change them, cost expectations, and on-demand `/devkit-review` in the devkit README. Verify by following the README's install steps on this machine: `/devkit-review` appears in a new Claude Code session.

## 7. Telemetry stack and dashboard

- [ ] 7.1 Write `devkit/telemetry/compose.yaml`, using `grafana/otel-lgtm` pinned to a tag at least two weeks old. Ports are bound to `127.0.0.1`, and the log directory and `collector.yaml` are mounted read-only. Write `collector.yaml` with `filelog` (`json_parser`) → Loki and OTLP → Prometheus/Loki, and no other exporters (D11). Verify with `docker compose config`, the stack starting, and `ss -ltn` showing only loopback listeners ("Not exposed on the network").
- [ ] 7.2 Verify that the hook's `session_id` equals the `session.id` that Claude Code puts in its own OTel events, by running one Claude session with telemetry pointed at the stack. Record the result in `telemetry/README.md`. If they differ, have the dashboard leave out the joined panels and say so in the README.
- [ ] 7.3 Build `dashboards/devkit-review.json`, provisioned automatically, with the activity, findings, quality and cost panels from the spec and D11. Verify by recording a sample round (from the 5.2 fixture) and seeing every devkit panel show data in Grafana. Then check that the cost panels show `devkit-review-*` series after one live review with the detailed env block.
- [ ] 7.4 Write `devkit/telemetry/README.md`, linked from the devkit README. It covers:
  - start and stop commands;
  - the minimal and detailed Claude Code env blocks for `settings.json` `env`, and what each one exposes (`OTEL_LOG_TOOL_DETAILS`, prompts);
  - what each panel means;
  - that nothing is exported off the machine.

  Verify that the documented commands run as written on this machine.

## 8. End-to-end check in Claude Code

- [ ] 8.1 In a scratch repo opted in with `review: block`, run a real Claude Code session with the devkit installed. Verify, and note in the PR:
  - a question-only prompt ends with no review;
  - a prompt that edits code is blocked into round 1, and parallel reviewer subagents run on their configured models;
  - a planted obvious bug is fixed, uncommitted;
  - a non-obvious finding is listed for discussion with its id;
  - round 2 reviews only the fix;
  - the summary line appears;
  - `devkit review verdict` on the discussed finding is recorded;
  - the dashboard shows the loop.
- [ ] 8.2 Run the devkit and root test suites clean; PR merged.

## 9. Codex (deferred until Codex credentials exist; may be split into its own change)

- [ ] 9.1 Check the Codex docs for `SessionStart` and `UserPromptSubmit` and their payload fields. Then add `session-start`, `user-prompt-submit` and `stop` to the Codex adapter, with allow printed as `{}` and a block as `decision: "block"` (D12a). Verify with recorded-payload tests matching 3.4.
- [ ] 9.2 Make the skill work under Codex: install it into Codex's skills directory, and run focuses one after another when subagents aren't available, saying so in the output. Verify in a live Codex session with the 8.1 checks.
- [ ] 9.3 Run `codex exec review --help` to settle the subcommand and flags. Then add the `external: [codex]` setting (an "unknown setting" error until then), the background run, translation of its findings to the reviewer JSON, the shared checker, the notice when the binary or authentication is missing, and the second-session guard (D12b). Verify with:
  - a CLI-mocked test of the translation;
  - a live Claude session where Codex findings reach the checker;
  - a check that Codex's own Stop hook does not start a loop.
- [ ] 9.4 Check Codex inside cplt: it needs network access to OpenAI, read access to `~/.codex`, and its own Linux sandbox. Record what cplt needs in the README. Verify with one external review run under cplt.
