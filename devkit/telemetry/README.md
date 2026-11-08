# devkit review telemetry

A local Grafana dashboard for devkit's review loops: how often reviews run, what the reviewers find,
how often the checker and you agree with them, and what each reviewer costs. It is optional, and
devkit never starts it for you.

One container, [`grafana/otel-lgtm`](https://github.com/grafana/docker-otel-lgtm) (pinned to
`0.38.0`), bundles an OpenTelemetry Collector, Prometheus, Loki and Grafana. It reads two inputs:

- **devkit's review logs**, `${XDG_CACHE_HOME:-~/.cache}/devkit/review/logs/*.jsonl`, mounted
  read-only (see "Review logs" in the [devkit README](../README.md#review-logs));
- **Claude Code's own telemetry** (metrics and events), which Claude Code sends over OTLP once you
  turn it on (below).

Everything stays on this machine:
- every port is published on `127.0.0.1` only;
- the collector's only exporters are the container's own Prometheus and Loki;
- the usage reporting that Grafana, Loki, Tempo and Pyroscope send to grafana.com is turned off,
  and so are Grafana's update checks, news feed and plugin downloads.

Data is kept in the Docker volume `devkit-telemetry_data` until you remove it.

## Start and stop

Run these from this directory (`devkit/telemetry` in a repo-warden checkout). You need Docker with
Compose v2.

```sh
mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}/devkit/review/logs"   # before the first start, see below
docker compose up -d       # start; the dashboard is ready within about 30 s
docker compose down        # stop, keeping the data
docker compose down -v     # stop and delete the data (Prometheus, Loki, Grafana)
```

Open <http://127.0.0.1:3300>. The **devkit review** dashboard is the home page. Grafana runs with
anonymous admin access, which is acceptable only because it listens on loopback.

- **Create the log directory first.** If it doesn't exist, Docker creates it as root, and devkit can
  no longer write its logs there.
- **Ports in use.** The defaults are 3300 (Grafana), 4327 (OTLP gRPC) and 4328 (OTLP HTTP): next to
  the standard 4317 and 4318, so they don't clash with another local collector. If something else
  already uses one, choose others. Use the same values for every `docker compose`
  command, and point Claude Code at the new OTLP port:

  ```sh
  DEVKIT_GRAFANA_PORT=13300 DEVKIT_OTLP_GRPC_PORT=14317 DEVKIT_OTLP_HTTP_PORT=14318 docker compose up -d
  ```
- **Docker Desktop** mounts only shared paths. Your home directory is shared by default. If you
  moved `XDG_CACHE_HOME` outside it, add that path under Settings → Resources → File sharing.
- **Existing logs** are read from the start once. The collector stores how far it has read in the
  volume, so a restart doesn't send them again.

## Sending Claude Code's telemetry

devkit's logs cover loops, rounds, findings and verdicts. Cost and tokens come from Claude Code,
which reports them only when its telemetry is on. devkit doesn't change your Claude settings, so add
one of these blocks under `env` in `~/.claude/settings.json` yourself. Change `4327` if you chose
another OTLP port. Claude Code reads `env` when it starts, so restart running sessions afterwards.

**Minimal.** This gives cost and tokens per model, but every custom subagent, devkit's reviewers
included, is reported under one agent name, `custom`.

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4327"
  }
}
```

**Detailed.** This is the minimal block plus one setting, and it gives a cost series per reviewer
(`devkit-review-correctness`, `devkit-review-checker` and so on).

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4327",
    "OTEL_LOG_TOOL_DETAILS": "1"
  }
}
```

What each block exposes, as seen in Claude Code 2.1.319 (the data goes only to this stack, but
anyone who can open Grafana or the volume can read it):

