# -*- coding: utf-8 -*-
"""The system folder (~/.kvist/workspace): how Kvist recognises it, what the model is told, and sudo there."""
from __future__ import annotations

import os
import stat
import tempfile

import pytest

from kernel.config.system_workspace import is_system_workspace, system_workspace_path
from kernel.context.system_context import (
    AGENT_INSTRUCTIONS_FILE,
    CHAT_INSTRUCTIONS_FILE,
    current_workspace_context,
    ensure_workspace_instructions,
    workspace_instructions_context,
)
from kernel.sandbox import terminal as terminal_module
from kernel.sandbox.terminal import HardDenyError, TerminalExecutor
from kernel.sandbox.terminal_sudo import AskpassFeed
from kernel.tests.conftest import requires_posix

_FAKE_SUDO = """#!/bin/sh
# Stands in for sudo: takes the password from the askpass helper as the real one does with -A.
[ "$1" = -A ] && [ "$2" = -k ] || exit 90
password=$("$SUDO_ASKPASS")
[ "$password" = right-password ] || { echo "Sorry, try again." >&2; exit 1; }
shift 2
[ "$1" = -v ] && exit 0
shift
env | grep -q right-password && echo LEAKED
echo "as root: $*"
"""


@pytest.fixture
def system_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "config"))
    folder = system_workspace_path()
    folder.mkdir(parents=True)
    return folder


class _Gate:
    def __init__(self, approve: bool = True, password: str | None = "right-password") -> None:
        self.approve = approve
        self.password = password
        self.commands: list[tuple[list[str], str]] = []
        self.confirms: list[str] = []
        self.secrets = 0

    def confirm_command(self, command, reason, timeout=None):
        self.commands.append((command, reason))
        return self.approve

    def confirm(self, title, body, question, timeout=None):
        self.confirms.append(title)
        return True

    def ask_secret(self, title, help_text):
        self.secrets += 1
        return self.password

    def commit_remembered_rule(self):
        pass

    def waiting_confirm_command(self):
        return None


