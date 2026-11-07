import re
from datetime import datetime, timezone

import pytest

from devkit.review import machine, state

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
DEFAULT_META = {"permission_mode": "default", "background_task_count": 0}


def changed_files(mapping=None):
    """changed(a, b) from {(a, b): [paths]}; any other pair changed `src/app.py`."""
    return lambda a, b: (mapping or {}).get((a, b), ["src/app.py"])


def stop(data, cur, *, level="block", max_rounds=3, skip=(), meta=DEFAULT_META, changed=None, sid="s1"):
    return machine.on_stop(data, sid, cur, level=level, max_rounds=max_rounds, skip_paths=skip, meta=meta,
                           changed=changed or changed_files(), now=NOW)


def turn(start, *, base=None, sid="s1"):
    """State after session start on `base` and a prompt on `start`."""
    data = machine.on_session_start(state.empty(), sid, base or start, "startup", NOW)
    return machine.on_prompt(data, sid, start, NOW)[0]


def record(data, sid="s1", **rec):
    data["sessions"][sid]["loop"]["record"] = rec
    return data


def test_question_only_turn():
    data = turn("T0")
    assert stop(data, "T0") == (data, machine.ALLOW)


def test_turn_that_edits_code_blocks_with_round_1():
    data, d = stop(turn("T0"), "T1")
    assert d.action == "block" and "round 1 of 3" in d.message and "devkit review plan --session s1" in d.message
    loop = dict(data["sessions"]["s1"]["loop"])
    assert re.fullmatch(r"l-[0-9a-f]{12}", loop.pop("id"))
    assert loop == {"round": 1, "max": 3, "from": "T0", "at": "T1", "record": None, "totals": {"fixed": 0, "open": 0}}


def test_already_reviewed():
    data = turn("T0")
    data["reviewed"] = ["T1"]
    data = machine.on_prompt(data, "s1", "T2", NOW)[0]  # a later turn changes a file, then restores it
    data, d = stop(data, "T1")
    assert d == machine.ALLOW and data["sessions"]["s1"]["loop"] is None


def test_pre_existing_changes_are_not_the_trigger():
    data = turn("DIRTY", base="DIRTY")
    assert stop(data, "DIRTY")[1] == machine.ALLOW


def test_round_1_covers_since_last_reviewed_else_baseline():
    data = turn("T1", base="T0")  # the user edited between session start and the prompt
    data, _ = stop(data, "T2")
    assert data["sessions"]["s1"]["loop"]["from"] == "T0"
    data["sessions"]["s1"]["loop"] = None
    data["sessions"]["s1"]["last_reviewed"] = "R"
    data, _ = stop(data, "T3")
    assert data["sessions"]["s1"]["loop"]["from"] == "R"


def test_docs_only_change_skipped_and_counts_as_reviewed():
    data, d = stop(turn("T0"), "T1", skip=["**/*.md"], changed=changed_files({("T0", "T1"): ["README.md"]}))
    assert d == machine.ALLOW and "T1" in data["reviewed"] and data["sessions"]["s1"]["last_reviewed"] == "T1"


def test_skip_paths_need_every_path_to_match():
    _, d = stop(turn("T0"), "T1", skip=["**/*.md"], changed=changed_files({("T0", "T1"): ["README.md", "app.py"]}))
    assert d.action == "block"


@pytest.mark.parametrize("pattern,path,hit", [
    ("**/*.md", "README.md", True), ("**/*.md", "docs/a/b.md", True), ("*.md", "docs/b.md", True),
    ("docs/*.md", "docs/b.md", True), ("docs/*.md", "docs/x/b.md", False), ("docs/**", "docs/x/b.md", True),
    ("*.md", "md", False), ("/docs/*", "docs/a", True), ("a?c", "abc", True), ("a?c", "a/c", False),
    ("vendor", "vendor/lib/x.py", True), ("generated/", "src/generated/api.py", True), ("docs", "docs.py", False),
    ("docs/api", "docs/api/x/y.md", True), ("docs/api", "src/docs/api/x.md", False), ("build/*", "build/a/b", True),
])
def test_skip_path_globs(pattern, path, hit):
    assert machine.skipped(path, [pattern]) is hit


