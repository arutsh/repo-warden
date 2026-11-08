"""`devkit review plan | diff | record | skip | verdict | reset`: what the devkit-review skill calls in a round."""

from __future__ import annotations

import json
import os
import shlex
from datetime import datetime, timezone
from typing import Any, TextIO

from .. import config, gitsafe
from ..repo import find_top
from ..rules.review import FOCUSES, MODELS, Review
from . import hooks, log, machine, snapshot, state

OUTCOMES = ("fixed", "discuss", "question", "dropped")


class ReviewError(Exception):
    pass


def _active(data: dict[str, Any]) -> list[str]:
    return [sid for sid, s in data["sessions"].items() if s.get("loop")]


def _pick(data: dict[str, Any], session: str | None) -> str:
    """The session whose loop a subcommand acts on: the named one, else the repo's only active loop."""
    active = _active(data)
    if session:
        if session not in active:
            raise ReviewError(f"no review loop is active for session {session}")
        return session
    if not active:
        raise ReviewError("no review loop is active in this repo")
    if len(active) > 1:
        raise ReviewError("more than one review loop is active here; pass --session (one of: "
                          + ", ".join(sorted(active)) + ")")
    return active[0]


def followup(settings: dict[str, Any]) -> dict[str, str]:
    """Round 2+ focuses. One named in `followup_reviewers` but not in `reviewers` is still run, on its
    default model: it was asked for by name, and config can only check one key at a time."""
    defaults = Review.settings["reviewers"]
    return {f: settings["reviewers"].get(f, defaults[f]) for f in settings["followup_reviewers"]}


def _start(top, data: dict[str, Any], session: str | None, settings: dict[str, Any], cur: str,
           now: datetime) -> str:
    """An on-demand loop: the session's changes since its last review, as if the Stop hook had asked.
    `cur` is snapshotted by the caller before the lock is taken, as the hooks do."""
    if session is None and not data["sessions"]:
        raise ReviewError("no agent session is known in this repo, so there is nothing to attach a review to; "
                          "pass --session <id> or start it from the agent")
    sid = session or max(data["sessions"], key=lambda k: data["sessions"][k]["updated"])
    s = data["sessions"].get(sid)
    if s is None:
        try:  # nothing seen yet: the changes since HEAD, or since nothing in a repo with no commits
            base = gitsafe.run(top, "rev-parse", "HEAD^{tree}").decode().strip()
        except gitsafe.GitError:
            base = gitsafe.run(top, "hash-object", "-t", "tree", "--stdin", stdin=b"").decode().strip()
        s = data["sessions"][sid] = {"baseline": base, "turn_start": cur, "last_reviewed": None, "loop": None,
                                     "warned": None, "updated": now.isoformat()}
    base = s.get("last_reviewed") or s["baseline"]
    if snapshot.missing(top, [base]):
        base = s["baseline"] = cur
    if cur == base or cur in data["reviewed"]:
        raise ReviewError("nothing to review: no changes since the last reviewed snapshot")
    s["loop"] = machine.new_loop(sid, base, cur, settings["max_rounds"], now)
    s["updated"] = now.isoformat()
    return sid


def plan(top, settings: dict[str, Any], secrets: list[str], session: str | None, start: bool, out: TextIO) -> None:
    now = datetime.now(timezone.utc)
    cur = snapshot.snapshot(top, secrets) if start else ""  # a slow snapshot must not hold the lock the hooks wait on
    with state.locked(top, now=now) as data:
        running = bool(_active(data)) if session is None else bool((data["sessions"].get(session) or {}).get("loop"))
        events = ()
        if start and not running:
            sid = _start(top, data, session, settings, cur, now)
            events = machine.started(sid, data["sessions"][sid]["loop"], "on_demand")
        else:
            sid = _pick(data, session)
        loop = data["sessions"][sid]["loop"]
    log.write(log.event_records(top, events, now), settings, now=now)
    reviewers = dict(settings["reviewers"]) if loop["round"] == 1 else followup(settings)
    json.dump({"active": True, "round": loop["round"], "max": loop["max"], "session": sid, "reviewers": reviewers,
               "checker": settings["checker"], "scope": f"devkit review diff --session {shlex.quote(sid)}"},
              out, indent=2)
    out.write("\n")


