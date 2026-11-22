# Tasks

## 1. Command parsing

- [x] 1.1 Add the parser (D1): heredoc bodies dropped, split on `&&`, `||`, `;`, `|` and newlines outside quotes, then each part tokenised with the `shlex` helper from `secrets`. It returns git invocations, each with its directory (`cd`, `-C`) and its subcommand arguments after git's global options. Verify with unit tests:
  - the GrandFlow command (`git branch --show-current && git add … && git commit -q -F - <<'EOF' … EOF` + `git push origin main`) yields `commit` and `push origin main`;
  - a commit message or heredoc containing `git push origin main` yields no push;
  - `git -C ../other push` carries `../other`;
  - `cd sub && git commit` carries `sub`.
- [x] 1.2 Add push target resolution (D2). Verify with table tests:
  - `origin main`, `+main`, `HEAD:main`, `feat:main`, `:main`, `refs/heads/main`, `--delete origin main`, `--all` and `--mirror` are protected;
  - `origin feat/x`, `-u origin feat/x`, `origin v1.0 --tags` and `origin refs/tags/v1` are not;
  - bare `push`, `push origin` and `push origin HEAD` are protected only when the current branch is.

## 2. The rule

- [x] 2.1 Add `rules/protected_branches.py` and register it:
  - `guard = True`, default `block`, phases `{"pre"}`, kinds `{"shell"}`;
  - settings `branches` (default `["main", "master"]`, in `extend` and `repo_settings`) and `exempt_repos` (default `[]`), with `validate` (D4);
  - the current-branch lookup, read from `HEAD` without running git (D3);
  - branch switches tracked within a command (D1);
  - the message (D5).

  Verify with temp-repo tests for every spec scenario under "protected_branches rule": a detached HEAD, a non-repo directory, and `gh pr merge` give no finding, and a command with no `git` runs no git subprocess.
- [x] 2.2 Verify the configuration scenarios with config tests:
  - "Repo tries to exempt itself from protected_branches": `devkit status` lists both ignored settings;
  - `.devkit.yaml` adding `release` gives branches `[main, master, release]`;
  - `exempt_repos` from the user config is honoured, with `~` expanded.
- [x] 2.3 Check the Claude and Codex adapters: a PreToolUse `Bash` payload with the GrandFlow command returns a `deny` with the reason, and the same with Codex's `Bash` payload. Verify with recorded-payload tests.

## 3. Docs and release

- [x] 3.1 Document the rule in the devkit README:
  - what it blocks and what it doesn't;
  - the settings, with `exempt_repos` and `warn` snippets;
  - "not a security boundary";
  - GitHub's `enforce_admins`, and that with 1 required approval and a single maintainer, it also blocks merging your own PRs unless the approval count is 0.

  Add it to the guard rule list in "Opting in and out" and to the status line at the top. Verify that the README's example config loads with `devkit status`.
- [x] 3.2 Bump devkit to 0.4.0 and run `uv lock`. Run the devkit and root test suites clean. Then, in a scratch repo on `main`, run a real Claude Code session: the agent's `git commit` is blocked; after it switches to a branch, its commit and `git push -u` go through.

  Done 2026-11-22: devkit 615 passed, root 240 passed and 3 skipped. In a live `claude -p` session, the agent's `git add … && git commit … && git push origin main` on `main` was denied by `protected_branches`. Its `git switch -c feat/hello && … git commit … && git push -u origin feat/hello` passed devkit's hook. It was then refused by the user's own Claude `ask` rules for `git add`, `git commit` and `git push`, which a headless session can't answer. The user accepted this, together with the unit tests, as evidence that the branch route goes through.
