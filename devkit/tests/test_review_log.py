import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from devkit.cli import main
from devkit.review import log
from .test_review_command import SECRET_NAME, blocked_round_1, edit, finding, in_repo, run  # noqa: F401
from .test_review_hooks import SID, opted_in, stop  # noqa: F401

P = log.PREFIX
SETTINGS = {"log": True, "log_retention_days": 30}
TEXT = {"quote": "a = 2", "failure_scenario": "a is read before it is set"}


def records():
    d = log.log_dir()
    if not d.is_dir():
        return []
    return [json.loads(line) for p in sorted(d.glob("*.jsonl")) for line in p.read_text().splitlines()]


def events(recs=None):
    return [r["attributes"]["event.name"].removeprefix(P) for r in (records() if recs is None else recs)]


def readme_sample():
    """The sample finding record in the README's review log section."""
    text = (Path(__file__).parents[1] / "README.md").read_text()
    section = text.split("## Review logs", 1)[1]
    return json.loads(re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1))


def record_round(capsys, monkeypatch, findings):
    code, out, err = run(capsys, monkeypatch, "record", stdin=json.dumps(findings))
    assert code == 0, err
    return out


def test_a_round_produces_finding_records(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    before = len(records())
    record_round(capsys, monkeypatch, [finding("fixed"), finding("discuss", line=2),
                                       finding("dropped", reason="pre-existing", score=10)])
    new = records()[before:]
    assert events(new) == ["round_recorded", "finding", "finding", "finding"]
    assert len({(r["attributes"][P + "loop_id"], r["attributes"][P + "round"]) for r in new}) == 1
    rr = new[0]["attributes"]
    assert (rr[P + "fixed"], rr[P + "open"], rr[P + "dropped"], rr["session.id"]) == (1, 1, 1, SID)
    f = new[1]
    assert f["severity_text"] == "INFO" and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z", f["timestamp"])
    assert f["resource"]["service.name"] == "devkit" and f["resource"]["service.version"]
    a = f["attributes"]
    assert re.fullmatch(r"f-[0-9a-f]{8}", a[P + "finding_id"])
    assert (a[P + "focus"], a[P + "model"], a[P + "outcome"], a[P + "score"]) == ("correctness", "opus", "fixed", 80)
    assert (a["code.file.path"], a["code.line.number"]) == ("src/app.py", 1)
    assert a["devkit.repo.name"] == "repo" and re.fullmatch(r"[0-9a-f]{16}", a["devkit.repo.path_hash"])
    assert P + "drop_reason" not in new[3]["attributes"]  # agent free text: only with log_content


def test_finding_id_is_stable():
    assert log.finding_id("l-1", 1, "tests", "a.py", 3, "c") == log.finding_id("l-1", 1, "tests", "a.py", 3, "c")
    assert log.finding_id("l-1", 1, "tests", "a.py", 3, "c") != log.finding_id("l-1", 2, "tests", "a.py", 3, "c")


def test_two_focuses_with_the_same_claim_get_different_ids(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    out = record_round(capsys, monkeypatch, [finding("discuss"), finding("discuss", focus="security")])
    ids = out.splitlines()[1].removeprefix("open finding ids: ").split()
    assert len(set(ids)) == 2


def test_logs_live_in_the_devkit_cache(isolated):
    assert log.log_dir() == isolated / ".cache" / "devkit" / "review" / "logs"


def test_two_rounds_produce_the_expected_sequence_in_one_loop(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    edit(in_repo, "a = 3\n")
    record_round(capsys, monkeypatch, [finding("fixed"), finding("discuss", line=2)])
    assert "round 2" in stop(in_repo)[0]["reason"]
    record_round(capsys, monkeypatch, [finding("question", score=50)])
    stop(in_repo)
    recs = records()
    assert events(recs) == ["loop_started", "round_requested", "round_recorded", "finding", "finding",
                            "round_requested", "round_recorded", "finding", "loop_ended"]
    assert len({r["attributes"][P + "loop_id"] for r in recs}) == 1
    assert [r["attributes"][P + "round"] for r in recs] == [1, 1, 1, 1, 1, 2, 2, 2, 2]
    assert recs[0]["attributes"][P + "trigger"] == "stop" and recs[0]["attributes"][P + "max_rounds"] == 3
    sample = readme_sample()
    assert sample.keys() == recs[3].keys() and sample["attributes"].keys() == recs[3]["attributes"].keys()
    assert sample["resource"].keys() == recs[3]["resource"].keys()
    end = recs[-1]["attributes"]
    assert (end[P + "reason"], end[P + "fixed"], end[P + "open"]) == ("clean", 1, 2)


def test_on_demand_loop_is_logged_as_started(in_repo, capsys, monkeypatch):
    from .test_review_hooks import start_turn

    start_turn(in_repo)
    edit(in_repo, "a = 2\n")
    assert run(capsys, monkeypatch, "plan", "--start")[0] == 0
    recs = records()
    assert events(recs) == ["loop_started", "round_requested"] and recs[0]["attributes"][P + "trigger"] == "on_demand"


def test_log_write_failure_still_blocks_and_warns(opted_in, monkeypatch, tmp_path, capsys):
    from .test_review_hooks import start_turn

    log.log_dir().parent.mkdir(parents=True)
    log.log_dir().write_text("")  # a file where the log directory goes: the state beside it still works
    start_turn(opted_in)
    (opted_in / "src" / "app.py").write_text("a = 2\n")
    resp, _ = stop(opted_in)
    assert resp["decision"] == "block"
    assert "devkit: could not write the review log" in capsys.readouterr().err


def test_retention_deletes_files_past_it():
    d = log.log_dir()
    d.mkdir(parents=True)
    now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)
    old, kept = (d / f"{(now - timedelta(days=n)).date()}.jsonl" for n in (31, 30))
    old.write_text("{}\n")
    kept.write_text("{}\n")
    (d / "notes.txt").write_text("")
    assert log.write([log.make("/r", "verdict", {}, "x", now)], SETTINGS, now=now)
    assert not old.exists() and kept.exists() and (d / "notes.txt").exists() and (d / "2026-11-07.jsonl").exists()


def test_no_record_contains_the_home_path(in_repo, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # the repo is now under home
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("fixed", file=str(in_repo / "src" / "app.py")),
                                       finding("discuss", file=str(tmp_path / "other" / "x.py"))])
    stop(in_repo)
    text = "".join(p.read_text() for p in log.log_dir().glob("*.jsonl"))
    assert text and str(tmp_path) not in text
    paths = [r["attributes"].get("code.file.path") for r in records() if events([r]) == ["finding"]]
    assert paths == ["src/app.py", "~/other/x.py"]


def test_default_records_leave_out_text(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("discuss", **TEXT)])
    a = records()[-1]["attributes"]
    assert {P + "focus", P + "model", "code.file.path", "code.line.number", P + "score", P + "outcome"} <= a.keys()
    assert not {P + "quote", P + "claim", P + "failure_scenario"} & a.keys()


