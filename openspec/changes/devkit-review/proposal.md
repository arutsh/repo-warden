# Proposal

## Why

The user wants every coding session that changes code to end with an independent review, with obvious problems fixed before the agent says it is done. A review on every reply would be slow and costly, because most replies only answer questions. devkit already has the hook plumbing, layered config and opt-in scopes, so this is the natural place to add a review loop that runs only when there is something new to review.

## What Changes

- **A new style rule, `review`** (off by default, opt-in repos only, like the other style rules). At the end of a turn it decides whether the working tree holds changes that haven't been reviewed yet:
  - If this turn changed nothing, or the changes were already reviewed, the agent stops as usual and nothing is printed.
  - Otherwise, the agent is told to run a review round instead of stopping.
  - There are at most 3 rounds (configurable). Round 1 reviews everything changed since the last review in this session. Rounds 2 and 3 review only the fixes from the previous round. The loop ends early when a round fixes nothing.
- **New Claude Code hook events**:
  - `SessionStart` records a baseline;
  - `UserPromptSubmit` records the tree at the start of each turn;
  - `Stop` runs the decision.

  The hooks fail open, and they never block in plan mode or while background tasks are running.
- **Snapshots of the working tree as git tree objects**. They are written through a temporary index, with the same git hardening repo-warden uses, and they leave out files that match the `secrets` patterns.
- **A `devkit-review` skill for Claude Code, plus reviewer subagent definitions**:
  - Several independent reviewers run in parallel, one per focus: correctness, project rules, tests, security, history. Each focus has its own model.
  - An independent checker re-checks every finding against the code and scores it.
  - The main session then:
    - fixes only the obvious findings, under a strict definition of "obvious";
    - re-runs the tests;
    - lists the remaining findings for discussion, each with file:line and quoted code;
    - drops findings that have no evidence, and says how many it dropped.
- **A `devkit review` command** that the skill calls:
  - `plan` gives the round, scope, reviewers and models;
  - `diff` gives the changes this round reviews;
  - `record` reports how the round went;
  - `skip` is for when the user asks to skip a review, and it is shown to the user;
  - `reset` clears the review state.

  `devkit status` shows the review state.
- **Review logs and telemetry**, so reviewer quality and cost can be analysed later:
  - Every loop, round and finding is appended as a local JSONL log record, shaped like an OpenTelemetry log. A finding's record holds its focus, model, file:line, checker score and outcome (fixed, discussed, question or dropped).
  - The logs stay on the machine. Code quotes and claims are kept out of exported records unless the user opts in.
  - `devkit review verdict` records whether the user accepted or rejected a finding that was up for discussion. Over time this shows which reviewers and models make good findings.
- **A local observability setup in `devkit/telemetry/`**:
  - a Docker Compose file running Grafana's all-in-one OpenTelemetry image;
  - an OTel Collector config that reads the devkit logs and receives Claude Code's own OTLP metrics and events;
  - a Grafana review dashboard;
  - documented environment settings to turn on Claude Code's built-in telemetry. That telemetry already reports cost and tokens for each model, subagent and skill.
- **`devkit install --agent claude`** also installs the skill and the reviewer agents into `~/.claude/`. Both are marked as devkit-managed, and the installer never overwrites a skill or agent of the same name that it didn't install.
- **External Codex reviewer and Codex as the main agent: designed here, deferred.** Configuration and behaviour are described in the design so the Claude implementation leaves room for them. They are built only once Codex credentials are available.

## Capabilities

### New Capabilities
- `devkit/review`: when a review is triggered and when it is skipped, the round limit, the snapshot and state model, the `devkit review` command, the review skill's process (independent reviewers, checking, triaging findings), its config, and installing the skill and agents for Claude Code.
- `devkit/review-telemetry`: the review log records and where they are kept, what exported records leave out, user verdicts, and the shipped collector config and dashboard.

### Modified Capabilities
<!-- none: devkit-core's specs are not archived yet, so this change only adds `devkit/review`. It builds on devkit-core's rule engine, configuration, Claude adapter and installer. -->

## Impact

- **New code in `devkit/`**:
  - the `review` rule;
  - a `review` package (state, snapshot, the decision, CLI);
  - a hardened git helper, copied from `repo_warden.gitcmd` and kept in sync by a parity test;
  - `skills/devkit-review/SKILL.md`;
  - reviewer agent definitions;
  - tests.
- **Changed code in `devkit/`**:
  - the Claude adapter handles the `session-start`, `user-prompt-submit` and `stop` events;
  - the engine gains a `stop` event kind;
  - the installer registers the new hooks and copies the skill and agents.
- **Hook cost**:
  - In repos where `review` is off or the repo isn't opted in, the new hooks exit after loading config, without running git.
  - Where `review` is on, each turn start and each stop runs a few git commands. Unlike devkit-core's tool hooks this involves git, but it happens once per turn, not once per tool call.
- **Files on the user's machine**:
  - review state in `${XDG_CACHE_HOME:-~/.cache}/devkit/review/`;
  - review logs in `${XDG_STATE_HOME:-~/.local/state}/devkit/review/`, kept for 30 days by default;
  - nothing is sent anywhere unless the user starts the telemetry stack or points a collector at the logs;
  - unreferenced git objects in the repo, removed by normal `git gc`;
  - skill and agent files in `~/.claude/skills/` and `~/.claude/agents/`, written only by `devkit install`.
- **Model usage**: each round that is triggered starts several reviewer subagents and one checker per finding. Rounds 2 and 3 are kept small.
- **Dependencies**: no Python dependency is added; devkit writes plain JSONL and doesn't use the OTel SDK. The optional telemetry stack needs Docker. This builds on devkit-core groups 1–3 being merged.
- **Out of scope**:
  - a Codex adapter for `Stop` and the Codex external reviewer (deferred, see design);
  - running reviews in CI or pre-commit;
  - posting reviews to GitHub PRs.
