# Agent instructions

Instructions for coding agents (Claude Code, Codex and others) working in this repo. Replace each
TODO with this project's details.

## Project

repo-warden is a capability broker for AI coding agents: one process per git repository exposes
read-only development actions (read, list and search files, git status, diff and log) as MCP tools,
decides each call with a policy kept outside the repo, and audits every decision. Its code is in
`src/repo_warden/`, with tests in `tests/`. It runs as `python -m repo_warden.cli`, normally inside a
`cplt` sandbox.

`devkit/` is a separate package in the same uv workspace: dev-workflow rules run as agent hooks, a
git pre-commit hook and a CI check. Its code is in `devkit/src/devkit/`, tests in `devkit/tests/`.
Planned work lives in `openspec/changes/`.

## Commands

- Tests: `uv run pytest` (repo-warden) and `uv run --package repo-warden-devkit pytest devkit/tests`
  (devkit). CI runs both.
- Lint and format: no linter is configured. `uv run devkit check --staged` runs devkit's rules on
  staged changes, and CI runs `devkit check` on every pull request.

## Conventions

- Keep code comments short: two lines at most. Longer rationale belongs in the commit message, the
  PR description or the docs.
- Don't read, print or change secret files (`.env*`, keys, certificates). Ask the user for what you
  need instead.
- Before adding a test, look for an existing one that covers the behaviour, and extend it instead.
- Work on a branch named by the repo's convention, never directly on the main branch.
- Keep changes small and focused, and run the tests before you say you are done.

## devkit

This repo is opted in to devkit (`.devkit.yaml`). Its hooks can refuse an action with a reason that
starts with `[devkit <rule>]`. Fix the cause instead of working around the check. If you think the
rule is wrong here, ask the user.
