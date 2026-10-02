# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kernel.terminal_env import _controlled_child_path
from kernel.terminal_trust import _is_trusted_system_directory, _is_trusted_system_executable
from kernel.terminal_windows_acl import dacl_lets_users_write, windows_path_is_user_mutable

SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
USERS = "S-1-5-32-545"
EVERYONE = "S-1-1-0"
SOME_USER = "S-1-5-21-1-2-3-1001"

FULL_CONTROL = 0x001F01FF
MODIFY = 0x001301BF
READ_AND_EXECUTE = 0x001200A9
GENERIC_WRITE = 0x40000000
OBJECT_INHERIT_CONTAINER_INHERIT = 0x03
INHERIT_ONLY = 0x08

requires_windows = pytest.mark.skipif(os.name != "nt", reason="reads real Windows ACLs")


def test_system_files_are_not_writable_by_users():
    aces = [
        (TRUSTED_INSTALLER, FULL_CONTROL, 0),
        (SYSTEM, MODIFY, 0),
        (ADMINISTRATORS, MODIFY, 0),
        (USERS, READ_AND_EXECUTE, 0),
    ]
    assert dacl_lets_users_write(aces) is False


def test_an_empty_dacl_lets_nobody_write():
    assert dacl_lets_users_write([]) is False


@pytest.mark.parametrize("sid", [USERS, EVERYONE, SOME_USER])
@pytest.mark.parametrize("mask", [MODIFY, FULL_CONTROL, GENERIC_WRITE])
def test_a_write_grant_to_a_user_principal_is_mutable(sid, mask):
    assert dacl_lets_users_write([(SYSTEM, FULL_CONTROL, 0), (sid, mask, 0)]) is True


def test_an_entry_that_only_applies_to_children_does_not_count():
    inherit_only = OBJECT_INHERIT_CONTAINER_INHERIT | INHERIT_ONLY
    assert dacl_lets_users_write([(SOME_USER, FULL_CONTROL, inherit_only)]) is False


@requires_windows
def test_system_executables_are_trusted_and_the_temp_dir_is_not(tmp_path):
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    cmd = system32 / "cmd.exe"

    assert windows_path_is_user_mutable(cmd) is False
    assert _is_trusted_system_executable(cmd, cmd) is True
    assert windows_path_is_user_mutable(tmp_path) is True
    assert _is_trusted_system_directory(tmp_path) is False


@requires_windows
def test_the_child_path_holds_the_system_directories_and_the_installed_git(tmp_path):
    entries = _controlled_child_path(tmp_path).split(os.pathsep)
    system32 = str((Path(os.environ["SystemRoot"]) / "System32").resolve())

    assert system32 in [str(Path(entry).resolve()) for entry in entries]
    assert all(Path(entry).is_absolute() for entry in entries)
    git = shutil.which("git", path=os.pathsep.join(entries))
    if git is None:
        pytest.skip("Git for Windows is not installed under a trusted directory")
    assert _is_trusted_system_executable(Path(git), Path(git).resolve()) is True


def test_windows_exec_dirs_keep_only_absolute_trusted_unique_entries(monkeypatch, tmp_path):
    from kernel import terminal_env

    system_root = tmp_path / "Windows"
    trusted_git = tmp_path / "Git" / "cmd"
    user_tools = tmp_path / "Users" / "tools"
    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([".", str(trusted_git), str(user_tools), str(trusted_git)]),
    )
    trusted = {system_root / "System32", system_root, trusted_git}
    monkeypatch.setattr(terminal_env, "_is_trusted_system_directory", lambda path: path in trusted)

    assert terminal_env._windows_exec_dirs() == [system_root / "System32", system_root, trusted_git]