def test_clean_second_round():
    data, _ = stop(turn("T0"), "T1")
    data, d = stop(record(data, fixed=2, open=0, dropped=1), "T2")
    assert d.action == "block" and "round 2 of 3" in d.message
    assert data["sessions"]["s1"]["loop"]["from"] == "T1" and data["sessions"]["s1"]["loop"]["at"] == "T2"
    data, d = stop(record(data, fixed=0, open=1, dropped=0), "T2")
    assert d == machine.Decision("notice", "review: 2 rounds, 2 fixed, 1 to discuss")
    s = data["sessions"]["s1"]
    assert s["loop"] is None and s["last_reviewed"] == "T2" and data["reviewed"] == ["T2"]


def test_round_limit():
    data, _ = stop(turn("T0"), "T1")
    blocks = 1
    for i in (2, 3, 4):
        data, d = stop(record(data, fixed=1, open=0, dropped=0), f"T{i}")
        blocks += d.action == "block"
    assert blocks == 3 and d == machine.Decision("notice", "review: 3 rounds, 3 fixed, 0 to discuss")
    assert data["sessions"]["s1"]["loop"] is None and "T4" in data["reviewed"]


@pytest.mark.parametrize("max_rounds", range(1, 6))
def test_a_turn_blocks_at_most_max_rounds_times(max_rounds):
    data, d = stop(turn("T0"), "T1", max_rounds=max_rounds)
    blocks, i = 0, 1
    while d.action == "block":
        blocks += 1
        i += 1
        data, d = stop(record(data, fixed=1, open=0, dropped=0), f"T{i}", max_rounds=max_rounds)
    assert blocks == max_rounds and d.action == "notice"
    assert stop(data, f"T{i}", max_rounds=max_rounds)[1] == machine.ALLOW  # stopping again: already reviewed


def test_loop_keeps_the_max_it_started_with():
    data, _ = stop(turn("T0"), "T1", max_rounds=2)
    data, d = stop(record(data, fixed=1, open=0, dropped=0), "T2", max_rounds=5)
    assert "round 2 of 2" in d.message
    assert stop(record(data, fixed=1, open=0, dropped=0), "T3", max_rounds=5)[1].action == "notice"


def test_agent_ignores_the_request():
    data, _ = stop(turn("T0"), "T1")
    data, d = stop(data, "T1")
    assert d == machine.Decision("notice", "review round 1 of 3 was not completed")
    assert data["sessions"]["s1"]["loop"] is None and "T1" in data["reviewed"]


def test_skip_is_visible():
    data, _ = stop(turn("T0"), "T1")
    data, d = stop(record(data, skipped="user asked"), "T1")
    assert d == machine.Decision("notice", "review skipped: user asked") and "T1" in data["reviewed"]


@pytest.mark.parametrize("meta", [{"permission_mode": "plan"}, {"background_task_count": 2}])
def test_never_blocked(meta):
    data = turn("T0")
    assert stop(data, "T1", meta=meta) == (data, machine.ALLOW)
    data, _ = stop(data, "T1")
    assert stop(data, "T1", meta=meta) == (data, machine.ALLOW)  # nor during a loop, which stays as it was


def test_warn_notifies_once_per_snapshot_and_never_blocks():
    data, d = stop(turn("T0"), "T1", level="warn")
    assert d.action == "notice" and "unreviewed changes" in d.message and "devkit-review" in d.message
    assert data["sessions"]["s1"]["loop"] is None
    data, d = stop(data, "T1", level="warn")
    assert d == machine.ALLOW
    assert stop(data, "T2", level="warn")[1].action == "notice"


def test_missing_session_starts_at_the_current_tree():
    data, d = stop(state.empty(), "T1")
    assert d == machine.ALLOW and data["sessions"]["s1"]["baseline"] == "T1"


def test_sessions_are_independent():
    data = turn("T0", sid="a")
    data = machine.on_prompt(data, "b", "T0", NOW)[0]
    data, _ = stop(data, "T1", sid="a")
    assert data["sessions"]["b"]["loop"] is None and stop(data, "T0", sid="b")[1] == machine.ALLOW


def test_compaction_mid_turn_keeps_the_scope():
    data = turn("T0")
    data = machine.on_session_start(data, "s1", "T1", "compact", NOW)  # the agent had edited to T1
    data, d = stop(data, "T1")
    assert d.action == "block" and data["sessions"]["s1"]["loop"]["from"] == "T0"


