# Spec Delta

## Purpose

Defines how agent-supplied paths become safe targets inside the bound repository, and which paths are never exposed even when they are inside it.

## ADDED Requirements

### Requirement: Only safe relative paths are accepted
Every path argument SHALL be a relative path inside the repository. The broker SHALL deny a request whose path is absolute, starts with `~`, contains a NUL byte, or uses `..` to leave the repository.

#### Scenario: Parent escape
- **WHEN** a capability is called with path `../other-repo/file.txt` or `src/../../x`
- **THEN** the call is denied and audited, and nothing outside the repository is read

#### Scenario: Absolute path
- **WHEN** a capability is called with path `/etc/passwd`
- **THEN** the call is denied

#### Scenario: Home shorthand
- **WHEN** a capability is called with path `~/.ssh/id_rsa` or `~root`
- **THEN** the call is denied

#### Scenario: NUL byte
- **WHEN** a path contains a NUL byte
- **THEN** the call is denied

#### Scenario: Harmless in-repo dot segments
- **WHEN** a capability is called with `src/../README.md`, which stays inside the repository
- **THEN** the request is evaluated as `README.md`

### Requirement: Symlinks must not leave the repository
The broker SHALL resolve every path fully, including chained and nested symlinks, and SHALL deny the request unless the real path is inside the repository's real root. Symlinks whose targets stay inside the repository SHALL be allowed, subject to the other rules applied to the target.

#### Scenario: Direct symlink escape
- **WHEN** `link -> /etc` exists in the repository and `link/passwd` is requested
- **THEN** the call is denied

#### Scenario: Chained symlink escape
- **WHEN** `a -> b`, `b -> c` and `c -> ../outside` exist and `a/file` is requested
- **THEN** the call is denied

#### Scenario: Nested directory symlink escape
- **WHEN** `src/vendor -> ../../elsewhere` exists and `src/vendor/x.py` is requested
- **THEN** the call is denied

#### Scenario: In-repo symlink
- **WHEN** `docs/latest -> v2` exists and `docs/latest/index.md` is requested
- **THEN** the call reads `docs/v2/index.md`

#### Scenario: Symlink swapped in after the check
- **WHEN** a path component is replaced with a symlink to a location outside the repository between validation and opening
- **THEN** the open fails and no data from outside the repository is returned

### Requirement: Git internals are not exposed
The broker SHALL deny any path that resolves into the repository's `.git` directory, or to a `.git` file, in any letter case.

#### Scenario: Reading git config
- **WHEN** `.git/config` or `.GIT/config` is requested
- **THEN** the call is denied

#### Scenario: Symlink into git internals
- **WHEN** `cfg -> .git/config` exists and `cfg` is requested
- **THEN** the call is denied

### Requirement: Sensitive paths are not exposed
The broker SHALL deny any path whose basename (lexical or resolved) matches a configured sensitive pattern. The default patterns SHALL be `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `*.pfx` and `id_*`, and the operator SHALL be able to extend them through the global policy configuration.

#### Scenario: Default sensitive file
- **WHEN** `.env`, `config/.env.production`, `certs/server.pem` or `deploy/id_ed25519` is requested
- **THEN** the call is denied and the audit record shows the denial without the file's contents

#### Scenario: Symlink to a sensitive file
- **WHEN** `notes.txt -> .env` exists and `notes.txt` is requested
- **THEN** the call is denied
