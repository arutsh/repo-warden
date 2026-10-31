# Spec Delta

## Purpose

Defines the read-only development capabilities offered in Phase 1 and their guarantees: bounded output, secret hiding, and no execution of repository-controlled code.

## ADDED Requirements

### Requirement: filesystem.read
`filesystem.read` SHALL return the contents of a regular file at a safe path, decoded as UTF-8 with replacement characters, up to a byte limit, reporting whether it was truncated. It SHALL support an optional line range. It SHALL deny directories, devices, FIFOs and sockets, and SHALL report binary files as binary without returning their bytes.

#### Scenario: Read a source file
- **WHEN** `filesystem.read` is called with `src/app.py`
- **THEN** the file's text is returned with its size and a truncation flag

#### Scenario: Large file
- **WHEN** the file is larger than the byte limit
- **THEN** only the first bytes up to the limit are returned and truncated is true

#### Scenario: FIFO
- **WHEN** the path is a named pipe
- **THEN** the call fails without blocking

### Requirement: filesystem.list
`filesystem.list` SHALL list the entries of a directory at a safe path (default: repository root), with their type and size, up to an entry limit and an optional bounded depth. It SHALL omit `.git` and entries matching sensitive patterns, and SHALL NOT follow symlinks while listing.

#### Scenario: Listing hides secrets and git
- **WHEN** the root containing `.git/`, `.env` and `src/` is listed
- **THEN** only `src/` (and other non-sensitive entries) are returned

#### Scenario: Entry limit
- **WHEN** a directory has more entries than the limit
- **THEN** the result is cut at the limit and marked truncated

#### Scenario: Nested worktree not descended
- **WHEN** a directory containing a `.git` entry (nested worktree, submodule or repository) is below the listed path
- **THEN** it is listed as an entry but its contents are not returned

### Requirement: search.code
`search.code` SHALL search file contents for a pattern (literal by default, regex optional) within an optional safe sub-path and glob, returning matches as path, line number and a bounded line excerpt, up to a match limit. It SHALL use ripgrep when available and a built-in walker otherwise. It SHALL skip `.git`, sensitive files and binary files, SHALL NOT follow symlinks, and SHALL NOT use repository-supplied preprocessors, decompressors or configuration.

#### Scenario: Secret file not searched
- **WHEN** `.env` contains `API_KEY=abc` and `search.code` looks for `API_KEY`
- **THEN** no match from `.env` is returned

#### Scenario: Nested worktree not searched
- **WHEN** a nested worktree inside the repository contains a matching line
- **THEN** no match from inside the nested worktree is returned

#### Scenario: Same results without ripgrep
- **WHEN** ripgrep is not installed
- **THEN** the search still works and obeys the same exclusions and limits

### Requirement: git.status
`git.status` SHALL return the branch and the changed, staged and untracked paths in a machine-readable form, up to an entry limit.

#### Scenario: Modified file
- **WHEN** a tracked file is modified
- **THEN** it appears in the status as modified

### Requirement: git.diff
`git.diff` SHALL return the unified diff of the working tree, or of the staged changes, optionally limited to safe paths, up to a byte limit. Changes to sensitive files SHALL be left out of the diff text.

#### Scenario: Tracked secret file changed
- **WHEN** a tracked `.env` and `src/app.py` are both modified
- **THEN** the diff shows `src/app.py` and not the contents of `.env`

### Requirement: git.log
`git.log` SHALL return commit metadata (hash, author name, date, subject) for up to a bounded number of commits, optionally limited to a safe path. It SHALL NOT return patch contents.

#### Scenario: Recent commits
- **WHEN** `git.log` is called with `max_count` 5
- **THEN** at most five commits are returned without diffs

### Requirement: Repository-controlled git configuration does not execute
Git capabilities SHALL NOT run any hook, fsmonitor, clean/smudge/process filter, textconv, external diff, pager, credential helper, SSH command or network fetch configured by the repository (including per-worktree config), by the user's global config, or through attributes.

#### Scenario: Malicious fsmonitor and hooks
- **WHEN** the repository config sets `core.fsmonitor` and `core.hooksPath` to scripts that create a marker file, and git.status, git.diff and git.log are called
- **THEN** the marker file is never created

#### Scenario: Malicious filter and textconv
- **WHEN** `.gitattributes` assigns a filter and a diff driver whose clean, textconv and command scripts create a marker file
- **THEN** git.status and git.diff succeed or fail without creating the marker file

#### Scenario: Malicious per-worktree config
- **WHEN** the broker runs in a linked worktree whose `config.worktree` (with `extensions.worktreeConfig` enabled) sets `core.fsmonitor` to a script that creates a marker file
- **THEN** git.status, git.diff and git.log never create the marker file

### Requirement: Outputs are bounded and scrubbed
Every capability result SHALL be bounded by the capability's own limits and by the policy's output cap. Error messages returned to the agent SHALL be scrubbed of credential-shaped substrings.

#### Scenario: Output cap from policy
- **WHEN** the policy sets a smaller output cap for `filesystem.read` than its byte limit
- **THEN** the returned content respects the smaller cap and is marked truncated
