# Spec Delta

## Purpose

Defines where devkit settings come from, how the user and repo layers combine, which repos the rules apply to, and how a user sees the effective configuration.

## ADDED Requirements

### Requirement: Layered configuration
The effective setting of every rule SHALL be computed in this order:
1. built-in defaults;
2. the user config at `$XDG_CONFIG_HOME/devkit/config.yaml` (default `~/.config/devkit/config.yaml`);
3. the repo config `.devkit.yaml` at the git top level of the working directory.

Each later layer overrides the earlier ones, except where the guard-rule restriction applies. Each rule's level SHALL be one of `off`, `warn` or `block`.

A missing file SHALL count as an empty layer. Unknown rules, unknown keys and invalid levels SHALL be reported:
- `devkit status` and `devkit check` SHALL fail on them;
- a hook SHALL ignore that layer and say so in its output.

#### Scenario: Repo overrides a style rule
- **WHEN** the user config sets `comment_brevity: warn` and the repo config sets `comment_brevity: block`
- **THEN** the effective level is `block`

#### Scenario: Repo relaxes a style rule
- **WHEN** the user config sets `comment_brevity: block` and the repo config sets it to `off`
- **THEN** the effective level is `off`

#### Scenario: Invalid level
- **WHEN** `.devkit.yaml` sets `secrets: maybe`
- **THEN** `devkit status` exits non-zero naming the file and key

### Requirement: Guard rules can only be tightened by the repo
The repo config can be written by the agent and by anyone who controls the repository. For guard rules (`secrets`) it SHALL therefore only be able to:
- raise the level;
- add patterns.

It SHALL NOT be able to lower the level, remove patterns or add exemptions. An attempt to loosen SHALL be ignored, and the setting SHALL keep the user-or-default value. `devkit status` SHALL show the ignored attempt.

#### Scenario: Repo tries to switch off secrets
- **WHEN** `.devkit.yaml` sets `secrets: off`
- **THEN** `secrets` stays at its user-or-default level, and `devkit status` reports the ignored setting

#### Scenario: Repo adds a pattern
- **WHEN** `.devkit.yaml` adds the secret pattern `*.tfvars`
- **THEN** a read of `prod.tfvars` gets a `secrets` finding

### Requirement: Repo scope
The user config SHALL set `repos` to either value:
- `opted-in` (the default): style rules run only in repos that contain `.devkit.yaml`;
- `all`: style rules run in every repo.

Guard rules SHALL run in every repo either way, and outside any repo, unless the user config sets their level to `off`.

#### Scenario: Repo not opted in
- **WHEN** `repos` is `opted-in`, the repo has no `.devkit.yaml`, and an edit adds a long comment
- **THEN** there is no finding

#### Scenario: Guard rule outside an opted-in repo
- **WHEN** `repos` is `opted-in`, the repo has no `.devkit.yaml`, and the agent runs `cat .env`
- **THEN** the command is blocked

### Requirement: No environment bypass
No environment variable, command-line flag passed to a hook, or repo file SHALL be able to disable the devkit or a guard rule. Switching the devkit off SHALL only be possible by one of:
- the user config;
- `devkit uninstall`.

#### Scenario: Environment variable set
- **WHEN** the agent's session has `DEVKIT_DISABLE=1` or any similar variable set
- **THEN** rules run exactly as configured

### Requirement: Status shows effective settings
`devkit status` SHALL print:
- for every rule, its effective level, its settings and the layer each came from;
- the repo scope, and whether the current repo is opted in;
- any ignored repo settings;
- for each agent and for the current repo's git hook, whether devkit hooks are installed.

#### Scenario: Explaining a block
- **WHEN** the user runs `devkit status` in a repo whose `.devkit.yaml` raised `comment_brevity` to `block`
- **THEN** the output shows `comment_brevity  block  (repo: .devkit.yaml)`
