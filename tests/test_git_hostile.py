"""A repository whose config, hooks, attributes and worktree config try to run code; git.* must never run it."""

import os
import subprocess
from pathlib import Path

import pytest

from repo_warden import gitcmd
from repo_warden.audit import Auditor
from repo_warden.broker import Broker
from repo_warden.capabilities import Registry
from repo_warden.execution import DirectBackend
from repo_warden.handlers import git as git_mod
from repo_warden.paths import SensitiveMatcher
from repo_warden.policy import PolicyGate, load_policy
from repo_warden.repo import open_repo

from .conftest import git

POLICY = ("tools:\n  git.status: { tiers: { local: L0 } }\n  git.diff: { tiers: { local: L0 } }\n"
          "  git.log: { tiers: { local: L0 } }\n")
HOOKS = ("post-index-change", "pre-commit", "post-checkout", "reference-transaction", "pre-auto-gc", "post-rewrite")
# vector -> extra script body after the touch (filters must still pass content through)
SCRIPTS = {"fsmonitor": "", "clean": "cat", "smudge": "cat", "process": "exit 1", "textconv": 'cat "$1"',
           "diffcmd": "", "external": "", "gpg": "exit 1", "wt_fsmonitor": ""}
SIG = ("gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n =abcd\n -----END PGP SIGNATURE-----\n")


def raw_git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Plain git with the repo's own config honoured: what an unhardened tool would run."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


@pytest.fixture
def hostile(tmp_path: Path, repo_dir: Path):
    marks = tmp_path / "marks"
    marks.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in SCRIPTS.items():
        s = bin_ / name
        s.write_text(f"#!/bin/sh\ntouch {marks}/{name}\n{body}\n")
        s.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for h in HOOKS:
        (hooks / h).write_text(f"#!/bin/sh\ntouch {marks}/hook-{h}\n")
        (hooks / h).chmod(0o755)

    r = repo_dir
    for f in ("f.txt", "p.dat", "d.txt", "e.txt", "s.txt"):
        (r / f).write_text(f"{f}\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    # A signed-looking commit on top, so log.showSignature would invoke gpg.program.
    body = git(r, "cat-file", "commit", "HEAD")
    head, _, msg = body.partition("\n\n")
    signed = f"{head}\n{SIG}\n{msg}"
    oid = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=r, input=signed,
                         capture_output=True, text=True, check=True).stdout.strip()
    git(r, "reset", "-q", "--soft", oid)  # not update-ref: cplt's git guard refuses it

    wt = tmp_path / "wt"  # a linked worktree next to the main checkout
    git(r, "worktree", "add", "-q", str(wt))

    cfg = {"core.fsmonitor": bin_ / "fsmonitor", "core.hooksPath": hooks,
           "filter.evil.clean": bin_ / "clean", "filter.evil.smudge": bin_ / "smudge",
           "filter.evil.required": "true", "filter.proc.process": bin_ / "process",
           "diff.tc.textconv": bin_ / "textconv", "diff.cmd.command": bin_ / "diffcmd",
           "diff.external": bin_ / "external", "log.showSignature": "true", "gpg.program": bin_ / "gpg",
           "extensions.worktreeConfig": "true"}
    for k, v in cfg.items():
        git(r, "config", k, str(v))
    git(wt, "config", "--worktree", "core.fsmonitor", str(bin_ / "wt_fsmonitor"))
    attrs = "f.txt filter=evil\np.dat filter=proc\nd.txt diff=tc\ne.txt diff=cmd\n"
    for top in (r, wt):
        (top / ".gitattributes").write_text(attrs)
        for f in ("f.txt", "p.dat", "d.txt", "e.txt"):
            with open(top / f, "a") as fh:
                fh.write("changed\n")
        (top / "s.txt").write_text("staged\n")
        subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "add", "s.txt"],
                       cwd=top, check=True, capture_output=True)
    for p in marks.iterdir():  # setup itself may have fired a vector
        p.unlink()
    return {"repo": r, "worktree": wt, "marks": marks}