def test_user_log_content_adds_text(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("discuss", **TEXT)])
    a = records()[-1]["attributes"]
    assert (a[P + "quote"], a[P + "claim"], a[P + "failure_scenario"]) == ("a = 2", "c", TEXT["failure_scenario"])


def test_secret_path_content_is_never_logged(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("discuss", file=f"config/{SECRET_NAME}", **TEXT)])
    a = records()[-1]["attributes"]
    assert a["code.file.path"] == f"config/{SECRET_NAME}" and P + "quote" not in a and P + "claim" not in a


def test_repo_cant_turn_on_content_logging(in_repo, capsys, monkeypatch):
    (in_repo / ".devkit.yaml").write_text("rules:\n  review: { level: block, log_content: true }\n")
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("discuss", **TEXT)])
    assert P + "quote" not in records()[-1]["attributes"]
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "ignored settings:" in out and "rules.review.log_content" in out


def test_log_false_writes_nothing(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log: false}\n")
    blocked_round_1(in_repo)
    edit(in_repo, "a = 3\n")
    record_round(capsys, monkeypatch, [finding("fixed")])
    stop(in_repo)
    assert run(capsys, monkeypatch, "skip", "--reason", "user asked")[0] == 0
    stop(in_repo)
    assert not log.log_dir().exists()