def diff(top, secrets: list[str], session: str | None, out: TextIO) -> None:
    with state.locked(top) as data:
        loop = data["sessions"][_pick(data, session)]["loop"]
    try:
        out.write(snapshot.diff(top, loop["from"], loop["at"], secrets))
    except gitsafe.GitError as e:
        raise ReviewError(f"cannot diff round {loop['round']}: {e}") from e


def _set_record(data: dict[str, Any], session: str | None, rec: dict[str, Any]) -> dict[str, Any]:
    """A round's record is written once, so a recorded skip can't be replaced by a review summary or back.
    Returns the log context of the round."""
    sid = _pick(data, session)
    loop = data["sessions"][sid]["loop"]
    if loop["record"] is not None:
        raise ReviewError(f"round {loop['round']} is already recorded; nothing changed")
    loop["record"] = rec
    return log.context(sid, loop)


def skip(top, settings: dict[str, Any], session: str | None, reason: str) -> None:
    if not reason.strip():
        raise ReviewError("--reason must say why the review is skipped")
    now = datetime.now(timezone.utc)
    with state.locked(top, now=now) as data:
        ctx = _set_record(data, session, {"skipped": reason.strip()})
    note = reason.strip() if settings["log_content"] else None
    log.write([log.make(top, "round_recorded", {**ctx, log.PREFIX + "skipped": True, log.PREFIX + "note": note},
                        f"review round {ctx[log.PREFIX + 'round']} skipped", now)], settings, now=now)


def _int(v: Any) -> bool:
    return type(v) is int


def validate_findings(findings: Any) -> list[str]:
    """Why `findings` can't be recorded, one line per problem."""
    if not isinstance(findings, list):
        return ["expected a JSON array of findings"]
    problems = []
    for i, f in enumerate(findings):
        at = f"finding {i}"
        if not isinstance(f, dict):
            problems.append(f"{at}: expected an object")
            continue
        outcome = f.get("outcome")
        if outcome not in OUTCOMES:
            problems.append(f"{at}: outcome must be one of {', '.join(OUTCOMES)}")
        if f.get("focus") not in FOCUSES:
            problems.append(f"{at}: focus must be one of {', '.join(FOCUSES)}")
        if f.get("model") not in MODELS:
            problems.append(f"{at}: model must be one of {', '.join(MODELS)}")
        if not isinstance(f.get("claim"), str) or not f["claim"]:
            problems.append(f"{at}: claim must be a non-empty string")
        # A finding dropped for having no evidence has no file, line or score to give.
        dropped = outcome == "dropped"
        if not dropped or f.get("file") is not None:
            if not isinstance(f.get("file"), str) or not f["file"]:
                problems.append(f"{at}: file must be a non-empty string")
        if not dropped or f.get("line") is not None:
            if not _int(f.get("line")) or f["line"] < 1:
                problems.append(f"{at}: line must be a positive integer")
        if not dropped or f.get("score") is not None:
            if not _int(f.get("score")) or not 0 <= f["score"] <= 100:
                problems.append(f"{at}: score must be an integer from 0 to 100")
        if dropped and (not isinstance(f.get("reason"), str) or not f["reason"]):
            problems.append(f"{at}: a dropped finding needs a reason")
        for k in ("quote", "failure_scenario", "reason"):
            if f.get(k) is not None and not isinstance(f[k], str):
                problems.append(f"{at}: {k} must be a string")
    return problems


def counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    """fixed, open (to discuss, or put as a question) and dropped, derived so the agent can't misreport them."""
    n = [f["outcome"] for f in findings]
    return {"fixed": n.count("fixed"), "open": n.count("discuss") + n.count("question"), "dropped": n.count("dropped")}