async def _broker_for(top: Path, tmp_path: Path):
    backend = DirectBackend(top)
    ctx = await open_repo(top, backend)
    pf = tmp_path / "policy.yaml"
    pf.write_text(POLICY)
    lp = load_policy(pf, ctx.root)
    auditor = Auditor.open(tmp_path / f"audit-{top.name}.jsonl", ctx, "direct")
    broker = Broker(registry=Registry([git_mod.STATUS, git_mod.DIFF, git_mod.LOG]), gate=PolicyGate(lp.policy),
                    auditor=auditor, repo=ctx, backend=backend, sensitive=SensitiveMatcher())
    return broker, ctx, auditor


async def _run_all(top: Path, tmp_path: Path) -> list:
    broker, ctx, auditor = await _broker_for(top, tmp_path)
    try:
        return [await broker.execute("git.status", {}), await broker.execute("git.diff", {}),
                await broker.execute("git.diff", {"staged": True}), await broker.execute("git.log", {}),
                await broker.execute("git.log", {"path": "f.txt"})]
    finally:
        auditor.close()
        ctx.close()


def marks(h) -> set[str]:
    return {p.name for p in h["marks"].iterdir()}


def test_every_vector_is_live(hostile):
    """Without hardening each vector fires, so the 'no marker' assertions below mean something."""
    r, wt = hostile["repo"], hostile["worktree"]
    raw_git(r, "status")
    raw_git(r, "-c", "filter.proc.process=", "diff", "--no-ext-diff")                # clean, smudge, textconv
    raw_git(r, "-c", "filter.proc.process=", "-c", "diff.external=", "diff", "e.txt")  # diffcmd
    raw_git(r, "-c", "filter.evil.clean=cat", "-c", "filter.proc.process=", "diff")  # external
    raw_git(r, "diff")                                                                # process
    raw_git(r, "log", "-1")                                                           # gpg
    raw_git(wt, "status")                                                             # wt_fsmonitor
    with open(r / "f.txt", "a") as fh:  # a new stat makes status refresh, and so write, the index
        fh.write("again\n")
    subprocess.run(["git", "-c", "core.fsmonitor=false", "status"], cwd=r, capture_output=True,
                   env={**os.environ, "GIT_OPTIONAL_LOCKS": "1"})                    # hooks
    assert marks(hostile) >= set(SCRIPTS) | {"hook-post-index-change"}


@pytest.mark.parametrize("where", ["repo", "worktree"])
async def test_hardened_git_runs_nothing(hostile, tmp_path, where):
    results = await _run_all(hostile[where], tmp_path)
    assert marks(hostile) == set()
    status, diff, staged, log, log_path = results
    assert status.ok, status.error
    assert {e["path"] for e in status.data["entries"]} >= {"f.txt", "s.txt", ".gitattributes"}
    assert diff.ok and "+changed" in diff.data["diff"] and "d.txt" in diff.data["diff"]
    assert staged.ok and "+staged" in staged.data["diff"]
    assert log.ok and len(log.data["commits"]) == 1 and log_path.ok


async def test_removing_overrides_fires_vectors(hostile, tmp_path, monkeypatch):
    """The -c overrides are what keeps the markers away (the test above fails without them)."""
    monkeypatch.setattr(gitcmd, "BASE_OVERRIDES", ())

    async def none(*a, **k):
        return []

    monkeypatch.setattr(git_mod, "repo_overrides", none)
    await _run_all(hostile["repo"], tmp_path)
    await _run_all(hostile["worktree"], tmp_path)
    # gpg, textconv and the diff commands stay off through --no-show-signature, --no-textconv, --no-ext-diff.
    assert marks(hostile) >= {"fsmonitor", "wt_fsmonitor", "clean"}
