# Tasks

Unblocked by cplt `2026.11.06-074203-2265661`. Checked 2026-11-08: inside `cplt exec`, `git write-tree` works, also with `GIT_INDEX_FILE` set to an index copy in `~/.cache`, and its id equals host git's. `mktree` and `commit-tree` are still refused; devkit needs neither.

## 1. Use write-tree

- [x] 1.1 In `devkit/src/devkit/review/snapshot.py`, make `snapshot()` return `git("write-tree")`'s id. Remove `_write_trees`, and the `hashlib` and `Callable` imports. `rev-parse` then needs only `--git-path index`, without `--show-object-format`. Keep the `stdin` parameter of `gitsafe.run`: `missing()` and `review/command.py` still use it. No fallback for older cplt versions.
- [x] 1.2 In `devkit/tests/test_snapshot.py`, turn `test_id_equals_write_tree` into a behaviour test over the same fixture (sha1 and sha256 repos, nested dirs, an executable, a symlink, a submodule). Check the snapshot's entries and modes with `git ls-tree -r`, rather than comparing it with `write-tree`. Verify that the other snapshot tests pass unchanged.

## 2. Docs

- [x] 2.1 In the devkit README's review section, rewrite the "Inside cplt" paragraph: snapshots use `git write-tree`, which cplt allows from `2026.11.06-074203-2265661`. That release is the minimum cplt for running devkit review inside cplt; an older one refuses the snapshot.
- [x] 2.2 In `openspec/changes/devkit-review/design.md` D4, step 3 becomes `git write-tree` on the index copy. Replace the paragraph about cplt refusing `write-tree` with the minimum cplt version.

## 3. Check and release

- [x] 3.1 Re-run the devkit-review task 2.4 check under the new cplt:
  - `cplt exec -- .venv/bin/python -m pytest devkit/tests/test_snapshot.py devkit/tests/test_gitsafe.py -p no:cacheprovider` from the repo-warden checkout;
  - the scratch-repo check: a scratch repo under `~/repos/`, cplt launched from inside it, repo-warden granted with `--allow-exec /home/noro/repos/repo-warden` (in this cplt release, top-level flags go before `exec`: `cplt --allow-exec … exec -- …`), and the devkit cache in `~/.cache`. Verify that a snapshot is taken, that objects land in `.git/objects`, and that no ref changes. Delete the scratch repo afterwards.
- [x] 3.2 Bump devkit's patch version (`0.2.0` → `0.2.1`) and run `uv lock`. Verify that the full test suite passes.
