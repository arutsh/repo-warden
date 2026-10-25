# Agent instructions

Instructions for coding agents (Claude Code, Codex and others) working in this repo. Replace each
TODO with this project's details.

## Project

TODO: what this repo is, its main parts, and how to run it.

## Commands

- Tests: TODO
- Lint and format: TODO

## Conventions

- Keep code comments short: two lines at most. Longer rationale belongs in the commit message, the
  PR description or the docs.
- Don't read, print or change secret files (`.env*`, keys, certificates). Ask the user for what you
  need instead.
- In tests, prefer the factories in `tests/factories/` to building model objects by hand.
- Work on a branch named by the repo's convention, never directly on the main branch.
- Keep changes small and focused, and run the tests before you say you are done.

## devkit

This repo is opted in to devkit (`.devkit.yaml`). Its hooks can refuse an action with a reason that
starts with `[devkit <rule>]`. Fix the cause instead of working around the check. If you think the
rule is wrong here, ask the user.
