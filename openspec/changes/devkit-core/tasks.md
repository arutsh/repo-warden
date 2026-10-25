# Tasks

## 1. Walking skeleton: secrets rule blocking in Claude Code — no dependencies

- [x] 1.1 Create the `devkit/` workspace member:
  - `pyproject.toml` (hatchling, `repo-warden-devkit`, Python >= 3.11, dependency `pyyaml`, script `devkit = devkit.cli:main`);
  - `src/devkit/`, `tests/`;
  - in the root `pyproject.toml`, add `[tool.uv.workspace] members = ["devkit"]`.

  Verify that `uv sync --all-packages` succeeds, `uv run devkit --help` works, the root `uv run pytest` still passes, and `uv pip show repo-warden` lists no devkit dependency.
- [x] 1.2 Implement `events.py` and `engine.py` (D3): Event, Finding, Level, strictest-wins Outcome, phase filtering. Verify with unit tests on stub rules for: warn does not block, block wins over warn, `off` rules are not run, and every finding is reported.
- [x] 1.3 Implement `config.py` and `repo.py` (D4, D10):
  - user and repo layers;
  - level shorthand;
  - validation errors that name the file and key;
  - guard-rule tighten-only, with the ignored settings recorded;
  - `repos` scope, and a top level found by walking up to `.git`.

  Verify with tests for every configuration spec scenario, including `secrets: off` ignored from the repo, `exempt` ignored from the repo, a missing file treated as empty, and that `DEVKIT_DISABLE=1` changes nothing.
- [x] 1.4 Implement the `secrets` rule (D9). Verify with:
  - tests for every rule-engine `secrets` scenario (`cat .env`, `grep KEY config/.env.local`, `head ./certs/server.KEY`, `echo environment`, `docs/dotenv.md`, user exemption, a repo-added `*.tfvars`);
  - a parity test asserting that the defaults equal `repo_warden.paths.DEFAULT_SENSITIVE`.
- [x] 1.5 Implement the Claude adapter (D5) and `devkit hook claude pre-tool-use|post-tool-use`, with fail-open (D8). Verify with recorded-payload tests for: a Bash block, an allowed command with no output, a Read/Grep/Glob reference to `.env*`, malformed JSON giving exit 0 with a warning, and no `"allow"` ever emitted.
- [x] 1.6 Implement `devkit install|uninstall --agent claude` (D7) and `devkit status`. Verify with tests on a temp `HOME` for:
  - idempotent install;
  - user hooks and permissions preserved;
  - the backup written once;
  - invalid JSON refused with the file unchanged;
  - uninstall removes only devkit entries;
  - status shows levels, their sources and the install state.
- [x] 1.7 Write `devkit/README.md` covering:
  - what the devkit is;
  - install (`uv tool install ./devkit`, `devkit install --agent claude`);
  - the config file format and its layers;
  - opt-in and opt-out at each level;
  - the "early feedback, not a security boundary" section pointing to cplt and repo-warden.

  Link it from the root README. Verify by installing on this machine and running a real Claude Code session in a scratch repo, where `cat .env` is denied with the devkit reason.
- [x] 1.8 Run `uv run pytest` and `uv run --package repo-warden-devkit pytest devkit/tests` clean; PR merged.

## 2. Codex CLI adapter — depends on 1