def record(top, settings: dict[str, Any], secrets: list[str], session: str | None,
           stdin: TextIO) -> tuple[dict[str, int], list[str]]:
    """The round's counts, and the logged ids of the findings left open (for the skill's To discuss list)."""
    try:
        findings = json.load(stdin)
    except (ValueError, RecursionError) as e:  # as in state.read: a deeply nested array overflows the parser
        raise ReviewError(f"findings are not valid JSON: {type(e).__name__}: {e}") from e
    problems = validate_findings(findings)
    if problems:
        raise ReviewError("findings rejected, nothing recorded:\n  " + "\n  ".join(problems))
    now = datetime.now(timezone.utc)
    with state.locked(top, now=now) as data:
        ctx = _set_record(data, session, rec := counts(findings))
    p = log.PREFIX
    out = [log.make(top, "round_recorded", {**ctx, **{p + k: v for k, v in rec.items()}},
                    f"review round {ctx[p + 'round']} recorded", now)]
    out += [log.finding_record(top, ctx, f, content=settings["log_content"], secrets=secrets, now=now)
            for f in findings]
    if not log.write(out, settings, now=now):
        return rec, []  # an id that wasn't logged can't take a verdict
    return rec, [r["attributes"][p + "finding_id"] for r in out[1:]
                 if r["attributes"][p + "outcome"] in ("discuss", "question")]


def verdict(top, settings: dict[str, Any], fid: str, value: str, note: str | None) -> None:
    """A verdict on a finding recorded within the retention period; nothing is written if there is none."""
    if not settings["log"]:
        raise ReviewError("review logging is off (rules.review.log: false), so no finding can be found")
    p = log.PREFIX
    found = None
    for r in log.read(settings["log_retention_days"]):
        a = r["attributes"]
        if a.get("event.name") == p + "finding" and a.get(p + "finding_id") == fid:
            found = a
    if found is None:
        raise ReviewError(f"no finding {fid} was recorded in the last {settings['log_retention_days']} days")
    # The finding's repo, not the one verdict runs in: attrs override make's repo_attrs(top).
    keep = ("devkit.repo.name", "devkit.repo.path_hash", "session.id", p + "loop_id", p + "round", p + "focus",
            p + "model", p + "outcome", "code.file.path", "code.line.number")
    attrs = {**{k: found[k] for k in keep if k in found}, p + "finding_id": fid, p + "verdict": value,
             p + "note": note if note and settings["log_content"] else None}
    now = datetime.now(timezone.utc)
    if not log.write([log.make(top, "verdict", attrs, f"finding {fid} {value}", now)], settings, now=now):
        raise ReviewError("the verdict was not recorded")


def reset(top) -> bool:
    return state.reset(top)


def run(args, stdin: TextIO, out: TextIO, err: TextIO) -> int:
    top = find_top(os.getcwd())
    if top is None:
        print("devkit: not in a git repository", file=err)
        return 2
    try:
        cfg = config.load(top, strict=False)
        settings = cfg.rules["review"].settings
        secrets = hooks.secret_patterns(cfg)
        sub = args.review_cmd
        if sub == "plan":
            plan(top, settings, secrets, args.session, args.start, out)
        elif sub == "diff":
            diff(top, secrets, args.session, out)
        elif sub == "record":
            rec, open_ids = record(top, settings, secrets, args.session, stdin)
            print(f"recorded: {rec['fixed']} fixed, {rec['open']} open, {rec['dropped']} dropped", file=out)
            if open_ids:
                print("open finding ids: " + " ".join(open_ids), file=out)
        elif sub == "skip":
            skip(top, settings, args.session, args.reason)
            print("review skipped; the next stop goes ahead and tells the user why", file=out)
        elif sub == "verdict":
            verdict(top, settings, args.finding_id, args.verdict, args.note)
            print(f"verdict recorded: {args.finding_id} {args.verdict}", file=out)
        else:
            print("review state cleared" if reset(top) else "no review state to clear", file=out)
    except (ReviewError, gitsafe.GitError, state.LockTimeout, OSError) as e:
        print(f"devkit: {e}", file=err)
        return 1
    return 0