- **Both blocks:**
  - every event and metric carries your account email, account and organization ids, the session
    id, and your OS and version;
  - events record each prompt's length, each tool's name, duration and success, and each API
    request's model, tokens and cost;
  - prompt text is sent as `<REDACTED>`, unless you also set `OTEL_LOG_USER_PROMPTS=1`. Don't, unless
    you want your prompts in Loki.
- **Detailed only, through `OTEL_LOG_TOOL_DETAILS=1`:**
  - tool inputs: every Bash command line and file path, and the first few hundred characters of the
    prompt given to each subagent;
  - subagent and skill names on the metrics, which is what the per-reviewer cost panels need.

Leave telemetry on only while you want the data. It applies to every Claude Code session, not just
review rounds.

## The dashboard

The time picker sets the range for every panel; the default is the last 7 days.

**Activity** (devkit logs)
- *Loops started*: review loops started in the range. A stop with unreviewed changes starts one, and
  so does `devkit review plan --start`.
- *Loops started per repo*: the same count, by repo directory name.
- *How loops ended, per repo*:
  - `clean`: a round fixed nothing;
  - `max_rounds`: the round limit was reached;
  - `not_completed`: the agent stopped without recording its round;
  - `skipped`: you asked to skip.
- *Loops by rounds used*: ended loops, by the round they ended in.
- *Review activity over time*: loops started, rounds requested and loops ended, per time bucket.

**Findings** (devkit logs)
- *Findings by focus and outcome* and *by model and outcome*: every finding the skill recorded. The
  outcomes are `fixed`, `discuss`, `question` and `dropped`.

**Quality** (devkit logs)
- *Checker confirmation rate*, per focus and model: of the findings the checker scored, the share it
  kept (`fixed`, `discuss` or `question`). Findings dropped before checking, such as those with no
  evidence or duplicates, have no score and are left out.
- *User acceptance rate*, per focus and model: of the `discuss` and `question` findings you gave a
  verdict on, the share you accepted. Only the latest `devkit review verdict` per finding counts. The
  verdict is recorded with the finding's focus and model, so no join is needed.
- *Findings with a verdict*: how many findings the acceptance rate rests on. A rate from two
  verdicts says little.

**Cost** (Claude Code metrics)
- *Cost by reviewer and model* and *Tokens by reviewer and model*: `claude_code_cost_usage` and
  `claude_code_token_usage` for agents named `devkit-review-*`, or `custom` with the minimal env
  block. `custom` then includes any other custom subagents you use.
- *Sessions with reviews*: each session that started a review loop, with its loop count and the
  session's whole cost (main agent, reviewers and checker). It joins devkit's `session.id` with
  Claude Code's `session.id`. These were checked to be the same id: the hook payload's
  `session_id`, the `session.id` on Claude Code's events and metrics, and `claude -p`'s
  `session_id` all matched (Claude Code 2.1.319, 2026-11-07). A session shows a cost only if
  telemetry was on while it ran.

Cost is each session's running total, read with `max_over_time`. A session that began before the
time range therefore counts in full. Prometheus's `increase()` gets short sessions wrong: it misses
a new series' first sample, or over-extrapolates when a zero sample is added. Claude Code sends delta
counters by default, so the stack starts Prometheus with `otlp-deltatocumulative`; without it,
Prometheus drops them.

## Files

| File | What it is |
|---|---|
| `compose.yaml` | the container, its loopback ports, mounts and privacy settings |
| `collector.yaml` | replaces the image's collector config: the devkit `filelog` receiver and OTLP in, Prometheus and Loki out |
| `dashboards/devkit-review.json` | the dashboard, provisioned read-only (edit the file, not the UI copy) |
| `dashboards/provider.yaml` | tells Grafana to load the dashboards directory |

In Loki, devkit records are in the stream `{service_name="devkit"}` and Claude Code's events in
`{service_name="claude-code"}`. Attributes become structured metadata, with dots replaced by
underscores. For example:

```logql
{service_name="devkit"} | event_name="devkit.review.finding" | devkit_review_outcome="discuss"
```
