# -*- coding: utf-8 -*-
"""Container mode: the benchmark container is the sandbox, and nowhere else is."""
from __future__ import annotations

import pytest

from kernel.tests.conftest import requires_posix

from kernel.sandbox import sandbox
from kernel.sandbox.approvals import ContainerApprovalGate
from kernel.sandbox.sandbox import SandboxMode, detect_sandbox_mode
from kernel.sandbox.terminal import HardDenyError, TerminalExecutor


@pytest.fixture
def inside_container(monkeypatch, tmp_path):
    marker = tmp_path / ".containerenv"
    marker.write_text("", encoding="utf-8")
    monkeypatch.setattr(sandbox, "_CONTAINER_MARKERS", (marker,))
    monkeypatch.setattr(sandbox, "resolve_bubblewrap_path", lambda: None)
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "container")


@pytest.fixture
def outside_container(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "_CONTAINER_MARKERS", (tmp_path / "absent",))
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "container")


def test_container_mode_is_honoured_inside_a_container(inside_container):
    mode, note = detect_sandbox_mode()

    assert mode is SandboxMode.CONTAINER
    assert "container is the sandbox" in note


def test_container_mode_outside_a_container_falls_back_to_bubblewrap(outside_container, monkeypatch):
    monkeypatch.setattr(sandbox, "resolve_bubblewrap_path", lambda: "/usr/bin/bwrap")

    mode, note = detect_sandbox_mode()

    assert mode is SandboxMode.BUBBLEWRAP
    assert "refused" in note


def test_refused_container_mode_without_bwrap_gets_the_strict_fallback(outside_container, monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox, "resolve_bubblewrap_path", lambda: None)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    executor = TerminalExecutor(workspace)

    assert executor.sandbox_mode is SandboxMode.WORKSPACE_ONLY
    # Not the explicit (weaker) workspace mode: arbitrary code still needs approval.
    assert executor._automatic_restricted_fallback is True


def test_container_gate_refuses_to_exist_outside_a_container(outside_container):
    with pytest.raises(RuntimeError, match="only allowed inside"):
        ContainerApprovalGate()


def test_container_gate_says_yes_and_logs_it(inside_container, capsys):
    gate = ContainerApprovalGate()

    assert gate.confirm_command(["curl", "x"], "dangerous") is True
    assert gate.confirm("Network", "body", "Allow?") is True
    assert gate.confirm_fetch_domain("example.com", "https://example.com/") == "once"
    assert "approved" in capsys.readouterr().err


@requires_posix
def test_container_terminal_runs_what_the_host_rules_forbid(inside_container, monkeypatch, tmp_path):
    workspace = tmp_path / "app"
    workspace.mkdir()
    outside = tmp_path / "outside"
    monkeypatch.setenv("INCEPTION_API_KEY", "sk-test-not-for-children-1234567890")
    executor = TerminalExecutor(workspace, approvals=ContainerApprovalGate())

    written = executor.run(["bash", "-c", f"mkdir -p {outside} && echo hi > {outside}/x && cat {outside}/x"])
    removed = executor.run(["rm", "-rf", str(outside)])
    env = executor.run(["bash", "-c", 'echo "home=$HOME key=${INCEPTION_API_KEY:-none}"'])

    assert written.returncode == 0 and written.stdout.strip() == "hi"
    assert removed.returncode == 0 and not outside.exists()
    # Tools a task installs into HOME must still be there for the next command.
    assert "home=/" in env.stdout and "kvist-home-" not in env.stdout
    assert "key=none" in env.stdout


def test_container_terminal_still_keeps_structural_checks(inside_container, tmp_path):
    executor = TerminalExecutor(tmp_path, approvals=ContainerApprovalGate())

    with pytest.raises(HardDenyError, match="NUL"):
        executor.run(["echo", "a\x00b"])
