import errno
import os
from pathlib import Path

import pytest

from repo_warden import policy
from repo_warden.policy import PolicyError, account_policy_path, check_policy_protected, require_direct_authorised


def sandbox_denies(path, flags, mode=0o777):
    """What Landlock does to a read-only hierarchy: every write or create is EACCES."""
    if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
        raise PermissionError(errno.EACCES, "Permission denied", path)
    return os.open(path, flags, mode)


@pytest.fixture
def policy_file(tmp_path: Path) -> Path:
    d = tmp_path / "cfg"
    d.mkdir(mode=0o755)
    f = d / "policy.yaml"
    f.write_text("tools: {}\n")
    return f


# --- policy protection --------------------------------------------------------------------------

def test_writable_policy_refused(policy_file):
    with pytest.raises(PolicyError, match="policy-protection check.*writable"):
        check_policy_protected(policy_file)
    assert sorted(p.name for p in policy_file.parent.iterdir()) == ["policy.yaml"]


def test_creatable_policy_dir_refused(policy_file):
    policy_file.chmod(0o444)  # file locked, directory still open
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    with pytest.raises(PolicyError, match="files can be created"):
        check_policy_protected(policy_file)
    assert sorted(p.name for p in policy_file.parent.iterdir()) == ["policy.yaml"]


def test_policy_locked_by_mode_bits_refused(policy_file):
    """0444 in 0555, as an agent could set up for itself: denied by modes, not by the sandbox."""
    if os.geteuid() == 0:
        pytest.skip("root ignores file modes")
    policy_file.chmod(0o444)
    policy_file.parent.chmod(0o555)
    try:
        with pytest.raises(PolicyError, match="not writable by its mode bits"):
            check_policy_protected(policy_file)
    finally:
        policy_file.parent.chmod(0o755)


def test_sandbox_denial_on_user_writable_dir_accepted(policy_file):
    check_policy_protected(policy_file, open_fn=sandbox_denies)


def test_mode_bits_checked_for_the_right_class(policy_file):
    d = policy_file.parent
    d.chmod(0o577)  # owner may not write, group and others may
    try:
        with pytest.raises(PolicyError, match="mode bits"):
            check_policy_protected(policy_file, open_fn=sandbox_denies, euid=os.stat(d).st_uid, groups=set())
        check_policy_protected(policy_file, open_fn=sandbox_denies, euid=os.stat(d).st_uid + 1, groups=set())
    finally:
        d.chmod(0o755)


def test_unexpected_error_refused(policy_file):
    def eio(path, flags, mode=0o777):
        raise OSError(errno.EIO, "I/O error", path)

    with pytest.raises(PolicyError, match="unexpectedly"):
        check_policy_protected(policy_file, open_fn=eio)


# --- direct-backend authorisation ---------------------------------------------------------------

def test_direct_refused_when_account_policy_missing(tmp_path):
    with pytest.raises(PolicyError, match="not authorised.*does not exist"):
        require_direct_authorised(tmp_path / "missing.yaml")


@pytest.mark.parametrize("body", ["tools: {}\n", "repo_warden:\n  allow_direct_backend: false\n",
                                  "repo_warden:\n  allow_direct_backend: 'yes please'\n", "- a list\n"])
def test_direct_refused_without_grant(tmp_path, body):
    f = tmp_path / "policy.yaml"
    f.write_text(body)
    with pytest.raises(PolicyError, match="not authorised"):
        require_direct_authorised(f)


def test_direct_allowed_by_account_policy(tmp_path):
    f = tmp_path / "policy.yaml"
    f.write_text("repo_warden:\n  allow_direct_backend: true\ntools: {}\n")
    require_direct_authorised(f)


def test_account_path_ignores_home_and_xdg(monkeypatch, tmp_path):
    expected = account_policy_path()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert account_policy_path() == expected
    assert str(tmp_path) not in str(expected)


def test_direct_ignores_policy_flag_grant(tmp_path, monkeypatch):
    """Only the account file counts: a --policy file granting it is never consulted."""
    acct = tmp_path / "acct.yaml"
    acct.write_text("tools: {}\n")
    monkeypatch.setattr(policy, "account_policy_path", lambda: acct)
    (tmp_path / "flag.yaml").write_text("repo_warden:\n  allow_direct_backend: true\n")
    with pytest.raises(PolicyError, match="not authorised"):
        require_direct_authorised()
