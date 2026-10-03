# Tasks

## 1. Walking skeleton: filesystem.read end to end over MCP — no dependencies

- [ ] 1.1 Create the uv project (`pyproject.toml` with hatchling, `src/repo_warden/`, `tests/`, MIT `LICENSE`, `.gitignore`), pinning `mcp-airlock==0.2.0`, `mcp`, `pydantic`, `pyyaml`, plus dev `pytest` and `pytest-asyncio`. Add a comment naming the fork fallback pin. Verify that `uv sync` succeeds and `uv run python -c "import mcp_airlock.policy, mcp_airlock.audit"` works.
- [ ] 1.2 Write a minimal `gitcmd.py` (hardened env and base `-c` flags) and a `DirectBackend` (argv only, `wait_for` timeout, capped reads, env allowlist) sufficient for `git rev-parse --show-toplevel`. Verify with a unit test that runs `git --version` through the backend.
- [ ] 1.3 Implement `repo.py` `RepoContext` (realpath, top-level check via hardened git, `root_fd`, random session id, OS-user principal). Verify with tests for a valid top level, a subdirectory, a non-repo, a missing path, and a symlinked repo path.
- [ ] 1.4 Implement `paths.py` (`resolve`, `SafePath`, `SensitiveMatcher` with the default patterns, `open_nofollow` component walk). Verify with tests for: `..` and in-repo dot segments, absolute paths, `~`, NUL, `.git`/`.GIT`, direct/chained/nested symlink escapes, in-repo symlinks, a symlink to `.env`, every default sensitive pattern, a symlink swapped in after `resolve`, and a FIFO not blocking.
- [ ] 1.5 Implement `policy.py`:
  - `load_policy` pops the `repo_warden:` block, forces environment `local`, and rejects L1 (including principal overrides), a missing file and a path inside the repo.
  - `PolicyGate` maps airlock decisions to ALLOW/APPROVAL/DENY.
  - `strictest()` and the `Approver` protocol.

  Verify with tests for each mapping, L1 rejection, policy-in-repo refusal (direct path and via symlink), the missing-policy refusal, and stricter-only merge (a looser overlay never wins).
