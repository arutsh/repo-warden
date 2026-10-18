# Spec Delta

## Purpose

Defines how the devkit rules run outside any agent (a pre-commit hook and CI) and how a repo is opted in with `devkit init`.

## ADDED Requirements

### Requirement: Checking staged changes and commit ranges
`devkit check` SHALL support two modes:
- `--staged` compares each staged file with its `HEAD` version;
- `--range BASE..HEAD` compares each file changed in that range.

It SHALL run the rules on those files:
- added and modified files become write or edit events;
- a staged or changed file matching a secret pattern gets a `secrets` finding;
- `branch_name`, if on, checks the current branch.

Output SHALL list each finding with the file, line and rule. The exit code SHALL be 1 if any finding is at `block`, and 0 otherwise. Unlike agent hooks, `devkit check` SHALL fail closed: it SHALL exit 2 if the config is invalid or git fails.

#### Scenario: Committing a secret file
- **WHEN** `.env.prod` is staged and `devkit check --staged` runs
- **THEN** it exits 1 with a `secrets` finding for `.env.prod`

#### Scenario: Pre-existing comment not blamed
- **WHEN** a staged file keeps an existing five-line docstring and changes other code
- **THEN** there is no `comment_brevity` finding

#### Scenario: Invalid config
- **WHEN** `.devkit.yaml` is malformed
- **THEN** `devkit check` exits 2 and names the file

### Requirement: Git pre-commit hook install
`devkit install --git-hooks` SHALL write a devkit-managed `pre-commit` hook that runs `devkit check --staged`, into the current repo's hooks directory. It SHALL refuse, printing how to call `devkit check --staged` from the existing setup instead, when either holds:
- `core.hooksPath` is set;
- a `pre-commit` hook not written by devkit already exists.

`devkit uninstall --git-hooks` SHALL remove the hook only if devkit wrote it.

#### Scenario: Existing foreign hook
- **WHEN** `.git/hooks/pre-commit` already exists and was not written by devkit
- **THEN** install exits non-zero and leaves it unchanged

#### Scenario: Hook blocks the commit
- **WHEN** the hook is installed and a commit stages `id_rsa`
- **THEN** the commit is refused with the `secrets` finding

### Requirement: Opting a repo in with init
`devkit init` SHALL write a commented `.devkit.yaml` at the repo's top level, which opts the repo in. Optional flags SHALL write:
- `--agents-md`: `AGENTS.md` from the template, and `CLAUDE.md` containing `@AGENTS.md` if no `CLAUDE.md` exists;
- `--openspec`: generic openspec `rules:` into `openspec/config.yaml`;
- `--ci`: a GitHub Actions workflow that runs `devkit check --range` on pull requests.

`init` SHALL never overwrite an existing file. An exception: for `--openspec` it SHALL add a `rules:` block only when the config has none, and otherwise print the snippet.

#### Scenario: Fresh repo
- **WHEN** `devkit init --agents-md` runs in a repo with neither file
- **THEN** `.devkit.yaml`, `AGENTS.md` and `CLAUDE.md` (containing `@AGENTS.md`) are created, and `devkit status` shows the repo as opted in

#### Scenario: Existing AGENTS.md
- **WHEN** `AGENTS.md` already exists
- **THEN** it is left unchanged and init says so

#### Scenario: openspec config with rules
- **WHEN** `openspec/config.yaml` already has a `rules:` key
- **THEN** the file is unchanged, and the suggested rules are printed
