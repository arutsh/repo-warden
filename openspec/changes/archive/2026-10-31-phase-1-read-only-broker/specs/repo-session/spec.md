# Spec Delta

## Purpose

Binds one broker process to exactly one git repository and one session, chosen by the trusted launcher at startup and fixed for the process lifetime.

## ADDED Requirements

### Requirement: Repository is fixed at startup
The broker SHALL take the repository from the required `--repo` argument, resolve it to its real path, and refuse to start unless that path is the top level of a git working tree. The resolved root SHALL stay fixed for the lifetime of the process.

#### Scenario: Valid repository top level
- **WHEN** the broker is started with `--repo` pointing at the top level of a git working tree
- **THEN** it starts and every capability operates relative to that real path

#### Scenario: Subdirectory of a repository
- **WHEN** `--repo` points at a subdirectory of a git working tree
- **THEN** the broker refuses to start with an error naming the expected top level

#### Scenario: Not a repository
- **WHEN** `--repo` points at a directory that is not inside a git working tree, or does not exist
- **THEN** the broker exits with a non-zero status before serving any request

#### Scenario: Symlinked repository path
- **WHEN** `--repo` is a symlink to a repository top level
- **THEN** the broker binds to the symlink's real target path and reports that path

#### Scenario: Linked worktree
- **WHEN** `--repo` points at the top level of a linked git worktree
- **THEN** the broker starts with that worktree as its root, and the worktree's `.git` pointer file is never exposed

### Requirement: No capability can change the repository or workspace
The broker SHALL NOT expose any capability that sets, switches or redefines the repository, workspace, root directory or session. No capability input SHALL accept a repository or workspace selector.

#### Scenario: Capability inventory
- **WHEN** the list of registered capabilities and their input schemas is inspected
- **THEN** none has a name or input field for selecting a repository, workspace, root or session

### Requirement: Session identity
At startup the broker SHALL generate a random session identifier and determine the principal as the operating-system user running the process. Both SHALL be attached to every audit record of that process.

#### Scenario: Session id on records
- **WHEN** two calls are made in one broker process
- **THEN** both audit records carry the same session id, and a second broker process uses a different one