def open_id(out):
    return re.search(r"open finding ids: (f-[0-9a-f]{8})", out).group(1)


def test_rejecting_a_finding(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    fid = open_id(record_round(capsys, monkeypatch, [finding("discuss")]))
    code, out, _ = run(capsys, monkeypatch, "verdict", fid, "rejected", "--note", "intended")
    assert code == 0 and out == f"verdict recorded: {fid} rejected\n"
    v = records()[-1]
    a = v["attributes"]
    assert events([v]) == ["verdict"] and (a[P + "finding_id"], a[P + "verdict"]) == (fid, "rejected")
    assert (a["session.id"], a[P + "focus"], a[P + "model"], a[P + "round"]) == (SID, "correctness", "opus", 1)
    assert P + "note" not in a  # log_content is off


def test_verdict_note_kept_with_log_content(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    blocked_round_1(in_repo)
    fid = open_id(record_round(capsys, monkeypatch, [finding("discuss")]))
    run(capsys, monkeypatch, "verdict", fid, "accepted", "--note", "good catch")
    assert records()[-1]["attributes"][P + "note"] == "good catch"


def test_unknown_finding(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    before = records()
    code, _, err = run(capsys, monkeypatch, "verdict", "nope", "accepted")
    assert code != 0 and "no finding nope" in err and records() == before


def test_home_path_scrubbed_from_free_text(in_repo, capsys, monkeypatch, tmp_path, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    real = tmp_path / "real-home"
    real.mkdir()
    (tmp_path / "link-home").symlink_to(real)
    monkeypatch.setenv("HOME", str(tmp_path / "link-home"))
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("dropped", reason=f"{real}/x.py and {tmp_path}/link-home/y.py are gone",
                                               file=f"{real}/z.py")])
    a = records()[-1]["attributes"]
    assert a[P + "drop_reason"] == "~/x.py and ~/y.py are gone" and a["code.file.path"] == "~/z.py"
    assert log.scrub(f"{tmp_path}/link-home2/a") == f"{tmp_path}/link-home2/a"  # only the home dir itself
    home = f"{tmp_path}/link-home"
    assert log.scrub(f"see {home}. Then {home}.bak and {home}-x") == f"see ~. Then {home}.bak and {home}-x"


def test_literal_names_with_glob_chars_are_checked_as_secrets(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("discuss", file="certs/server[prod].key", **TEXT)])
    assert P + "quote" not in records()[-1]["attributes"]


def test_verdict_names_the_findings_repo(in_repo, capsys, monkeypatch, tmp_path):
    blocked_round_1(in_repo)
    fid = open_id(record_round(capsys, monkeypatch, [finding("discuss")]))
    other = tmp_path / "elsewhere"
    other.mkdir()
    import subprocess

    subprocess.run(["git", "init", "-q", str(other)], check=True)
    monkeypatch.chdir(other)
    assert run(capsys, monkeypatch, "verdict", fid, "accepted")[0] == 0
    f, v = [r["attributes"] for r in records() if r["attributes"].get(P + "finding_id") == fid]
    assert (v["devkit.repo.name"], v["devkit.repo.path_hash"]) == (f["devkit.repo.name"], f["devkit.repo.path_hash"])


def test_drop_reason_logged_with_log_content(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log_content: true}\n")
    blocked_round_1(in_repo)
    record_round(capsys, monkeypatch, [finding("dropped", reason="pre-existing", score=10)])
    assert records()[-1]["attributes"][P + "drop_reason"] == "pre-existing"


def test_no_open_ids_printed_when_nothing_was_logged(in_repo, capsys, monkeypatch, user_config):
    user_config("rules:\n  review: {level: block, log: false}\n")
    blocked_round_1(in_repo)
    assert record_round(capsys, monkeypatch, [finding("discuss")]) == "recorded: 0 fixed, 1 open, 0 dropped\n"


def test_verdict_carries_the_findings_outcome(in_repo, capsys, monkeypatch):
    blocked_round_1(in_repo)
    fid = open_id(record_round(capsys, monkeypatch, [finding("question", score=50)]))
    run(capsys, monkeypatch, "verdict", fid, "accepted")
    assert records()[-1]["attributes"][P + "outcome"] == "question"