- [x] 2.1 Implement `patch.py` (D6): parse Add, Update (with Move) and Delete, apply hunks in memory, and fall back to the hunk text. Verify with tests on patches that add a file, update with matching context, update with stale context (fallback), move, and delete, plus a malformed envelope rejected.
- [x] 2.2 Implement the Codex adapter and `devkit hook codex pre-tool-use|post-tool-use`. Verify with recorded-payload tests for: a Bash `cat .env` deny, an `apply_patch` touching `.env.local` denied, an allowed patch with no output, and malformed JSON failing open.
- [x] 2.3 Extend `install|uninstall` and `status` to `--agent codex` (`~/.codex/hooks.json`, the `/hooks` review notice, a warning when `config.toml` disables hooks). Verify with the same installer tests as 1.6 on a temp `HOME`, and the hooks-disabled warning.
- [ ] 2.4 Add the Codex section to the devkit README. Verify by running a real Codex CLI session in a scratch repo after `/hooks` approval: a shell `cat .env` and an `apply_patch` that creates `.env.local` are both denied.

  > **Note (2026-10-18):** the README section is written. The live Codex check is **still to do**, because there is no active Codex account yet. So far the adapter has been tested two ways: with recorded-payload tests, and by running the real `devkit hook codex` command against Codex-shaped JSON. It has not run inside Codex.
  >
  > When an account is available, run the steps above. Also confirm that a `warn`-level finding's `additionalContext` reaches the model. Then tick this task.
- [x] 2.5 Run the devkit and root test suites clean; PR merged.

## 3. Style rules: comment_brevity, test_factories, branch_name — depends on 1 (2 for Codex coverage)

- [x] 3.1 Port `comment_brevity` from GrandFlow (tokenizer and AST for Python, a pattern fallback, only "introduced" violations counted, configurable `max_lines`). Verify with tests for every spec scenario, plus a file that fails to parse using the fallback and an Alembic migration header skipped.
- [x] 3.2 Port `test_factories` (factory discovery in the nearest `tests/factories/`, configurable `discouraged_calls`, `block` treated as `warn`, post phase only). Verify with tests for raw construction with a factory present, no factories directory, a discouraged call, and that it never blocks.
- [x] 3.3 Implement `branch_name` (configurable `pattern` and `base`, the GrandFlow regex for detecting branch creation, flags ignored). Verify with tests for `git switch -c tmp` flagged, a conforming name allowed, `git branch -d old` ignored, and `main` accepted.
- [x] 3.4 Run each style rule through both adapters. Verify with payload tests that a Claude `Edit` and a Codex `apply_patch` adding the same four-line comment produce the same finding, and that `test_factories` warns from PostToolUse in both.
- [x] 3.5 Document each rule and its settings in the devkit README. Verify that the README's example config loads without errors through `devkit status`.
- [ ] 3.6 Run the devkit and root test suites clean; PR merged.

## 4. Repo checks and opting repos in — depends on 1, 3

- [ ] 4.1 Implement `devkit check --staged | --range BASE..HEAD` (D3 check phase, D8 fail-closed): `git show` gives the before and after content, staged secret files are flagged, and `branch_name` checks the current branch. Verify with tests in a temp repo for:
  - a staged `.env.prod` gives exit 1;
  - an untouched existing docstring gives no finding;
  - a malformed `.devkit.yaml` gives exit 2;
  - a range check over two commits.
- [ ] 4.2 Implement `devkit install|uninstall --git-hooks` (D7). Verify with tests for:
  - the hook installed into a linked worktree's hooks path;
  - refusal when `core.hooksPath` is set or a foreign hook exists;
  - a commit staging `id_rsa` refused;
  - uninstall removing only the managed hook.
- [ ] 4.3 Implement `devkit init` with `--agents-md`, `--openspec` and `--ci`, plus the templates (`devkit.yaml`, `AGENTS.md`, `openspec-rules.yaml`, `devkit-ci.yml`). Verify with tests for every repo-checks `init` scenario (no overwrites, `CLAUDE.md` with `@AGENTS.md`, rules only appended when none exist), and that the generated workflow is valid YAML.
- [ ] 4.4 Document check, git hooks, init and the GrandFlow migration steps (design "Migration Plan") in the devkit README. Verify by opting repo-warden itself in (`devkit init`, `devkit install --git-hooks`): a commit that adds a four-line comment is refused, and `devkit status` shows the repo as opted in.
- [ ] 4.5 Run the devkit and root test suites clean, and `devkit check --range origin/main..HEAD` clean on the branch; PR merged.