- [ ] 1.6 Implement `audit.py` `Auditor` over `audit_from_env` (default XDG state path, refuse inside repo, fail if it can't be opened). Verify that records carry ts/principal/session/repo/capability/decision/rule_id/status/duration and that `redact`/`scrub` are applied.
- [ ] 1.7 Implement `capabilities.py` (`Capability`, `Risk`, `Registry`) and `broker.py` `Broker.execute` (unknown → deny, invalid input → deny, V1 risk floor, policy, APPROVAL → `approval.unavailable`, intent and outcome audit, output cap from policy, path denials audited as `path.*`). Verify with tests for:
  - unknown capability denied and audited
  - extra `repo` field denied
  - a non-READ test capability denied despite policy
  - an L2 capability denied and audited without running its handler
  - a capability with no policy entry denied
- [ ] 1.8 Implement `handlers/fs.py` `filesystem.read` (regular files only, byte limit, optional line range, binary detection, UTF-8 with replacement). Verify with tests for a normal read, truncation, a binary file, a directory/FIFO refusal, a policy output cap smaller than the byte limit, and that the audit log never contains the file's contents (fixture file containing `AKIA…`).
- [ ] 1.9 Add a guard test that no registered capability has a repo, workspace, root or session selector in its name or input schema. Verify that it passes, and that it fails when a dummy `workspace` field is temporarily added.
- [ ] 1.10 Implement `mcp_server.py` (low-level Server; `list_tools` from the registry with `.`→`_` names; `call_tool` is one `broker.execute`). Write `cli.py` with `--repo/--policy/--audit/--backend`; only `direct` works in this group, with its loud stderr warning. Verify with an in-memory MCP client test: list tools, a successful read, a denied call returning `isError`, and an unlisted tool name denied and audited.
- [ ] 1.11 Ship `examples/policy.yaml` (read capabilities at L0) and a README skeleton covering what it is, install, a run with `--backend direct`, and the policy file location and format. Verify that the documented command starts and lists tools against a scratch repo.
- [ ] 1.12 Run `uv run pytest` clean; PR merged.

## 2. Execution hardening and cplt containment — depends on 1

- [ ] 2.1 Complete `DirectBackend`: `start_new_session`, `killpg(SIGKILL)` on timeout, a drain-and-discard pipe reader past the cap, `stdin=DEVNULL`, empty temp `HOME`, cwd must be in the repo. Verify with tests for:
  - a sleeping child plus grandchild both killed on timeout
  - 50 MB of output capped and marked truncated without memory blow-up
  - `AWS_SECRET_ACCESS_KEY`, `GITHUB_TOKEN` and `SSH_AUTH_SOCK` absent from the child env
  - shell metacharacters passed literally
- [ ] 2.2 Implement `verify_containment()` and `CpltBackend` with the D8 probes (marker, seccomp, write refused in the repo's parent, `~/.ssh` unreadable if present). Make it the CLI default, with a refusal message that points to `--backend direct` and the docs. Verify with unit tests on fake probe inputs: marker-only fails, all pass succeeds, each single failing probe is reported by name.
- [ ] 2.3 Run the probes for real under `cplt exec -- uv run python -m repo_warden.execution --probe` on this host. If a probe doesn't hold, replace it with one that does and update design.md D8 (never weaken it). Add an integration test that runs only under cplt (skipped otherwise). Verify that the probe passes under cplt and refuses outside it.
- [ ] 2.4 Add a README section on broker vs cplt responsibilities and the containment probe, stating that it is a heuristic. Verify that the stated cplt behaviours match `cplt check` output on this host.
- [ ] 2.5 Run `uv run pytest` (and the cplt-only tests under `cplt exec`) clean; PR merged.

## 3. filesystem.list and search.code — depends on 1, 2

- [ ] 3.1 Implement `filesystem.list` (fd-based scandir, no symlink following, bounded depth and entries, hide `.git` and sensitive entries). Verify with tests that a root with `.git/`, `.env` and `src/` lists only `src/`, the entry limit truncates, and a symlinked dir is reported as a link and not descended.
- [ ] 3.2 Implement `search.code` via ripgrep (`--no-config --json --no-follow`, sensitive `--glob` exclusions, no `--pre`/`-z`, literal by default) with post-filtering and caps. Verify with tests that a `.env` match is never returned, `.git` is never searched, the match limit truncates, and a pattern like `$(id)` is treated literally.
- [ ] 3.3 Implement the pure-Python fallback walker (selected when `rg` is missing) with the same exclusions, size and binary skips, and a deadline. Verify by running the 3.2 test cases parametrised over both engines with `rg` hidden from `PATH`.
- [ ] 3.4 Add `filesystem.list` and `search.code` to the example policy and the README capability table with their limits. Verify that the MCP tool-list test now shows them.
- [ ] 3.5 Run `uv run pytest` clean; PR merged.

## 4. Git capabilities, hostile-repo hardening and release docs — depends on 1, 2

- [ ] 4.1 Complete `gitcmd.py`: enumerate `filter.*`/`diff.*`/`merge.*` driver keys with `git config --list --name-only` under the hardened env and override them, plus `protocol.allow=never`, `credential.helper=`, `core.sshCommand=false`, `diff.external=`, `core.attributesFile=/dev/null`, `GIT_NO_LAZY_FETCH` and the others in D9. Verify with unit tests of the generated argv/env for a fixture config containing each driver kind.
- [ ] 4.2 Build a hostile fixture repo whose `core.fsmonitor`, `core.hooksPath` hooks, `.gitattributes` clean/smudge filter, diff textconv, diff command and `diff.external` each `touch` a marker file. Verify that git.status, git.diff and git.log on it never create any marker, and that the test fails if the `-c` overrides are removed.
- [ ] 4.3 Implement `git.status` (`--porcelain=v2 -z --branch`, entry cap). Verify with tests for modified, staged and untracked files, and that `.env` entries are hidden.
- [ ] 4.4 Implement `git.diff` (working tree or `--staged`, safe pathspecs, `:(exclude)` sensitive patterns, header post-filter, `--no-ext-diff --no-textconv`, byte cap). Verify with a test where a tracked `.env` and `src/app.py` are modified: only `src/app.py` is in the output. Also test the cap and truncation.
- [ ] 4.5 Implement `git.log` (`--no-patch`, NUL-separated format, `max_count` bound, optional safe path). Verify that it returns metadata only and respects `max_count`.
- [ ] 4.6 Finish the README:
  - the threat model
  - limitations: native tools bypass the broker, the audit log is writable in the sandbox (Postgres INSERT-only role as the stronger option), repo-local MCP configs are agent-writable
  - user-scope MCP registration for VS Code and for CLI agents under `cplt`, with the VS Code caveat
  - a ROADMAP

  Add `docs/upstream-gaps.md`, covering the missing `where` rules in 0.2.0, the heavy import graph, and approval tokens inside the HTTP app. Verify that every command in the README runs as written against a scratch repo.
- [ ] 4.7 Integration check: under `cplt exec`, start `repo-warden --repo <scratch>` with the default backend, connect a real MCP stdio client, call all six tools plus one denied and one unknown tool, and confirm the audit JSONL has the expected records outside the repo. Record the result in the PR description.
- [ ] 4.8 Run `uv run pytest` (and the cplt-only tests under `cplt exec`) clean; PR merged.
