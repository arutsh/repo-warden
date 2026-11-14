# Design

## Context

This design is moved from `devkit-review`'s D12, where it was written so that the shipped config schema and stop state machine would not need to change for Codex. That change built:
- the `turn` event kind and the `session-start`, `prompt` and `stop` phases;
- the stop decision, including the guard that won't start a loop in a repo where another session has an active loop;
- the `devkit review` command, the skill and the reviewer agents for Claude Code.

What Codex documents for hooks (Codex hooks docs, 2026-10-24):
- `Stop` takes `decision: "block"` and `reason`, turning the reason into a new prompt;
- it has `stop_hook_active` and `turn_id`;
- it must always print JSON when exiting 0.

`SessionStart` and `UserPromptSubmit` are not yet confirmed.

## Goals / Non-Goals

**Goals:**
- Codex sessions get the same review loop as Claude Code sessions, with no change to the state machine or to `devkit review`.
- A Claude Code session can add Codex as one more reviewer, whose findings are checked like any other.

**Non-Goals:**
- Codex subagents in TOML. They are described only by third parties so far, so they are left out until confirmed.
- `claude -p` as the external reviewer of a Codex session. The same "external reviewer command" shape allows it later.

## Decisions

### D1. Codex as the main agent
The adapter maps:
- Codex `Stop` → `stop`;
- `SessionStart` and `UserPromptSubmit` → the other two phases, if Codex provides them. This must be checked against the Codex docs first.

Codex Stop must print JSON on exit 0, so "allow" becomes `{}`. The `devkit-review` SKILL.md follows the open Agent Skills format, so Codex can load it from its skills directory.

Until Codex subagents are confirmed, the skill runs the focuses one after another in the same session on Codex. That is less independent, and the skill says so in its output.

### D2. Codex as an external reviewer from a Claude Code session
- A new `review` setting, `external: [codex]`. Until it is built, it is an "unknown setting" error, so it can never be silently ignored.
- When it is set, the skill runs `codex exec review --base <base> --json` in round 1, or `--uncommitted` where that fits better. It runs in the background, because Bash's default timeout is 2 minutes. Third-party sources disagree on the exact subcommand and flags, so check them with `codex exec review --help` first.
- Codex findings are turned into the reviewer JSON format and go through the same checker. Nothing from Codex is trusted unchecked.
- A missing `codex` binary or missing authentication skips this step, with a notice in the round's output.
- **Recursion:** a Codex review doesn't change the working tree. So if Codex has devkit hooks installed, its own Stop decision sees `cur == turn_start` and doesn't trigger. The second-session guard from `devkit-review` is the second safeguard.

### D3. Sandbox
Inside cplt, Codex needs network access to OpenAI, read access to `~/.codex`, and its own Linux sandbox, which may clash with cplt. This is checked live, and the README records what cplt needs.

## Risks / Trade-offs

- [Codex hook payloads differ from the docs] → Tests use recorded payloads from a live Codex session, not the docs.
- [Running focuses one after another in the same session is less independent] → The skill says so in its output, and the findings still go through the checker.
- [`codex exec review` output format changes] → The translation is tested against a recorded output. A parse failure skips the external reviewer with a notice instead of failing the round.

## Open Questions

- Whether Codex's own sandbox can run inside cplt at all. If it can't, the README says external Codex reviews need to run outside cplt.
