# Design

## Context

See proposal.md for the incident. What the code already offers:
- `Rule.guard`, `extend` and `repo_settings` give exactly the layering the spec asks for. `branches` goes in `extend`; `repo_settings = {"branches"}`, so the repo layer's `exempt_repos` and lower levels are ignored and listed by `devkit status`.
- `secrets.shell_words` already splits a command with `shlex`, falling back on bad quoting. `branch_name.CREATE_RE` finds branch creation.
- `gitsafe.run(root, ...)` runs git with repo-warden's hardening: no global or system config, no hooks, no fsmonitor. The `review` rule already calls it from hooks.
- Hooks must stay quick and fail open (agent-hooks spec).

## Goals / Non-Goals

**Goals:**
- Catch the common shapes, the GrandFlow one first: `git commit` on main, and `git push` to main, alone or chained.
- No git subprocess for commands that don't mention `git commit` or `git push`.
- No false positive for a commit message or heredoc that mentions `git push origin main`.

**Non-Goals:**
- Enforcement. GitHub branch protection with `enforce_admins` is the real control. The README says so.
- Blocking `git merge`, `git rebase`, `cherry-pick` or `revert` on main. They change only the local branch, and the push that would publish them is blocked.
- Judging the user's own commits, or a git `pre-push` hook. Both would put devkit between the user and their repo, and GrandFlow's own `pre-push` already allows `main` on purpose.
- Resolving git aliases, script files or `eval`.

## Decisions

### D1. Parse the command into git invocations, with state carried left to right
1. Drop heredoc bodies, from a `<<WORD` or `<<'WORD'` line through the closing `WORD` line.
2. Split on `&&`, `||`, `;`, `|`, `&` and newlines outside quotes. The `&` in a redirection (`2>&1`, `&>file`) doesn't split. Tokenise each part with the `shlex` helper that `secrets` uses, and drop redirection words.
   - A part that runs `sh`, `bash`, `zsh` or `dash` with a `-c`-style flag is parsed again from its script, up to 3 levels deep. Codex sends every command as `["bash", "-lc", "..."]`, so without this the rule would never fire under Codex.
3. Walk the parts in order, carrying the repo directory and the assumed current branch:
   - `cd <dir>` changes the directory. The branch becomes "unknown, read it again" for the new directory.
   - `git` with its global options (`-C <dir>`, `-c k=v`, `--git-dir`, `--work-tree`, `--no-pager`) sets the directory for that one invocation.
   - `git switch <b>`, `git switch -c|-C <b>`, `git checkout <b>` and `git checkout -b|-B <b>` set the assumed branch. A `checkout` whose argument isn't a branch (`checkout -- file`, `checkout .`) leaves it alone, judged by whether `<b>` is a local branch or the create flag was used.
   - `git commit …` is judged against the assumed branch.
   - `git push …` is judged by D2.

Alternative considered: one regex over the whole command, like `branch_name`. It can't tell `git push origin main` inside a commit message from a real push, and it can't follow `git switch -c x && git commit`, the safe pattern we want to allow.

### D2. Which branches a push would update
- Options: skip `-u`/`--set-upstream`, `-f`/`--force`, `--force-with-lease[=…]`, `--no-verify`, `-q` and `-v`. `--all` or `--mirror` → protected. `--delete <names>` → judge each name. `--tags` alone → not protected.
- The positional arguments are `[remote] [refspec…]`.
  - With no refspec, or a refspec of only `HEAD`, the push updates the current branch's upstream (`push.default=simple`, git's default). It is protected when the current branch is protected. In that case we don't need to read the upstream: `simple` refuses a name mismatch, and a protected local branch normally tracks the protected remote branch of the same name.
  - Each refspec `[+]src[:dst]` updates `dst`, or `src` when there's no `:`. `HEAD` resolves to the current branch, `refs/heads/` is stripped, and an empty `src` (`:main`) is a delete.
- A refspec under `refs/tags/` is not a branch. A bare name is treated as a branch: only protected names matter, and a tag called `main` isn't worth an extra git call.

### D3. Reading the current branch
- Read `HEAD` from the repo's git dir directly: `.git/HEAD`, or for a worktree the `gitdir:` that `.git` points to. `ref: refs/heads/<b>` gives the branch.
  - This runs no git, so no repo config is read, and it stays cheap in a hook.
  - It replaces the planned `gitsafe.run(... "symbolic-ref" ...)`, which would also have started gitsafe's `config --list` process on every call.
- Whether a `git checkout <name>` argument is a local branch is read from `refs/heads/<name>` or `packed-refs`, following `commondir` for linked worktrees.
- A detached HEAD, not being in a repo, or an unreadable git dir gives no finding, keeping hooks fail-open.
- The repo top level, for `exempt_repos`, comes from the existing repo lookup used by config loading. Paths are compared after `expanduser` and `realpath`.

### D4. Matching branch names
Exact, case-sensitive names: git branch names are case-sensitive, and GitHub's default branch is `main`. No globs for now, since `branches` can list names. `validate` rejects empty names and names containing whitespace, `:` or `..`.

### D5. The message
`[devkit protected_branches] this command would push to the protected branch 'main'. Create a branch (git switch -c <name>, following the repo's branch naming), commit and push there, then open a pull request. If this must go to 'main' directly, ask the user to do it from their own terminal.`

The commit variant says "commit on". The reason names the first offending part only.

## Risks / Trade-offs

- [False positives block normal work in repos where the user does want an agent on main] → `exempt_repos`, or `warn`/`off` in the user config. The README shows the snippet.
- [Bypass through aliases, script files, `eval`, `git -c alias.p=push p`] → Accepted and documented. Not a security boundary.
- [An assumed branch goes stale] → `git checkout <b>` can fail at run time, leaving HEAD on main while the rule assumes `<b>`. That gives a false negative, which is the safe direction for fail-open. The push is still caught when it names `main` explicitly.
- [Hook latency] → No subprocess at all: a regex pre-check, then file reads of `HEAD` and refs, only for commands that mention commit or push.
