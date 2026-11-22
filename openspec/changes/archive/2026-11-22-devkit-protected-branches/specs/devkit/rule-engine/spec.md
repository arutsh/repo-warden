# Spec Delta

## ADDED Requirements

### Requirement: protected_branches rule
The `protected_branches` rule is a guard rule and SHALL default to `block`. It SHALL judge the agent's shell commands only. It SHALL NOT run under `devkit check` or the git pre-commit hook, so the user's own commits are never judged.

The protected branches SHALL be `main` and `master`, plus any names added by the `branches` setting. The rule SHALL report a finding when a command, in any of its `&&`, `||`, `;`, `|` or newline-separated parts, does either of these:
- runs `git commit` (with any options, `--amend` included) while the branch it would commit to is protected;
- runs `git push` that would update or delete a protected branch on a remote. That is:
  - a refspec whose destination is a protected branch (`main`, `+main`, `HEAD:main`, `feature:main`, `:main`, `refs/heads/main`);
  - `--delete` naming a protected branch;
  - `--all` or `--mirror`;
  - a push that names no branch, or names only `HEAD`, while on a protected branch.

A script passed to `sh`, `bash`, `zsh` or `dash` with `-c` (as Codex runs every command, `bash -lc '...'`) SHALL be judged the same way. Redirections such as `2>&1` and `> file` SHALL NOT split a part or count as arguments.

The branch a command acts on SHALL be read from the repo it runs in. That repo is named by `git -C <dir>`, or by an earlier `cd <dir>` in the same command, or else is the event's working directory. An earlier `git switch`, `git switch -c`, `git checkout <branch>` or `git checkout -b` in the same command SHALL change the branch that later parts are judged against.

Text that git or the shell treats as data SHALL NOT be judged as a command: heredoc bodies, and the arguments of `-m` or `-F`.

These SHALL NOT be reported:
- pushes of tags only;
- commits and pushes on branches that aren't protected;
- `gh pr merge`;
- a detached HEAD;
- a directory that isn't a git repository;
- a repo listed in `exempt_repos`.

The reason SHALL name the branch, and SHALL tell the agent to create a branch, commit and push there, and open a pull request. If the change must land on the protected branch directly, the reason SHALL tell the agent to ask the user to do it themselves.

Like `secrets`, this is early feedback and not a security boundary. A command that hides `git` (an alias, a script file, `eval`) gets past it.

#### Scenario: Commit and push on main
- **WHEN** the current branch is `main` and the agent runs `git add -A && git commit -m "x" && git push origin main`
- **THEN** the command gets a `protected_branches` finding naming `main`, and it is blocked

#### Scenario: Bare push while on main
- **WHEN** the current branch is `main` and the agent runs `git push`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Push to main from a feature branch
- **WHEN** the current branch is `feat/x` and the agent runs `git push origin HEAD:main`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Codex-style wrapped command
- **WHEN** the current branch is `main` and the agent's command is `bash -lc 'git commit -m x && git push origin main'`
- **THEN** the command gets a `protected_branches` finding

#### Scenario: Feature branch work allowed
- **WHEN** the current branch is `feat/x` and the agent runs `git commit -m "fix main menu" && git push -u origin feat/x`
- **THEN** there is no finding

#### Scenario: Branching off main first
- **WHEN** the current branch is `main` and the agent runs `git switch -c feat/y && git commit -m "y" && git push -u origin feat/y`
- **THEN** there is no finding

#### Scenario: Merging through GitHub
- **WHEN** the agent runs `gh pr merge 12 --merge`
- **THEN** there is no finding

#### Scenario: Repo adds a protected branch
- **WHEN** `.devkit.yaml` sets `protected_branches: { branches: [release] }` and the agent runs `git push origin release`
- **THEN** the command gets a finding

#### Scenario: Exempt repo
- **WHEN** the user config lists `~/dotfiles` in `exempt_repos` and the agent commits on `main` there
- **THEN** there is no finding
