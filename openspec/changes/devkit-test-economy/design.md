# Design

## Context

See proposal.md for the motivation. What exists today:
- The reviewer agents and the skill are packaged under `devkit/src/devkit/claude_assets/`, and `devkit install` copies them into the user's Claude config, refreshing managed files. One edit there reaches every repo on the machine.
- `devkit init --openspec` appends `templates/openspec-rules.yaml` to `openspec/config.yaml` only when the config has no `rules:` key ([initcmd.py](../../../devkit/src/devkit/initcmd.py) `_openspec`). It never rewrites existing rules.
- openspec (1.13) reads `rules.<artifact>` while it plans and `operations.apply.guidance` while it applies. The generated `openspec/config.yaml` documents both.
- repo-warden has `.devkit.yaml`, but no `AGENTS.md`, no `CLAUDE.md`, no openspec `rules:`, and `review` is off.

## Goals / Non-Goals

**Goals:**
- One place to change for every installed repo: the managed agents for review, the templates for planning.
- Excess-test findings that a person can act on: what the excess test is, which existing test already covers it, and what to delete or merge.

**Non-Goals:**
- Updating openspec rules in repos that already have them. init keeps printing the suggestion. A merge or `devkit init --refresh` can come later if this turns out to matter.
- A dedicated spec reviewer focus. That would add a sixth focus to the config, and a separate agent, for what fits in one paragraph of the rules reviewer.
- Changing the AGENTS.md template's other conventions, such as the `tests/factories/` line, beyond the one new line.

## Decisions

### D1. Excess tests belong to the tests reviewer, not a new focus
The tests reviewer already finds the tests for the changed code (step 2 of its process). Deciding whether a new test repeats one of them is the same search. A separate "test economy" focus would read the same files twice, and its findings would conflict with the tests reviewer's "missing test" findings. Instead, both directions sit in one agent, and that agent can weigh them against each other.

To keep the two from fighting, an excess finding must name the test that already covers the behaviour, or the scenario or intent it goes beyond. "Looks like too many tests" is not a finding. The existing rule that a finding needs a file, line, quote and failure scenario still applies. For an excess test, the failure scenario is the cost it adds: for example, "renaming `_parse` breaks this test while behaviour is unchanged".

### D2. Excess findings follow the normal fix-or-discuss rule
No special handling. The skill's "obvious" test already decides:
- deleting an exact duplicate is local, has one correct form, and the existing test still covers the behaviour, so it is obvious;
- merging cases into a parametrized test, or dropping a test of internals, changes test structure, so it goes to discussion.

### D3. Openspec artifacts are checked by the rules reviewer
`openspec/config.yaml` `rules:` are written project rules, which is exactly what this reviewer checks. Its prompt already includes the active change directories. It only needs to treat `openspec/config.yaml` as a rule file whenever the diff touches `openspec/changes/`. The skill's rule-file list gains `openspec/config.yaml`, and the paths are still passed without the content.

Alternative considered: checking specs at propose time only, through the template rules. That fails in repos whose rules predate this change, and it doesn't catch specs edited later during apply.

### D4. init: skip when either `rules` or `operations` exists
The snippet now holds two top-level keys. If the config already has `operations:` but no `rules:`, appending the snippet would create a duplicate `operations` key, which is invalid YAML for most readers. Merging the keys is more code than it is worth (non-goal above). So init appends the snippet only when neither key exists, and otherwise prints it.

### D5. Language-neutral wording
The reviewer and templates say "table-driven or parametrized test" and "the test file for that module". They name no framework and no file extension. The only language-specific parts of devkit stay the `comment_brevity` and `test_factories` rules.

### D6. Repo 0 uses the shipped commands, and adds only what's specific to this project
In repo-warden, run `devkit install` and `devkit init --agents-md --openspec`. init leaves `.devkit.yaml` unchanged and creates `AGENTS.md`, `CLAUDE.md` and the openspec rules. Then fill in only the Project and Commands TODOs (what the repo is, `uv run pytest`), and set `review: { level: block }` in `.devkit.yaml`. If anything else in the generated files is wrong for repo-warden, fix it in the template, not here.

## Risks / Trade-offs

- [The tests reviewer starts reporting real tests as excess] → each excess finding has to name the covering test, and the checker scores it like any other finding, so a vague one is dropped.
- [Repos with existing openspec rules never get the new ones] → init prints them, and the devkit README says to run `devkit init --openspec` and copy the suggested lines. A merge can be added later (non-goal).
- [Review on in repo 0 adds a review round to every code-changing turn here] → wanted: repo 0 should exercise the whole loop. `skip_paths` can exclude docs if the rounds get noisy.

## Migration Plan

Ships in devkit 0.5.0. This assumes `devkit-protected-branches` (0.4.0) merges first; otherwise bump whatever main has by one minor. Existing repos get the reviewer changes on their next `devkit install`. Rollback is to reinstall the previous devkit version.
