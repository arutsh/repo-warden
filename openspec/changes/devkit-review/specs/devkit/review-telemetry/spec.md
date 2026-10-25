# Spec Delta

## Purpose

Defines the logs devkit keeps about review loops, rounds and findings, what they may contain, and the shipped telemetry setup that lets the user analyse reviewer quality and cost on a dashboard.

## ADDED Requirements

### Requirement: Review log records
devkit SHALL append one JSON object per line to `${XDG_STATE_HOME:-~/.local/state}/devkit/review/<YYYY-MM-DD>.jsonl` for each of these events:
- `devkit.review.loop_started`
- `devkit.review.round_requested`
- `devkit.review.round_recorded`
- `devkit.review.finding`, one per finding passed to `record`
- `devkit.review.loop_ended`, with the reason `clean`, `max_rounds`, `not_completed` or `skipped`
- `devkit.review.verdict`

Each record SHALL follow the OpenTelemetry log data model:
- a `timestamp`;
- a `severity_text`;
- a `body`;
- a `resource` with `service.name: devkit` and the devkit version;
- `attributes` with `event.name`, `session.id`, `devkit.review.loop_id` and `devkit.review.round`, plus the event's own fields.

A `devkit.review.finding` record SHALL also carry:
- a stable `devkit.review.finding_id`;
- `focus`, `model` and `outcome`;
- `score`, when checked;
- `drop_reason`, when dropped;
- `code.file.path` and `code.line.number`.

The repo SHALL be identified by its top-level directory name and by a hash of its path. The user's home path SHALL NOT appear in records.

Writing a log record SHALL never fail the hook or the command that triggered it. A failed write SHALL produce a one-line warning on stderr.

#### Scenario: A round produces finding records
- **WHEN** `record` receives findings with outcomes `fixed`, `discuss` and `dropped`
- **THEN** the day's log gains one `round_recorded` record and three `finding` records, all with the same `loop_id` and `round`

#### Scenario: Log write failure
- **WHEN** the log directory is not writable and a stop requests a round
- **THEN** the stop is still blocked as usual, and stderr carries a devkit warning about the log

### Requirement: Content in logs is controlled
By default, finding records SHALL NOT contain the code quote, the claim text or the failure scenario. A record SHALL carry those fields only when the user config sets `rules.review.log_content: true`. The repo layer SHALL NOT be able to enable `log_content`; a repo setting for it SHALL be ignored, and `devkit status` SHALL report it. Records SHALL never contain the content of files that match the `secrets` patterns.

The user config SHALL be able to turn logging off with `rules.review.log: false`. `rules.review.log_retention_days` SHALL default to 30. Log files older than that SHALL be deleted when a new record is written.

#### Scenario: Default records leave out text
- **WHEN** `log_content` is not set and a finding is recorded
- **THEN** its record has focus, model, path, line, score and outcome, but no quote, claim or failure scenario

#### Scenario: Repo can't turn on content logging
- **WHEN** `.devkit.yaml` sets `review: { level: block, log_content: true }`
- **THEN** records still leave out the text fields, and `devkit status` lists the ignored setting

#### Scenario: Retention
- **WHEN** `log_retention_days` is 30 and a log file from 31 days ago exists
- **THEN** it is deleted the next time a record is written

### Requirement: User verdicts on findings
`devkit review verdict <finding_id> accepted|rejected [--note TEXT]` SHALL append a `devkit.review.verdict` record that references the finding. It SHALL exit non-zero if no finding with that id was recorded within the retention period. `--note` text SHALL be stored only when `log_content` is true.

#### Scenario: Rejecting a finding
- **WHEN** the user rejects a finding up for discussion and `devkit review verdict f-3a9c rejected` runs
- **THEN** the log gains a verdict record with `finding_id: f-3a9c` and `verdict: rejected`

#### Scenario: Unknown finding
- **WHEN** `devkit review verdict nope accepted` runs
- **THEN** it exits non-zero and writes nothing

### Requirement: Shipped telemetry stack and dashboard
devkit SHALL ship, under `devkit/telemetry/`:
- a Docker Compose file that runs a local OpenTelemetry collector, a metrics store, a log store and Grafana. Every port SHALL be bound to `127.0.0.1` only.
- a collector config that:
  - tails the review log directory (mounted read-only);
  - accepts OTLP on localhost for Claude Code's own telemetry;
  - sends records nowhere else.
- a Grafana dashboard, set up automatically, with:
  - **activity:** reviews triggered, skipped and not completed per repo; rounds per loop;
  - **findings:** findings by focus and by model, and their outcomes;
  - **quality:** for each focus and model, the share of findings the checker confirmed out of those it checked, and the share of discussed findings the user accepted;
  - **cost:** cost and tokens for each reviewer agent and model, from Claude Code's metrics.
- documentation of the Claude Code environment settings that send its telemetry to this collector, and of which of them expose prompts, tool parameters or file paths.

Starting the stack SHALL be a manual, documented command. Nothing in devkit SHALL start it or send data off the machine on its own.

#### Scenario: Stack starts and shows review data
- **WHEN** the user runs the documented compose command and a review round has been recorded
- **THEN** the dashboard at `http://127.0.0.1:3000` shows the round and its findings

#### Scenario: Not exposed on the network
- **WHEN** the stack is running
- **THEN** none of its ports listen on a non-loopback address