def test_no_loop_while_another_session_has_one():
    data = turn("T0", sid="a")
    data, _ = stop(data, "T1", sid="a")
    data = machine.on_prompt(data, "b", "T1", NOW)[0]
    data, d = stop(data, "T2", sid="b")
    assert d == machine.ALLOW and data["sessions"]["b"]["loop"] is None and "T2" not in data["reviewed"]
    data, d = stop(record(data, "a", fixed=0, open=0, dropped=0), "T2", sid="a")
    assert d.action == "notice"
    data = machine.on_prompt(data, "b", "T2", NOW)[0]
    assert stop(data, "T3", sid="b")[1].action == "block"  # once a's loop ends, b's changes are reviewed


def test_new_prompt_drops_an_interrupted_loop_unreviewed():
    data, _ = stop(turn("T0"), "T1")
    data, d = machine.on_prompt(data, "s1", "T1", NOW)
    assert d.action == "notice" and "interrupted" in d.message
    assert data["sessions"]["s1"]["loop"] is None and data["reviewed"] == []
    data, d = stop(data, "T2")
    assert d.action == "block" and data["sessions"]["s1"]["loop"]["from"] == "T0"


def test_input_state_not_mutated():
    data = turn("T0")
    before = repr(data)
    stop(data, "T1")
    machine.on_prompt(data, "s1", "T5", NOW)
    machine.on_session_start(data, "s1", "T5", "startup", NOW)
    assert repr(data) == before


def test_trees_are_the_ones_the_next_stop_diffs_from():
    data = turn("T1", base="T0")
    assert machine.trees(data, "s1") == ["T0"]
    data["sessions"]["s1"]["last_reviewed"] = "R"
    assert machine.trees(data, "s1") == ["R"]  # the old baseline is no longer used: its loss doesn't matter
    data, _ = stop(data, "T2")
    assert machine.trees(data, "s1") == ["R", "T2"]
    assert machine.trees(data, "other") == []
    assert machine.forget(data, "s1", "T9", NOW)["sessions"]["s1"]["baseline"] == "T9"


def names(d):
    return [(name, e["loop"]["round"], e.get("reason")) for name, e in d.events]


def test_loop_events_name_one_loop_through_its_rounds():
    data, d = stop(turn("T0"), "T1")
    assert names(d) == [("loop_started", 1, None), ("round_requested", 1, None)]
    lid = data["sessions"]["s1"]["loop"]["id"]
    assert d.events[0][1]["trigger"] == "stop" and d.events[0][1]["max_rounds"] == 3
    data, d = stop(record(data, fixed=1, open=0, dropped=0), "T2")
    assert names(d) == [("round_requested", 2, None)] and data["sessions"]["s1"]["loop"]["id"] == lid
    data, d = stop(record(data, fixed=0, open=1, dropped=0), "T2")
    assert names(d) == [("loop_ended", 2, "clean")]
    assert {e["loop"]["id"] for _, e in d.events} == {lid} and d.events[0][1]["session"] == "s1"
    assert (d.events[0][1]["fixed"], d.events[0][1]["open"]) == (1, 1)


@pytest.mark.parametrize("rec, reason", [(None, "not_completed"), ({"skipped": "user asked"}, "skipped"),
                                         ({"fixed": 1, "open": 0, "dropped": 0}, "max_rounds")])
def test_loop_ended_reasons(rec, reason):
    data, _ = stop(turn("T0"), "T1", max_rounds=1)
    if rec is not None:
        record(data, **rec)
    assert names(stop(data, "T2", max_rounds=1)[1]) == [("loop_ended", 1, reason)]


def test_interrupted_loop_ends_not_completed():
    data, _ = stop(turn("T0"), "T1")
    assert names(machine.on_prompt(data, "s1", "T1", NOW)[1]) == [("loop_ended", 1, "not_completed")]


def test_allowed_stops_have_no_events():
    assert stop(turn("T0"), "T0")[1].events == ()


def test_loop_ended_early_counts_its_recorded_round():
    data, _ = stop(turn("T0"), "T1")
    data, _ = stop(record(data, fixed=2, open=0, dropped=0), "T2")
    record(data, fixed=1, open=1, dropped=0)  # round 2 recorded, then the user interrupts before the stop
    e = machine.on_prompt(data, "s1", "T2", NOW)[1].events[0][1]
    assert (e["reason"], e["fixed"], e["open"]) == ("not_completed", 3, 1)
