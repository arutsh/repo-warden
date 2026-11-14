# Proposal

## Why

The devkit review loop (change `devkit-review`) works only with Claude Code. Its Codex parts were designed in that change (its D12) but deferred until Codex could be checked live. They are split out here so that `devkit-review` can be archived without them, and so they can be built once the `codex` CLI is installed and signed in on this machine.

## What Changes

- **Codex as the main agent.** The Codex adapter handles `SessionStart`, `UserPromptSubmit` and `Stop`, maps them to the same turn phases as Claude Code, and prints Codex's output format: `{}` to allow, `decision: "block"` with a reason to continue the turn.
- **The `devkit-review` skill under Codex.** `devkit install --agent codex` installs the skill into Codex's skills directory. When subagents aren't available, the skill runs the reviewer focuses one after another in the same session and says so in its output.
- **Codex as an external reviewer from a Claude Code session.** A new `review` setting, `external: [codex]`:
  - round 1 also runs `codex exec review` in the background;
  - its findings are turned into the reviewer JSON and go through the same checker;
  - a missing binary or missing sign-in skips this step with a notice.

  Until this change is built, `external` stays an "unknown setting" config error.
- **Codex inside cplt.** What cplt needs for an external Codex review is checked live and documented.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `devkit/review`: adds the Codex turn hooks, the skill under Codex, and the `external` reviewer setting. This capability is created by `devkit-review`, which must be archived first.

## Impact

- **Code in `devkit/`:**
  - the Codex adapter and the Codex installer;
  - the `review` rule's settings;
  - the skill text;
  - tests with recorded Codex payloads and a mocked `codex` CLI.
- **Docs:** the devkit README's Codex sections and review section.
- **Prerequisite:** the `codex` CLI on `PATH`, signed in. It isn't installed on this machine yet (2026-11-14).
- **Unchanged:** the stop state machine and `devkit review`. The second-session guard that a Codex external review relies on was already built in `devkit-review`.
