# Proposal

## Why

On 2026-11-22 a Claude Code session in GrandFlow committed on `main`, then ran `git push origin main` in the same Bash call, without a PR. GitHub's protection on `main` didn't stop it: `enforce_admins` is off, and the user is the repo's admin. devkit had no rule for it: `branch_name` only checks the names of new branches. Agents should work on a branch and open a PR, and devkit should tell them so before the commit or push runs.

## What Changes

- **A new guard rule, `protected_branches`**, at level `block` by default. It runs in every repo once the agent hook is installed, like `secrets` and `self_guard`. It blocks two kinds of agent shell command:
  - `git commit` while the branch it would commit to is protected;
  - `git push` that would update or delete a protected branch on the remote. This covers:
    - an explicit destination (`origin main`, `HEAD:main`, `+main`, `:main`, `refs/heads/main`);
    - a push that names no branch, made while on a protected branch (`git push`, `git push origin`, `git push origin HEAD`);
    - `--all` and `--mirror`.
- The block message tells the agent to create a branch, commit and push there, and open a PR. If the user wants the change on `main` itself, the agent should ask them to do it from their own terminal.
- **Settings:**
  - `branches`: the protected branch names, default `[main, master]`. Each layer adds to the list, and the repo layer may add branches too.
  - `exempt_repos`: repo paths where the rule is off. Only the user config can set it.

  As with every guard rule, a repo can't lower the level.
- **Not blocked:**
  - `gh pr merge` and other ways of merging through GitHub;
  - commits and pushes on other branches;
  - pushes of tags;
  - `git merge`, `git rebase` or `cherry-pick` on `main`, since these commits can't reach the remote without a push, which is blocked.
- **README:** the rule is early feedback, not a security boundary. The real protection is GitHub's branch protection with `enforce_admins` on. The README says how that interacts with required reviews when you are the only maintainer.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `devkit/rule-engine`: adds the `protected_branches` rule.
- `devkit/configuration`: the guard-tightening requirement covers `protected_branches`: a repo may add branches but can't remove them, lower the level, or set `exempt_repos`.

## Impact

- **New code:**
  - `devkit/src/devkit/rules/protected_branches.py`;
  - a registry entry;
  - tests.
- **Changed:**
  - `devkit/README.md` (the rule, the opt-out, GitHub's `enforce_admins`);
  - `devkit status` shows the new rule automatically.
- **Hook cost:** only commands that contain `git commit` or `git push` run git, a single quick hardened call to read the current branch (and its upstream for a bare push). Every other command costs a regex check.
- **Behaviour change for users:** in every repo, agents can no longer commit on or push to `main` or `master`. Users who want an agent working on `main` somewhere (a dotfiles repo, say) list it in `exempt_repos`, or set the rule to `warn` or `off` in the user config.
- **Version:** devkit 0.3.0 → 0.4.0.
