from repo_warden.cli import main


def test_cplt_backend_refuses_and_points_to_direct(repo_dir, capsys):
    assert main(["--repo", str(repo_dir)]) == 1
    assert "--backend direct" in capsys.readouterr().err


def test_direct_warns_then_refuses_missing_policy(repo_dir, tmp_path, capsys):
    rc = main(["--repo", str(repo_dir), "--backend", "direct", "--policy", str(tmp_path / "none.yaml"),
               "--audit", str(tmp_path / "a.jsonl")])
    err = capsys.readouterr().err
    assert rc == 1 and "NO OS CONTAINMENT IS VERIFIED" in err and "policy file not found" in err


def test_subdirectory_refused(repo_dir, capsys):
    (repo_dir / "src").mkdir()
    assert main(["--repo", str(repo_dir / "src"), "--backend", "direct"]) == 1
    assert f"--repo {repo_dir}" in capsys.readouterr().err


def test_audit_inside_repo_refused(repo_dir, tmp_path, capsys):
    pol = tmp_path / "p.yaml"
    pol.write_text("tools: {}\n")
    rc = main(["--repo", str(repo_dir), "--backend", "direct", "--policy", str(pol),
               "--audit", str(repo_dir / "audit.jsonl")])
    assert rc == 1 and "inside the repository" in capsys.readouterr().err
