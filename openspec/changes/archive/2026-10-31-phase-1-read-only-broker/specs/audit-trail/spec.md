# Spec Delta

## Purpose

Records every capability call, including denials, so operators can reconstruct what an agent tried to do in a repository and what the broker decided.

## ADDED Requirements

### Requirement: Every call is audited
For every capability call, including unknown, invalid and denied ones, the broker SHALL write an audit record containing: timestamp, principal (OS user), session id, repository root, capability name, redacted arguments, decision, rule id, result status and duration. Calls that reach a handler SHALL produce an intent record before the handler runs and an outcome record after it.

#### Scenario: Allowed call
- **WHEN** `git.status` is called and allowed
- **THEN** an intent record and an outcome record are written with the same call id, the outcome giving status ok and a duration

#### Scenario: Denied call
- **WHEN** a call is denied by policy
- **THEN** one record is written with the decision deny, its rule id, and no handler output

#### Scenario: Handler error
- **WHEN** a handler fails (e.g. file not found or timeout)
- **THEN** the outcome record carries an error status and a short scrubbed message

### Requirement: Audit never contains secrets or file contents
Audit records SHALL NOT contain file contents, search matches, diff text or other handler output. Arguments SHALL be passed through credential redaction, and free-text messages through credential scrubbing, before they are written.

#### Scenario: Read of a file containing a token
- **WHEN** `filesystem.read` returns a file containing `AKIA...` credentials
- **THEN** the audit log contains the path and byte count but not the file contents

#### Scenario: Credential-shaped argument
- **WHEN** a search pattern argument looks like a credential (e.g. `ghp_...`)
- **THEN** the audited argument value is redacted

### Requirement: Audit location is outside the repository
The default audit sink SHALL be a JSONL file at `$XDG_STATE_HOME/repo-warden/audit.jsonl` (falling back to `~/.local/state/repo-warden/audit.jsonl`). The broker SHALL refuse to start if the configured audit path resolves inside the repository. When an audit database DSN is configured, records SHALL also be written to Postgres.

#### Scenario: Audit path inside the repository
- **WHEN** `--audit` points inside the repository
- **THEN** the broker refuses to start

#### Scenario: Audit write failure
- **WHEN** the audit log cannot be opened at startup
- **THEN** the broker refuses to start instead of running unaudited
