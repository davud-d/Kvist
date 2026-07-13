from __future__ import annotations

from pathlib import Path

import sandbox
from sandbox import SandboxMode, detect_sandbox_mode, wrap_command_for_sandbox
from terminal import TerminalExecutor


def test_detect_bubblewrap_when_available(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    mode, note = detect_sandbox_mode("bubblewrap")

    assert mode is SandboxMode.BUBBLEWRAP
    assert note == ""


def test_detect_degrades_without_bwrap(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    mode, note = detect_sandbox_mode("bubblewrap")

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "degraded" in note.casefold()


def test_detect_auto_defaults_to_workspace_only(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    mode, note = detect_sandbox_mode("auto")

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert note == ""


def test_detect_auto_hints_when_bwrap_available(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    mode, note = detect_sandbox_mode("auto")

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "bubblewrap" in note.casefold()


def test_detect_unknown_value_falls_back_with_note():
    mode, note = detect_sandbox_mode("chroot")

    assert mode is SandboxMode.WORKSPACE_ONLY
    assert "chroot" in note


def test_wrap_command_noop_for_workspace_only(tmp_path):
    command = ["ls", "-la"]
    wrapped = wrap_command_for_sandbox(command, SandboxMode.WORKSPACE_ONLY, tmp_path, tmp_path)
    assert wrapped == command


def test_wrap_command_builds_bwrap_invocation(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    wrapped = wrap_command_for_sandbox(["ls", "-la"], SandboxMode.BUBBLEWRAP, workspace, workspace)

    assert wrapped[0] == "bwrap"
    assert "--ro-bind" in wrapped
    bind_index = wrapped.index("--bind")
    assert wrapped[bind_index + 1] == str(workspace)
    separator_index = wrapped.index("--")
    assert wrapped[separator_index + 1 :] == ["ls", "-la"]


def test_terminal_uses_bubblewrap_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "bubblewrap")
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")

    executor = TerminalExecutor(tmp_path)

    assert executor.sandbox_mode is SandboxMode.BUBBLEWRAP


def test_terminal_degrades_to_workspace_only_without_bwrap(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "bubblewrap")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    executor = TerminalExecutor(tmp_path)

    assert executor.sandbox_mode is SandboxMode.WORKSPACE_ONLY


def test_terminal_default_mode_is_workspace_only(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_SANDBOX_MODE", raising=False)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)

    executor = TerminalExecutor(tmp_path)

    assert executor.sandbox_mode is SandboxMode.WORKSPACE_ONLY
