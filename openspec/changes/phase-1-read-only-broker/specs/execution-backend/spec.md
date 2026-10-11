# Spec Delta

## Purpose

Runs the external processes capabilities need (git, ripgrep) with bounded time, bounded output and a minimal environment, and only where the selected containment is in place.

## ADDED Requirements

### Requirement: No shell, argv only
Processes SHALL be started from an argument vector without a shell. No capability SHALL accept a command line, program name or interpreter code from the agent.

#### Scenario: Shell metacharacters in an argument
- **WHEN** a search pattern contains `; rm -rf ~` or `$(id)`
- **THEN** it is passed as a literal argument and nothing is executed by a shell

### Requirement: Mandatory timeout
Every process SHALL run with a timeout. On timeout the process and every process it started SHALL be killed, and the result SHALL be reported as timed out.

#### Scenario: Process exceeds timeout
- **WHEN** a child process (and a grandchild it spawned) runs longer than the timeout
- **THEN** both are terminated and the call returns a timed-out error

### Requirement: Bounded output
Captured stdout and stderr SHALL each be limited to a maximum size. Output beyond the limit SHALL be dropped and the result marked as truncated, without buffering unbounded data in memory.

#### Scenario: Huge output
- **WHEN** a child writes far more than the output limit
- **THEN** the result holds at most the limit and is marked truncated

### Requirement: Environment allowlist
Child processes SHALL receive only allowlisted environment variables plus those the capability sets explicitly. Credentials and other variables of the broker process SHALL NOT be inherited.

#### Scenario: Cloud credentials in the broker environment
- **WHEN** the broker runs with `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN` and `SSH_AUTH_SOCK` set
- **THEN** none of them is present in a child process's environment

### Requirement: Working directory inside the repository
A process's working directory SHALL be the repository root or a validated path inside it.

#### Scenario: Working directory
- **WHEN** a capability runs a process
- **THEN** its working directory resolves inside the repository

### Requirement: Contained backend verifies containment
The default `cplt` backend SHALL refuse to start unless containment is verified both by the sandbox marker and by active probes showing that access cplt denies is actually denied. A marker alone SHALL NOT be enough. In the contained mode it SHALL run processes directly, since they inherit the sandbox.

#### Scenario: Marker set but not contained
- **WHEN** `__CPLT_WRAPPED` is set but a path cplt denies is accessible
- **THEN** the broker refuses to start and explains which probe failed

#### Scenario: Not under cplt
- **WHEN** the broker is started with the default backend outside cplt
- **THEN** it refuses to start and points to `--backend direct` and the setup docs

### Requirement: Uncontained execution is explicit
The `direct` backend SHALL only be used when explicitly selected and when the account's own policy file authorises it, and SHALL print a prominent warning to stderr at startup stating that no OS containment is verified. The authorisation SHALL be `repo_warden.allow_direct_backend: true` in `<home>/.config/repo-warden/policy.yaml`, where `<home>` comes from the password database. `--policy`, `XDG_CONFIG_HOME` and `HOME` SHALL NOT change where the authorisation is read from. It SHALL default to false.

#### Scenario: Direct backend authorised
- **WHEN** the account policy sets `allow_direct_backend: true` and the broker is started with `--backend direct`
- **THEN** it starts and writes a warning to stderr before serving requests, and records the backend in the audit records

#### Scenario: Direct backend not authorised
- **WHEN** the account policy is missing or does not set `allow_direct_backend: true` and the broker is started with `--backend direct`
- **THEN** it refuses to start and names the missing authorisation

#### Scenario: Authorisation only in a --policy file
- **WHEN** `--policy` points at a file that sets `allow_direct_backend: true` but the account policy does not
- **THEN** the broker refuses to start
