import pytest

from devkit.patch import Hunk, PatchError, apply, fallback, parse


def envelope(*lines: str) -> str:
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"]) + "\n"


def test_add_file():
    [op] = parse(envelope("*** Add File: src/new.py", "+import os", "+", "+print(os.sep)"))
    assert (op.kind, op.path, op.content) == ("add", "src/new.py", "import os\n\nprint(os.sep)\n")


def test_update_with_matching_context():
    [op] = parse(envelope("*** Update File: app.py", "@@ def main():", "     a = 1", "-    b = 2", "+    b = 3",
                          "     return a + b"))
    assert op.kind == "update" and op.hunks[0].context == "def main():"
    before = "import x\n\ndef main():\n    a = 1\n    b = 2\n    return a + b\n"
    assert apply(before, op.hunks) == "import x\n\ndef main():\n    a = 1\n    b = 3\n    return a + b\n"


def test_update_with_stale_context_falls_back():
    [op] = parse(envelope("*** Update File: app.py", "@@", " a = 1", "-b = 2", "+b = 3"))
    assert apply("something else entirely\n", op.hunks) is None
    assert fallback(op.hunks) == ("a = 1\nb = 2\n", "a = 1\nb = 3\n")


def test_move():
    [op] = parse(envelope("*** Update File: old.py", "*** Move to: pkg/new.py", "@@", "-x = 1", "+x = 2"))
    assert (op.path, op.move_to) == ("old.py", "pkg/new.py")


def test_delete():
    assert [(op.kind, op.path) for op in parse(envelope("*** Delete File: gone.txt"))] == [("delete", "gone.txt")]


def test_several_files_in_order():
    ops = parse(envelope("*** Add File: a", "+1", "*** Delete File: b", "*** Update File: c", "@@", "-x", "+y"))
    assert [(op.kind, op.path) for op in ops] == [("add", "a"), ("delete", "b"), ("update", "c")]


@pytest.mark.parametrize("text", [
    "*** Add File: a\n+1\n",  # no envelope
    "*** End Patch\n*** Begin Patch\n",
    envelope(),  # empty
    envelope("*** Frobnicate File: a"),
    envelope("*** Update File: a"),  # no hunks
    envelope("*** Update File: a", "@@", "?what"),
    envelope("*** Add File:   ", "+1"),
    envelope("*** Update File: a", "*** End of File"),
])
def test_malformed_envelope_rejected(text):
    with pytest.raises(PatchError):
        parse(text)


def test_envelope_inside_a_heredoc():
    text = "apply_patch <<'EOF'\n" + envelope("*** Add File: a.txt", "+hi") + "EOF\n"
    assert parse(text)[0].path == "a.txt"


def test_first_hunk_may_omit_its_marker():
    [op] = parse(envelope("*** Update File: a", "-x", "+y"))
    assert apply("w\nx\nz\n", op.hunks) == "w\ny\nz\n"


def test_context_anchor_picks_the_right_occurrence():
    hunks = [Hunk("def b():", ["    pass"], ["    return 2"])]
    before = "def a():\n    pass\ndef b():\n    pass\n"
    assert apply(before, hunks) == "def a():\n    pass\ndef b():\n    return 2\n"


def test_end_of_file_anchors_at_the_end():
    [op] = parse(envelope("*** Update File: a", "@@", "-x", "+y", "*** End of File"))
    assert apply("x\nmid\nx\n", op.hunks) == "x\nmid\ny\n"


def test_pure_addition_appends():
    hunks = [Hunk(None, [], ["one"]), Hunk(None, [], ["two"])]
    assert apply("start\n", hunks) == "start\none\ntwo\n"


def test_whitespace_tolerant_match():
    assert apply("    x = 1   \n", [Hunk(None, ["x = 1"], ["x = 2"])]) == "x = 2\n"


def test_trailing_blank_context_line_tolerated():
    assert apply("a\nb\n", [Hunk(None, ["b", ""], ["c", ""])]) == "a\nc\n"


def test_hunks_apply_in_order():
    hunks = [Hunk(None, ["1"], ["one"]), Hunk(None, ["3"], ["three"])]
    assert apply("1\n2\n3\n", hunks) == "one\n2\nthree\n"
    assert apply("1\n2\n3\n", list(reversed(hunks))) is None  # `3` then `1` can't be found going forward


def test_indented_header_lines_are_context():
    """Codex reads ` *** Delete File: x` inside a hunk as context, not as a new file operation."""
    [op] = parse(envelope("*** Update File: README.md", "@@", " intro", " *** Delete File: .env", "-old", "+new"))
    assert op.path == "README.md" and op.hunks[0].old == ["intro", "*** Delete File: .env", "old"]


def test_end_of_file_hunk_never_matches_earlier():
    assert apply("x\nmid\nx  \n", [Hunk(None, ["x"], ["y"], at_end=True)]) == "x\nmid\ny\n"
    assert apply("x\nmid\nz\n", [Hunk(None, ["x"], ["y"], at_end=True)]) is None


def test_blank_only_old_side_inserts_after_the_anchor():
    assert apply("a\nb\n", [Hunk("a", [""], ["", "z"])]) == "a\n\nz\nb\n"  # not None: no fallback
