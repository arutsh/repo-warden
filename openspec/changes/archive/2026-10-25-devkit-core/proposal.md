# Proposal

## Why

The dev-workflow rules the user relies on (don't read secret files, keep comments short, prefer test factories, follow the branch convention) live as Claude-only hooks inside the GrandFlow repo. They are registered by absolute GrandFlow paths, so they don't apply to any other repo or any other agent. A shared, opt-in devkit inside the repo-warden repo gives every repo and every supported agent the same rules. It also lets each user and repo choose which rules apply.

## What Changes

- **A new, separate package in this repo**, `devkit/`:
  - It is a uv workspace member with its own `devkit` command.
  - repo-warden (the broker) does not depend on it.
  - Installing repo-warden installs nothing from the devkit.
- **An agent-neutral rule engine.** Each rule is a plain function from a normalised event to a decision:
  - the event is one of shell command, file read, file write or file edit;
  - the decision is allow, warn or deny, with a reason.
- **Four rules ported from GrandFlow's hooks:**
  - `secrets` (a guard rule). It blocks shell commands, reads and edits that touch secret files. Its default patterns match repo-warden's sensitive paths, and it now also covers the agent's own read and search tools.
  - `comment_brevity`. It blocks newly introduced comment blocks of three or more lines.
  - `test_factories`. It warns about hand-built model objects where a factory exists. It is off by default.
  - `branch_name`. It checks created branches against a configurable pattern. It is off by default.
- **Agent adapters for Claude Code and OpenAI Codex CLI** (`devkit hook claude|codex <event>`). Each translates that agent's hook input into events and the decisions back into its output format. The Codex adapter parses `apply_patch` so that file edits are checked too.
- **The same rules outside any agent:**
  - `devkit check --staged` for a pre-commit hook;
  - `devkit check --range BASE..HEAD` for CI.

  These still catch problems when no agent hook is installed.
- **Opt-in at four levels:**
  1. package install;
  2. agent, with `devkit install|uninstall --agent claude,codex` and `--git-hooks`;
  3. repo, through the user's `repos: opted-in|all` setting, where `devkit init` writes `.devkit.yaml`;
  4. rule, with each rule set to `off|warn|block`.
- **Config is layered:** built-in defaults, then `~/.config/devkit/config.yaml`, then the repo's `.devkit.yaml`.
  - The agent can edit the repo file, so the repo may only tighten guard rules.
  - There is no environment-variable bypass.
- **`devkit status`** shows every rule's effective level and which layer set it. It also shows which agents and git hooks are installed.
- **`devkit init`** writes a commented `.devkit.yaml`. It can also write an `AGENTS.md` template (with `@AGENTS.md` in `CLAUDE.md`), generic openspec `rules:` and a CI workflow.
- **Documentation:** a devkit README stating that in-agent hooks are early feedback, not a security boundary. The sandbox, repo-warden, pre-commit and CI enforce.

## Capabilities

### New Capabilities
- `devkit/rule-engine`: the normalised event model, decisions, how rules are run, and the four rules' behaviour.
- `devkit/configuration`: config locations, layering, repo scope, per-rule levels, guard-rule tighten-only, and `devkit status`.
- `devkit/agent-hooks`: the Claude and Codex adapters, their input and output mapping, failure behaviour, and `devkit install|uninstall --agent`.
- `devkit/repo-checks`: `devkit check` (staged and range), the git pre-commit hook installer, the CI template and `devkit init`.

### Modified Capabilities
<!-- none: the broker's behaviour does not change -->

## Impact

- **New code:**
  - `devkit/` (its own `pyproject.toml`, `src/devkit/`, `tests/`, `templates/`, `README.md`);
  - a `[tool.uv.workspace]` entry in the root `pyproject.toml`;
  - a `uv.lock` update.
- **Dependencies:** PyYAML only, which is already a repo-warden dependency. There is no dependency on `repo_warden` or mcp-airlock, so hook start-up stays fast.
- **Files on the user's machine, written only by explicit `devkit install` / `init` commands:**
  - `~/.claude/settings.json`;
  - `~/.codex/hooks.json`;
  - `<repo>/.git/hooks/pre-commit`;
  - `.devkit.yaml`, `AGENTS.md` and `CLAUDE.md` in the repo;
  - `openspec/config.yaml`;
  - `.github/workflows/devkit.yml`.
- **GrandFlow:** unchanged by this change. Switching GrandFlow over (removing its project hooks and opting in) is a documented manual step.
- **Out of scope (a later `devkit-flow` change):**
  - porting `flow.py`;
  - branch-naming and close-linked-issues workflows;
  - the commit-push-pr skill;
  - adapters for other agents (Cursor, Gemini, Copilot).
