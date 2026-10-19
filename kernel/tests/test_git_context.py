from __future__ import annotations

import subprocess

from kernel.executor import SafeExecutor


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_git_context_lists_changes_and_recent_commits(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "first commit")
    (tmp_path / "a.py").write_text("y\n")
    (tmp_path / "b.py").write_text("z\n")

    header, details = SafeExecutor(tmp_path).git_context()

    assert header == "main (2 uncommitted)"
    assert "M a.py" in details
    assert "?? b.py" in details
    assert "first commit" in details


def test_git_context_of_a_clean_repo_has_only_commits(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "only commit")

    header, details = SafeExecutor(tmp_path).git_context()

    assert header == "main (clean)"
    assert "Changed files" not in details
    assert "only commit" in details


def test_git_context_without_commits_does_not_fail(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x\n")

    header, details = SafeExecutor(tmp_path).git_context()

    assert "main" in header
    assert "Recent commits" not in details


def test_git_context_caps_the_changed_files(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    for number in range(14):
        (tmp_path / f"f{number}.txt").write_text("x\n")

    _, details = SafeExecutor(tmp_path).git_context()

    assert "… and 4 more" in details
