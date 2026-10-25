import pytest

from devkit.engine import evaluate
from devkit.events import Event, Level
from devkit.rules.comment_brevity import RULE

DEFAULTS = {"max_lines": 2}


def check(path, before, after, **settings):
    return RULE.check(Event("edit", path=path, before=before, after=after), {**DEFAULTS, **settings})


def test_new_long_comment_named_by_first_line():
    before = "x = 1\ny = 2\n"
    after = "x = 1\n# one\n# two\n# three\n# four\ny = 2\n"
    (f,) = check("/r/app.py", before, after)
    assert f.rule == "comment_brevity" and f.line == 2 and "line 2" in f.message and "'# one'" in f.message
    assert "commit message" in f.message


def test_two_lines_is_fine():
    assert check("/r/app.py", "x = 1\n", "# one\n# two\nx = 1\n") == []


def test_existing_long_docstring_untouched():
    before = 'def f():\n    """One.\n\n    Two.\n    Three.\n    """\n    return 1\n'
    after = before.replace("return 1", "return 2")
    assert check("/r/app.py", before, after) == []


def test_new_docstring_flagged():
    after = 'def f():\n    """One.\n\n    Two.\n    """\n    return 1\n'
    (f,) = check("/r/app.py", "", after)
    assert f.line == 2


def test_string_literal_is_not_a_comment():
    before = "x = 1\n"
    after = 'x = 1\nSQL = """\n# not a comment\n# still not\n# nope\n"""\n'
    assert check("/r/app.py", before, after) == []


def test_unknown_file_type():
    assert check("/r/README.md", "", "# a\n# b\n# c\n# d\n") == []


def test_unparsable_python_falls_back_to_patterns():
    after = "def f(:\n# one\n# two\n# three\n"
    (f,) = check("/r/app.py", "", after)
    assert f.line == 2


def test_alembic_migration_header_skipped():
    header = '"""add users\n\nRevision ID: abc\nRevises: def\n"""\nrevision = "abc"\n'
    assert check("/r/migrations/versions/abc_add_users.py", "", header) == []
    assert check("/r/app/models.py", "", header) != []


@pytest.mark.parametrize("path,text", [
    ("/r/a.ts", "// one\n// two\n// three\nconst x = 1;\n"),
    ("/r/a.go", "/* one\n two\n three */\nvar x = 1\n"),
    ("/r/a.css", "/* one\n two\n three */\nbody {}\n"),
    ("/r/a.sh", "# one\n# two\n# three\necho hi\n"),
    ("/r/a.yaml", "# one\n# two\n# three\nkey: v\n"),
])
def test_other_languages(path, text):
    assert len(check(path, "", text)) == 1


def test_banner_lines_are_not_comment_runs():
    assert check("/r/a.py", "", "#####\n#####\n#####\nx = 1\n") == []


def test_max_lines_configurable():
    after = "# one\n# two\n# three\nx = 1\n"
    assert check("/r/app.py", "", after, max_lines=3) == []
    assert len(check("/r/app.py", "", after, max_lines=1)) == 1


def test_only_content_events():
    assert RULE.kinds == {"write", "edit"} and RULE.content and RULE.check(Event("edit", path="/r/a.py"), DEFAULTS) == []


def test_deeply_nested_python_does_not_crash():
    after = "x = " + "(" * 500 + ")" * 500 + "\n# a\n# b\n# c\n"
    assert len(check("/r/app.py", "", after)) == 1


# --- through the engine: levels and repo scope ---

LONG = "# one\n# two\n# three\n# four\nx = 1\n"


def test_default_block_in_opted_in_repo(repo):
    (repo / ".devkit.yaml").write_text("")
    out = evaluate([Event("write", path=str(repo / "app.py"), before="", after=LONG, cwd=str(repo))])
    assert out.blocked and out.blocks[0].rule == "comment_brevity"


def test_warn_does_not_block(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: warn\n")
    out = evaluate([Event("write", path=str(repo / "app.py"), before="", after=LONG, cwd=str(repo))])
    assert not out.blocked and out.warnings[0].rule == "comment_brevity"


def test_repo_not_opted_in(repo):
    assert evaluate([Event("write", path=str(repo / "app.py"), before="", after=LONG, cwd=str(repo))]).findings == []


def test_max_lines_from_repo_config(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: { level: block, max_lines: 5 }\n")
    assert evaluate([Event("write", path=str(repo / "app.py"), before="", after=LONG, cwd=str(repo))]).findings == []


def test_level_off(repo):
    (repo / ".devkit.yaml").write_text("rules:\n  comment_brevity: off\n")
    out = evaluate([Event("write", path=str(repo / "app.py"), before="", after=LONG, cwd=str(repo))])
    assert out.findings == [] and Level.OFF is Level.parse(False)


DOC = 'def f():\n    """Line one\n    two\n    three\n    four"""\n    return 1\n'


def test_existing_docstring_not_new_when_after_does_not_parse():
    """before goes through ast, after through the pattern fallback: the same docstring must still match."""
    assert check("/r/a.py", DOC, DOC + "def g(:\n") == []


def test_reindented_docstring_is_not_introduced():
    moved = 'class A:\n    def f(self):\n        """Line one\n        two\n        three\n        four"""\n        return 1\n'
    assert check("/r/a.py", DOC, moved) == []


def test_reindented_block_comment_is_not_introduced():
    before = "/* one\n   two\n   three */\nint x;\n"
    after = "void f() {\n    /* one\n       two\n       three */\n    int x;\n}\n"
    assert check("/r/a.c", before, after) == []


@pytest.mark.parametrize("path", ["/r/run.py", "/r/run.sh"])
def test_shebang_is_not_part_of_a_comment_run(path):
    assert check(path, "", "#!/usr/bin/env python3\n# Copyright x\n# SPDX-License-Identifier: MIT\n") == []
    assert check(path, "", "#!/bin/sh\n# one\n# two\n# three\n")  # still three real comment lines
