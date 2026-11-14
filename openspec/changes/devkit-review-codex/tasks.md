# Tasks

Moved from `devkit-review` group 9. Start once `devkit-review` is archived and the `codex` CLI is installed and signed in.

## 1. Codex as the main agent

- [ ] 1.1 Check the Codex docs for `SessionStart` and `UserPromptSubmit` and their payload fields. Then add `session-start`, `user-prompt-submit` and `stop` to the Codex adapter, with allow printed as `{}` and a block as `decision: "block"` (D1). Verify with recorded-payload tests matching `devkit-review` task 3.4.
- [ ] 1.2 Make the skill work under Codex: install it into Codex's skills directory, and run focuses one after another when subagents aren't available, saying so in the output. Update the README's Codex sections. Verify in a live Codex session with the checks from `devkit-review` task 8.1.

## 2. Codex as an external reviewer

- [ ] 2.1 Run `codex exec review --help` to settle the subcommand and flags. Then add:
  - the `external: [codex]` setting, an "unknown setting" error until then;
  - the background run;
  - translation of its findings to the reviewer JSON;
  - the shared checker;
  - the notice when the binary or authentication is missing.

  The second-session guard already exists in the stop decision. Document the setting in the README's review section. Verify with:
  - a CLI-mocked test of the translation;
  - a live Claude session where Codex findings reach the checker;
  - a check that Codex's own Stop hook does not start a loop.

## 3. Codex inside cplt

- [ ] 3.1 Check Codex inside cplt: it needs network access to OpenAI, read access to `~/.codex`, and its own Linux sandbox (D3). Record what cplt needs in the README. Verify with one external review run under cplt.
