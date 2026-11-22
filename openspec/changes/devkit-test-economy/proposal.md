# Proposal

## Why

Nothing in devkit pushes back on extra tests or bloated specs. The tests reviewer only reports *missing* tests, the rules reviewer checks code against an openspec change but never the change itself, and the openspec rules that `devkit init` ships say nothing about reusing tests or keeping specs small. In practice, every change adds new tests next to existing ones that already cover the same behaviour, and specs restate requirements instead of modifying them. This has to be fixed in what devkit ships, so it applies in every repo where devkit is installed. repo-warden is the first of those repos ("repo 0") and gets the fix the same way as any other.

## What Changes

- **Tests reviewer reports excess, not only gaps.** The managed `devkit-review-tests` agent also reports:
  - a new test that checks the same behaviour as an existing one;
  - several tests that differ only in input or expected value, and should be one table-driven or parametrized test;
  - a test that pins down internals, so a refactor that keeps the behaviour would break it;
  - a test for behaviour that neither the change's intent nor its spec scenarios ask for.
  
  The fix it proposes is to delete, merge or extend a test, never to add one. The wording names no language or test framework.
- **Rules reviewer checks openspec artifacts in the diff.** When the diff changes files under `openspec/changes/`, the managed `devkit-review-rules` agent checks them against the `rules:` in the repo's `openspec/config.yaml`, the same way it checks code against `AGENTS.md`.
- **Leaner openspec template.** The rules that `devkit init --openspec` writes gain:
  - specs: scenarios only for behaviour a caller can observe, and modify an existing requirement instead of restating it;
  - design: a non-goals list;
  - tasks: each test task names the existing test file it extends, or says why it needs a new one;
  - an `operations.apply.guidance` entry: search the existing tests before writing one, and extend an existing test when it covers the behaviour.
- **AGENTS.md template.** Adds one convention line: reuse or extend an existing test before adding a new one.
- **Not a mechanical check.** Whether each scenario has a test, and whether a test duplicates another, stays a reviewer judgement. There is no new `devkit check` rule: any convention linking tests to scenarios would depend on the language.
- **Repo 0 onboarding.** repo-warden runs `devkit init --agents-md --openspec` like any other repo, fills in only the project-specific TODOs in `AGENTS.md`, and turns `review` on in its `.devkit.yaml`.
- devkit gets a minor version bump.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `devkit/review`: adds a requirement that the tests reviewer reports excess tests and the rules reviewer checks openspec artifacts against the openspec config rules.
- `devkit/repo-checks`: the `--openspec` part of "Opting a repo in with init" also writes `operations` guidance, and treats a config that has either `rules` or `operations` as already set.

## Impact

- `devkit/src/devkit/claude_assets/agents/devkit-review-tests.md` and `devkit-review-rules.md`. Every installed repo gets these on its next `devkit install`.
- `devkit/src/devkit/templates/openspec-rules.yaml`, `AGENTS.template.md` and `devkit/src/devkit/initcmd.py`. Only repos that run `init` after the upgrade get the new rules. Init never rewrites an existing `rules:` block, it only prints the suggested one.
- repo-warden itself gets a new `AGENTS.md` and `CLAUDE.md`, rules in `openspec/config.yaml`, and `review` turned on in `.devkit.yaml`.
- devkit's `pyproject.toml` version and `uv.lock`.
