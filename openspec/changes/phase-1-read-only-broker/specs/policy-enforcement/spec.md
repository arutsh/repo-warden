# Spec Delta

## Purpose

Decides, from an operator-controlled policy outside the repository and outside the LLM, whether each capability call may run, denying by default.

## ADDED Requirements

### Requirement: Global policy is loaded from outside the repository
The broker SHALL load its policy from `--policy`, or by default from `$XDG_CONFIG_HOME/repo-warden/policy.yaml` (falling back to `~/.config/repo-warden/policy.yaml`). It SHALL refuse to start if the policy file is missing or invalid, or if its real path is inside the repository.

#### Scenario: Policy inside the repository
- **WHEN** `--policy` points at a file inside the repository, directly or through a symlink
- **THEN** the broker refuses to start

#### Scenario: Missing policy
- **WHEN** no policy file exists at the configured or default location
- **THEN** the broker refuses to start, and does not fall back to an allow-all policy

### Requirement: Default deny and tier mapping
The broker SHALL evaluate each call against the policy's `local` environment. A capability with no policy entry, or no tier for that environment, SHALL be denied. Tiers L0 and L3 SHALL allow. Tier L2 SHALL be denied with an "approval unavailable" reason while no approver is configured. A policy that assigns tier L1 to any capability, in any form including principal overrides, SHALL be rejected at load time.

#### Scenario: Unlisted capability
- **WHEN** the policy has no entry for `git.log` and `git.log` is called
- **THEN** the call is denied and audited with the policy's allowlist rule id

#### Scenario: L2 without an approver
- **WHEN** a capability has tier L2 and is called
- **THEN** the call is denied, its handler does not run, and the audit records an approval-unavailable rule id

#### Scenario: L1 in policy
- **WHEN** the policy file assigns L1 to any capability
- **THEN** the broker refuses to start with an error naming that capability

### Requirement: Unknown capability is denied
A call naming a capability that is not registered SHALL be denied and audited, whatever the policy says.

#### Scenario: Unknown name allowed in policy
- **WHEN** the policy allows `shell.run` at L0 but no such capability is registered, and `shell.run` is called
- **THEN** the call is denied and audited

### Requirement: Invalid input is denied
Capability arguments SHALL be validated against the capability's declared input schema before the policy is evaluated. Unknown fields and out-of-range values SHALL cause a denial.

#### Scenario: Extra argument
- **WHEN** `filesystem.read` is called with an extra `repo` field
- **THEN** the call is denied as invalid input and audited

### Requirement: V1 risk floor
In V1 the broker SHALL deny any capability whose declared risk class is above read-only, regardless of policy.

#### Scenario: Non-read capability registered
- **WHEN** a capability declared with a write, execute or external risk class is called and the policy allows it
- **THEN** the call is denied

### Requirement: Later policy layers can only tighten
When more than one policy decision is combined, the result SHALL be the most restrictive one, ordered DENY > APPROVAL > ALLOW. No additional layer SHALL be able to turn a deny into an approval or allow, or an approval into an allow.

#### Scenario: Stricter overlay
- **WHEN** the global policy allows a call and an additional layer requires approval
- **THEN** the combined decision requires approval

#### Scenario: Looser overlay
- **WHEN** the global policy denies a call and an additional layer allows it
- **THEN** the combined decision is deny
