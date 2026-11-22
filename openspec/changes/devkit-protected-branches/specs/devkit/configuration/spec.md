# Spec Delta

## MODIFIED Requirements

### Requirement: Guard rules can only be tightened by the repo
The repo config can be written by the agent and by anyone who controls the repository. For guard rules (`secrets`, `self_guard`, `protected_branches`), it SHALL therefore only be able to:
- raise the level;
- add `secrets` patterns;
- add protected branches.

It SHALL NOT be able to:
- lower the level;
- remove patterns or protected branches;
- add `secrets` exemptions;
- set `protected_branches.exempt_repos`.

An attempt to loosen SHALL be ignored, and the setting SHALL keep the user-or-default value. `devkit status` SHALL show the ignored attempt.

#### Scenario: Repo tries to switch off secrets
- **WHEN** `.devkit.yaml` sets `secrets: off`
- **THEN** `secrets` stays at its user-or-default level, and `devkit status` reports the ignored setting

#### Scenario: Repo adds a pattern
- **WHEN** `.devkit.yaml` adds the secret pattern `*.tfvars`
- **THEN** a read of `prod.tfvars` gets a `secrets` finding

#### Scenario: Repo tries to exempt itself from protected_branches
- **WHEN** `.devkit.yaml` sets `protected_branches: { level: off, exempt_repos: ["."] }`
- **THEN** `protected_branches` stays at `block` with no exempt repos, and `devkit status` reports both ignored settings
