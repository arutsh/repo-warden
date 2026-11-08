# Proposal

Status: active since 2026-11-08. Written 2026-10-31 as a parked draft. cplt `2026.11.06-074203-2265661` allows `git write-tree`, so step 2 of "What Changes" applies; see `tasks.md`.

## Why

devkit's review snapshots (the `devkit-review` change, task group 2) need a git tree id for the working tree. The natural way to get one is `git write-tree` on a copy of the index.

Inside cplt that call fails. cplt's git guard is a `git` wrapper on PATH. As part of push prevention, it refuses every subcommand it doesn't recognise, because an unknown name could be an alias that expands to `git push`. With cplt `2026.10.02-134616-4970b0e`, checked 2026-10-31, it refuses:
- `write-tree`
- `mktree`
- `commit-tree`

It allows:
- `add`
- `rm --cached`
- `ls-files -s`
- `cat-file`
- `rev-parse`
- `diff`
- `hash-object -w`, including `-t tree --stdin-paths`

So devkit re-implements `write-tree` in `_write_trees()`, in `devkit/src/devkit/review/snapshot.py`:
1. Read `git ls-files -s -z` from the index copy.
2. Build each directory's tree body in Python, in git's entry order (a directory sorts as `name/`), with modes 100644, 100755, 120000, 160000 and 40000.
3. Compute each tree's id locally, using sha1 or sha256 as `rev-parse --show-object-format` reports.
4. Write all trees with one `git hash-object -w -t tree --no-filters --stdin-paths` call, and check that git's ids equal the computed ones.

`test_id_equals_write_tree` in `devkit/tests/test_snapshot.py` checks that the result equals real `write-tree` output, in sha1 and sha256 repos, with nested dirs, an executable, a symlink and a submodule.

The workaround works, but it is about 40 lines that copy git internals devkit shouldn't have to own.

## Why cplt config can't fix it today

The `[git_guard]` keys are:
- `enabled`
- `mode` (`block`, `warn` or `audit`)
- `prevent_push`
- `prevent_force_push`
- `protect_default_branch_only`
- `allow_push`

None of them allows extra subcommands, and each way to let `write-tree` through also weakens push protection:
- `mode = "warn"` lets pushes to `main` and force pushes run, with only a warning.
- `enabled = false`, or `--no-git-guard`, turns the guard off entirely.
- `prevent_push = false` probably lifts the unknown-subcommand check, which is part of push prevention (not tested). It also allows pushes.
- Calling `/usr/bin/git` directly goes around the guard. devkit must not quietly bypass a control the user turned on, so this option is rejected.

## What Changes

These steps depend on cplt.

1. **Upstream request to cplt.** Ask cplt to add `write-tree` and `mktree` to the git guard's known, allowed subcommands. Both only write local objects and can't contact a remote, like `hash-object`, which is already allowed. A per-subcommand allowlist in `[git_guard]`, for example `allow_subcommands = ["write-tree"]`, would also solve it.
2. **Once a cplt release allows `write-tree`:**
   - Replace `_write_trees()` with `gitsafe.run(top, "write-tree", env=env)`.
   - Drop `test_id_equals_write_tree`, or keep it as a plain behaviour test.
   - Remove `stdin=` from `gitsafe.run` if nothing else uses it.
   - Re-run the devkit-review task 2.4 check under cplt (snapshot tests plus the scratch-repo check with the cache in `~/.cache`). See "Re-checking under cplt" below.
   - Update the "Inside cplt" paragraph in the devkit README's `review` section, and D4 in the devkit-review design (or its archived copy).
   - Decide whether to keep the hash-object path as a fallback for older cplt versions. Option: try `write-tree` first, and fall back when the guard refuses it. That keeps both code paths, so it is only worth it if older cplt versions matter.
3. **If cplt never allows it:** close this change and keep the workaround. Nothing else needs to happen.

No spec-level behaviour changes: snapshots give the same tree ids either way. So this change sets `skip_specs: true`.

## Re-checking under cplt

From devkit-review task 2.4, and the cplt quirks in memory:
- Run the tests with `cplt exec -- .venv/bin/python -m pytest devkit/tests/test_snapshot.py devkit/tests/test_gitsafe.py -p no:cacheprovider` from the repo-warden checkout.
- For the scratch-repo check, make the scratch repo the cplt project: create it under `~/repos/` and launch cplt from inside it. Grant repo-warden with `--allow-exec /home/noro/repos/repo-warden`, not `-d`. With `-d`, the scratch repo wasn't visible, and the command started in repo-warden instead.
- `/tmp` inside cplt is private.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
<!-- none: an implementation swap with identical results -->

## Impact

- **Code:**
  - `devkit/src/devkit/review/snapshot.py`: `_write_trees` is removed, and the `hashlib` import is dropped;
  - `devkit/src/devkit/gitsafe.py`: possibly the `stdin` parameter;
  - `devkit/tests/test_snapshot.py`.
- **Docs:** the devkit README's `review` section, and devkit-review D4.
- **Dependency:** a cplt release that allows `write-tree` must be the minimum supported cplt for running devkit review inside cplt. The README should say so.
- **Risk:** low. Tree ids stay the same, so existing review state stays valid.
