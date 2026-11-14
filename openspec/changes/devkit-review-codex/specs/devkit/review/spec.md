# Spec Delta

## ADDED Requirements

### Requirement: Review loop in Codex sessions
`devkit hook codex` SHALL handle Codex's `Stop` hook, and its `SessionStart` and `UserPromptSubmit` hooks where Codex provides them. It SHALL map them to the same turn phases as the Claude Code adapter. When the decision allows the stop, it SHALL print `{}`. When the decision blocks, it SHALL print `decision: "block"` with the reason. `devkit install --agent codex` SHALL register these hooks and install the `devkit-review` skill into Codex's skills directory, with the same marker and refusal rules as for Claude Code.

When subagents aren't available, the skill SHALL run the reviewer focuses one after another in the main session, and SHALL say so in the round's output.

#### Scenario: Codex stop allowed
- **WHEN** a Codex turn changed nothing and Codex sends `Stop`
- **THEN** devkit prints `{}` and exits 0

#### Scenario: Codex stop blocked into a round
- **WHEN** a Codex turn edited code in a repo with `review: block` and Codex sends `Stop`
- **THEN** devkit prints `decision: "block"` with a reason that names the `devkit-review` skill

#### Scenario: Focuses run in sequence
- **WHEN** the skill runs in a Codex session without subagents
- **THEN** each focus is reviewed in turn, and the output says the reviews were not independent subagents

### Requirement: Codex as an external reviewer
The `review` rule SHALL accept `external`, a list whose only allowed value is `codex`. The default SHALL be empty. When `codex` is listed, round 1 of the skill SHALL also run a Codex review of the round's scope in the background. Its findings SHALL be turned into the reviewer finding format, with focus `external`, and SHALL go through the same checker as every other finding. When the `codex` binary is missing or not signed in, the round SHALL go on without it and SHALL say so in its output.

#### Scenario: Unknown external reviewer
- **WHEN** `.devkit.yaml` sets `review: { level: block, external: [gemini] }`
- **THEN** `devkit status` exits non-zero naming `.devkit.yaml` and `rules.review.external`

#### Scenario: Codex findings are checked
- **WHEN** the Codex review reports a finding
- **THEN** it is scored by the checker and recorded with an outcome like any other finding

#### Scenario: Codex not installed
- **WHEN** `external: [codex]` is set and `codex` is not on `PATH`
- **THEN** round 1 runs the other reviewers, and its output says the Codex review was skipped

#### Scenario: No recursive loop
- **WHEN** Codex, with devkit hooks installed, finishes the external review
- **THEN** Codex's own Stop starts no review loop
