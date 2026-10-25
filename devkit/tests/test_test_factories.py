from devkit.engine import evaluate
from devkit.events import Event, Level
from devkit.rules.test_factories import RULE

FACTORY = "import factory\n\nclass UserFactory(factory.Factory):\n    class Meta:\n        model = User\n"


def check(path, text, **settings):
    return RULE.check(Event("edit", "post", path=str(path), after=text), {"discouraged_calls": [], **settings})


def project(tmp_path):
    (tmp_path / "tests" / "factories").mkdir(parents=True)
    (tmp_path / "tests" / "factories" / "users.py").write_text(FACTORY)
    return tmp_path / "tests" / "api" / "test_users.py"


def test_raw_construction_with_factory(tmp_path):
    (f,) = check(project(tmp_path), "def test_x():\n    u = User(name='a')\n")
    assert "UserFactory.build(...)" in f.message and f.line == 2


def test_factory_use_and_class_definition_not_flagged(tmp_path):
    assert check(project(tmp_path), "class User(Base):\n    pass\n\nu = UserFactory.build()\nx = MyUser()\n") == []


def test_no_factories_directory(tmp_path):
    assert check(tmp_path / "tests" / "test_users.py", "u = User(name='a')\n") == []


def test_discouraged_call(tmp_path):
    (f,) = check(tmp_path / "tests" / "test_users.py", "u = make_valid_user()\n", discouraged_calls=["make_valid_user"])
    assert "make_valid_user(...)" in f.message and "discouraged_calls" in f.message
    assert check(tmp_path / "tests" / "test_users.py", "def make_valid_user():\n    pass\n",
                 discouraged_calls=["make_valid_user"]) == []


def test_only_test_python_files(tmp_path):
    project(tmp_path)
    assert check(tmp_path / "app" / "users.py", "u = User()\n") == []
    assert check(tmp_path / "tests" / "factories" / "more.py", "u = User()\n") == []
    assert check(tmp_path / "tests" / "users.txt", "u = User()\n") == []


def test_factory_fifo_does_not_hang(tmp_path):
    import os

    target = project(tmp_path)
    os.mkfifo(tmp_path / "tests" / "factories" / "pipe.py")
    assert len(check(target, "User()\n")) == 1


def test_post_phase_only_and_never_blocks(repo, user_config):
    assert RULE.phases == {"post", "check"} and RULE.max_level is Level.WARN
    user_config("rules:\n  test_factories: { level: block, discouraged_calls: [make_valid_user] }\n")
    (repo / ".devkit.yaml").write_text("")
    path = str(repo / "tests" / "test_a.py")
    out = evaluate([Event("edit", "post", path=path, after="make_valid_user()\n", cwd=str(repo))])
    assert not out.blocked and out.warnings[0].rule == "test_factories"
    assert evaluate([Event("edit", "pre", path=path, after="make_valid_user()\n", cwd=str(repo))]).findings == []


def test_off_by_default(repo):
    (repo / ".devkit.yaml").write_text("")
    (repo / "tests" / "factories").mkdir(parents=True)
    (repo / "tests" / "factories" / "u.py").write_text(FACTORY)
    path = str(repo / "tests" / "test_a.py")
    assert evaluate([Event("edit", "post", path=path, after="User()\n", cwd=str(repo))]).findings == []


def test_dirs_above_the_repo_do_not_count(tmp_path):
    """A repo cloned under ~/factories/ or ~/tests/ is judged by its own layout only."""
    import subprocess

    under_factories = tmp_path / "factories" / "app"
    subprocess.run(["git", "init", "-q", str(under_factories)], check=True)
    assert check(project(under_factories), "u = User(name='a')\n")  # still fires

    outer = tmp_path / "tests"  # ~/tests/factories/ outside the repo
    (outer / "factories").mkdir(parents=True)
    (outer / "factories" / "users.py").write_text(FACTORY)
    repo = outer / "proj"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    assert check(repo / "app" / "users.py", "u = User()\n") == []  # not a test file
    assert check(repo / "tests" / "test_users.py", "u = User()\n") == []  # no factories inside the repo
