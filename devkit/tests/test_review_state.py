import hashlib
import json
import threading
import multiprocessing
from datetime import datetime, timedelta, timezone

import pytest

from devkit.review import machine, state

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)


def t(name) -> str:
    """A well-formed tree id standing for `name`."""
    return hashlib.sha1(str(name).encode()).hexdigest()


def session(**kw):
    return {"baseline": t("b"), "turn_start": t("b"), "last_reviewed": None, "loop": None, "warned": None,
            "updated": NOW.isoformat(), **kw}


def test_path_is_per_repo_and_outside_it(tmp_path, isolated):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (tmp_path / "link").symlink_to(a)
    assert state.path_for(a) != state.path_for(b)
    assert state.path_for(tmp_path / "link") == state.path_for(a)
    assert state.path_for(a).parent == isolated / ".cache" / "devkit" / "review"


def test_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert state.path_for(tmp_path).parent == tmp_path / "xdg" / "devkit" / "review"


def test_round_trip_and_unchanged_not_written(tmp_path):
    with state.locked(tmp_path, now=NOW) as data:
        assert data == state.empty()
    assert not state.path_for(tmp_path).exists()
    with state.locked(tmp_path, now=NOW) as data:
        data["reviewed"].append(t(1))
    with state.locked(tmp_path, now=NOW) as data:
        assert data["reviewed"] == [t(1)]


def test_atomic_replace_keeps_old_file_on_failure(tmp_path, monkeypatch):
    with state.locked(tmp_path, now=NOW) as data:
        data["reviewed"].append(t(1))
    path = state.path_for(tmp_path)
    monkeypatch.setattr(state.json, "dump", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        with state.locked(tmp_path, now=NOW) as data:
            data["reviewed"].append(t(2))
    assert json.loads(path.read_text())["reviewed"] == [t(1)]
    assert sorted(p.name for p in path.parent.iterdir()) == sorted([path.name, path.with_suffix(".lock").name])


def test_reviewed_capped_to_the_latest(tmp_path):
    with state.locked(tmp_path, now=NOW) as data:
        data["reviewed"] = [t(i) for i in range(150)] + [t(0)]
    with state.locked(tmp_path, now=NOW) as data:
        assert len(data["reviewed"]) == state.MAX_REVIEWED
        assert data["reviewed"][-1] == t(0) and data["reviewed"][0] == t(51) and t(50) not in data["reviewed"]


def test_old_sessions_pruned(tmp_path):
    with state.locked(tmp_path, now=NOW) as data:
        data["sessions"] = {"old": session(updated=(NOW - timedelta(days=8)).isoformat()),
                            "recent": session(updated=(NOW - timedelta(days=6)).isoformat()),
                            "broken": session(updated="yesterday"), "naive": session(updated="2026-11-01T11:00")}
    with state.locked(tmp_path, now=NOW) as data:
        assert list(data["sessions"]) == ["recent"]


LOOP = {"round": 1, "max": 3, "from": t("b"), "at": t("c"), "record": None, "totals": {"fixed": 0, "open": 0}}
BAD_SESSIONS = [
    1, {"updated": NOW.isoformat()}, session(baseline=123), session(turn_start="T0"), session(last_reviewed="-x"),
    session(updated=None), session(loop={"round": 1}), session(loop={**LOOP, "record": "x"}),
    session(loop={**LOOP, "record": {"fixed": "2", "open": 0, "dropped": 0}}),
    session(loop={**LOOP, "record": {"skipped": 1}}), session(loop={**LOOP, "round": True}),
    session(loop={**LOOP, "totals": {"fixed": -1, "open": 0}}), session(loop={**LOOP, "at": "--output=x"}),
]


@pytest.mark.parametrize("text", ["{not json", "[]", '{"reviewed": "x", "sessions": {}}', "\xff",
                                  json.dumps({"reviewed": ["HEAD"], "sessions": {}}),
                                  *(json.dumps({"reviewed": [], "sessions": {"s": b}}) for b in BAD_SESSIONS)])
def test_invalid_file_is_empty_with_a_warning(tmp_path, text):
    path = state.path_for(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(text.encode("latin-1"))
    warnings = []
    with state.locked(tmp_path, now=NOW, warn=warnings.append) as data:
        assert data == state.empty()
        data["reviewed"].append(t(1))
    assert len(warnings) == 1 and warnings[0].startswith("devkit: review state")
    assert json.loads(path.read_text())["reviewed"] == [t(1)]


@pytest.mark.parametrize("good", [session(), session(last_reviewed=t("r"), warned=t("w"), loop=LOOP),
                                  session(loop={**LOOP, "record": {"fixed": 2, "open": 1, "dropped": 0}}),
                                  session(loop={**LOOP, "record": {"skipped": "user asked"}})])
def test_valid_sessions_are_kept(tmp_path, good):
    path = state.path_for(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"reviewed": [t(1)], "sessions": {"s": good}}))
    with state.locked(tmp_path, now=NOW, warn=pytest.fail) as data:
        assert data["sessions"]["s"] == good


def test_machine_output_is_valid(tmp_path):
    data = machine.on_session_start(state.empty(), "s", t(0), "startup", NOW)
    data, _ = machine.on_stop(data, "s", t(1), level="block", max_rounds=3, skip_paths=(), meta={},
                              changed=lambda a, b: ["x.py"], now=NOW)
    assert state._valid(data) and data["sessions"]["s"]["loop"]


def test_lock_timeout(tmp_path):
    held, release = threading.Event(), threading.Event()

    def holder():
        with state.locked(tmp_path):
            held.set()
            release.wait(10)

    th = threading.Thread(target=holder)
    th.start()
    held.wait(10)
    try:
        with pytest.raises(state.LockTimeout):
            with state.locked(tmp_path, timeout_s=0.2):
                pass
    finally:
        release.set()
        th.join()
    with state.locked(tmp_path, timeout_s=0.2):
        pass


def test_resume_keeps_the_session(tmp_path):
    data = machine.on_session_start(state.empty(), "s1", "base", "startup", NOW)
    data["sessions"]["s1"]["last_reviewed"] = "rev"
    resumed = machine.on_session_start(data, "s1", "now", "resume", NOW)
    assert resumed["sessions"]["s1"]["baseline"] == "base" and resumed["sessions"]["s1"]["last_reviewed"] == "rev"
    for source in ("compact", "startup", "clear", None):  # a known session id keeps its entry whatever the source
        assert machine.on_session_start(data, "s1", "now", source, NOW)["sessions"]["s1"]["baseline"] == "base"
    assert machine.on_session_start(state.empty(), "s2", "now", "resume", NOW)["sessions"]["s2"]["baseline"] == "now"


def test_reset(tmp_path):
    assert state.reset(tmp_path) is False
    with state.locked(tmp_path, now=NOW) as data:
        data["reviewed"].append(t(1))
    assert state.reset(tmp_path) is True and not state.path_for(tmp_path).exists()


def _append_many(top, tag, n):
    for i in range(n):
        with state.locked(top) as data:
            data["reviewed"].append(t(f"{tag}{i}"))


def test_racing_writers_lose_nothing(tmp_path):
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_append_many, args=(tmp_path, tag, 40)) for tag in "ab"]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
        assert p.exitcode == 0
    with state.locked(tmp_path) as data:
        assert sorted(data["reviewed"]) == sorted(t(f"{tag}{i}") for tag in "ab" for i in range(40))