def _fake_sudo(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text(_FAKE_SUDO, encoding="utf-8")
    sudo.chmod(sudo.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_the_system_folder_is_recognised_by_its_place_only(system_folder, tmp_path):
    assert system_folder == tmp_path / "config" / "workspace"
    assert is_system_workspace(system_folder)
    other = tmp_path / "project"
    other.mkdir()
    assert not is_system_workspace(other)


def test_the_model_is_told_the_system_folder_is_not_a_project(system_folder, tmp_path):
    assert "not a project" in current_workspace_context(system_folder)
    project = tmp_path / "project"
    project.mkdir()
    assert "not a project" not in current_workspace_context(project)


def test_the_system_folder_gets_a_notebook_on_the_computer(system_folder):
    ensure_workspace_instructions(system_folder)

    notebook = (system_folder / AGENT_INSTRUCTIONS_FILE).read_text(encoding="utf-8")
    assert "## This computer" in notebook
    assert not (system_folder / CHAT_INSTRUCTIONS_FILE).exists()
    # Untouched, it states nothing yet and is not quoted as rules.
    assert "still holds the template" in workspace_instructions_context(system_folder, "coder")


@requires_posix
def test_sudo_stays_forbidden_outside_the_system_folder(system_folder, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    executor = TerminalExecutor(project, timeout=5, approvals=_Gate())

    with pytest.raises(HardDenyError, match="unconditionally forbidden"):
        executor.run(["sudo", "id", "-u"])


@requires_posix
@pytest.mark.parametrize(
    ("command", "message"),
    [
        (["sudo", "-E", "id"], "no options"),
        (["sudo", "-s"], "no options"),
        (["sudo"], "no options"),
        (["sudo", "cat", "x", "|", "tee", "y"], "pipe"),
        (["sudo", "rm", "-rf", "/tmp/x"], "unconditionally forbidden"),
        (["sudo", "sudo", "id"], "unconditionally forbidden"),
    ],
)
def test_sudo_takes_only_a_program_that_is_allowed_without_it(system_folder, command, message):
    gate = _Gate()
    executor = TerminalExecutor(system_folder, timeout=5, approvals=gate)

    with pytest.raises(HardDenyError, match=message):
        executor.run(command)
    assert gate.secrets == 0


@requires_posix
def test_sudo_takes_no_environment_overrides(system_folder):
    executor = TerminalExecutor(system_folder, timeout=5, approvals=_Gate())

    with pytest.raises(HardDenyError, match="environment overrides"):
        executor.run(["sudo", "id"], env_overrides={"LANG": "C"})


@requires_posix
def test_sudo_refused_by_the_person_asks_no_password(system_folder, tmp_path, monkeypatch):
    _fake_sudo(tmp_path, monkeypatch)
    gate = _Gate(approve=False)
    executor = TerminalExecutor(system_folder, timeout=5, approvals=gate)

    result = executor.run(["sudo", "id", "-u"])

    assert result.denied
    assert gate.secrets == 0
    assert "root" in gate.commands[0][1]


@requires_posix
def test_sudo_runs_after_approval_with_the_password_asked_once(system_folder, tmp_path, monkeypatch):
    _fake_sudo(tmp_path, monkeypatch)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    gate = _Gate()
    executor = TerminalExecutor(system_folder, timeout=10, approvals=gate)

    first = executor.run(["sudo", "id", "-u"])
    second = executor.run(["sudo", "uname"])

    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "as root: id -u"
    assert second.stdout.strip() == "as root: uname"
    assert gate.secrets == 1
    assert len(gate.commands) == 2
    # One question per command: the root approval, not a second one about leaving the sandbox.
    assert gate.confirms == []
    assert not list(temp.glob("kvist-sudo-*"))


@requires_posix
def test_a_wrong_password_is_not_kept(system_folder, tmp_path, monkeypatch):
    _fake_sudo(tmp_path, monkeypatch)
    gate = _Gate(password="wrong")
    executor = TerminalExecutor(system_folder, timeout=10, approvals=gate)

    result = executor.run(["sudo", "id", "-u"])
    assert result.denied
    assert "did not accept" in result.stderr

    gate.password = "right-password"
    assert executor.run(["sudo", "id", "-u"]).returncode == 0
    assert gate.secrets == 2


@requires_posix
def test_sudo_without_a_way_to_ask_for_the_password_does_not_run(system_folder, tmp_path, monkeypatch):
    _fake_sudo(tmp_path, monkeypatch)
    gate = _Gate()
    monkeypatch.delattr(_Gate, "ask_secret")
    executor = TerminalExecutor(system_folder, timeout=5, approvals=gate)

    result = executor.run(["sudo", "id", "-u"])

    assert result.denied
    assert "no password" in result.stderr


@requires_posix
def test_the_askpass_feed_serves_the_password_once_and_leaves_nothing(tmp_path):
    import subprocess

    feed = AskpassFeed()
    feed.serve("right-password")
    env = {"PATH": os.environ["PATH"], **feed.env()}
    first = subprocess.run([str(feed.helper)], env=env, capture_output=True, text=True, timeout=5, check=False)
    second = subprocess.run([str(feed.helper)], env=env, capture_output=True, text=True, timeout=5, check=False)
    directory = feed.directory
    feed.close()

    assert first.stdout == "right-password\n"
    assert second.returncode != 0 and second.stdout == ""
    assert not directory.exists()


def test_sudo_is_not_offered_in_a_container(system_folder, monkeypatch):
    executor = TerminalExecutor(system_folder, timeout=5, approvals=_Gate())
    monkeypatch.setattr(executor, "sandbox_mode", terminal_module.SandboxMode.CONTAINER)

    assert not executor._is_sudo_command(["sudo", "id"])
