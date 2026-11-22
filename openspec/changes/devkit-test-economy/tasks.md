# Tasks

## 1. Reviewers (managed assets, reach every installed repo)

- [ ] 1.1 In `claude_assets/agents/devkit-review-tests.md`, add a "What's too much" section (D1, D5): duplicate of an existing test, cases that should be one table-driven or parametrized test, tests of internals, tests beyond the intent or scenarios. Each finding names the covering test or the scenario it goes beyond, and its fix deletes, merges or extends tests. Add a step to "How to work" saying to compare each new test with the existing tests found in step 2. Verify: extend `test_agent_definition` in `devkit/tests/test_claude_assets.py` with a check that the tests agent names no framework (`pytest`, `jest`, `unittest`, `go test`).
- [ ] 1.2 In `devkit-review-rules.md`, add that when the diff touches `openspec/changes/`, the reviewer checks those files against `openspec/config.yaml` `rules:`. In `skills/devkit-review/SKILL.md`, add `openspec/config.yaml` to the rule-file paths when the repo has one (D3). Verify: add one phrase assertion to the existing `test_skill_process_points`. No new test.
- [ ] 1.3 Update the reviewer table in the `devkit/README.md` "review" section (the `tests` and `rules` rows).

## 2. Templates and init

- [ ] 2.1 Extend `templates/openspec-rules.yaml` with the specs, design and tasks rules and the `operations.apply.guidance` entry from the proposal, worded without naming a language. Verify: the existing `test_templates_pass_comment_brevity` and `test_openspec_rules_appended_when_none` in `devkit/tests/test_init.py` still pass. Change the second to compare `operations` as well as `rules`.
- [ ] 2.2 In `initcmd._openspec`, skip appending when the config has `rules` or `operations`, and say which key it found (D4). Verify: parametrize the existing `test_openspec_with_rules_unchanged_and_printed` over a `rules:` config and an `operations:`-only config, rather than adding a new test.
- [ ] 2.3 Add the test-reuse line to the Conventions section of `templates/AGENTS.template.md`, and update the `devkit init` section of `devkit/README.md`. Explain that repos with existing openspec rules get them printed and should copy them by hand. Verify: `test_templates_pass_comment_brevity` passes.
- [ ] 2.4 Bump devkit to 0.5.0 in `devkit/pyproject.toml` and run `uv lock`. Verify: `uv run pytest` passes in `devkit/` and at the repo root.

## 3. Repo 0: repo-warden on the shipped setup

- [ ] 3.1 Run `devkit install --agent claude` with the new version, then `devkit init --agents-md --openspec` in repo-warden (D6). Verify: init reports `.devkit.yaml` left unchanged, `AGENTS.md` and `CLAUDE.md` created, and openspec rules added. `openspec instructions specs --change devkit-test-economy --json` shows the new `rules`.
- [ ] 3.2 Fill in only the Project and Commands TODOs in `AGENTS.md`. Fix anything else that's wrong for repo-warden in the template instead (task 2.3). Verify: `git diff` on `AGENTS.md`, compared with the template, touches only those two sections.
- [x] 3.3 Set `review: { level: block }` in `.devkit.yaml`. Verify: `devkit status` shows `review block (repo: .devkit.yaml)`.
- [ ] 3.4 Live check of both spec scenarios: on a scratch branch, add a test that duplicates an existing one, plus a spec delta that restates an existing requirement, and let the review round run. Verify: the tests reviewer reports the duplicate and names the existing test, and the rules reviewer cites the openspec rule. Record the outcome in the PR description, then drop the scratch branch.
