from __future__ import annotations

import importlib
import subprocess
import zipfile


def test_package_script_import_has_main():
    module = importlib.import_module("package_script")
    assert callable(module.main)


def test_create_source_archive_contains_only_committed_tree(tmp_path):
    module = importlib.import_module("package_script")
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Archive Test"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "archive-test@local.invalid"],
        cwd=repository,
        check=True,
    )
    # write_bytes, not write_text: Path.write_text would turn "\n" into "\r\n"
    # on Windows and the committed blob (autocrlf is off here) would carry it.
    (repository / "tracked.txt").write_bytes(b"committed\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    (repository / "untracked-secret.txt").write_text(
        "must not be archived\n",
        encoding="utf-8",
    )
    archive = tmp_path / "source.zip"

    result = module.create_source_archive(repository, archive)

    assert result == archive
    with zipfile.ZipFile(archive) as source:
        assert source.namelist() == ["tracked.txt"]
        assert source.read("tracked.txt") == b"committed\n"
