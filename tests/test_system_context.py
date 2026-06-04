from __future__ import annotations

import system_context


def test_current_os_context_for_linux(monkeypatch):
    monkeypatch.setattr(system_context.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system_context.distro, "name", lambda: "TestLinux")
    monkeypatch.setattr(system_context.distro, "version", lambda: "1")
    assert system_context.current_os_context() == "TestLinux 1"


def test_current_shell_context_uses_shell_env(monkeypatch):
    monkeypatch.setattr(system_context.platform, "system", lambda: "Linux")
    monkeypatch.setenv("SHELL", "/bin/bash")
    assert system_context.current_shell_context() == "bash"


def test_current_time_context_returns_key():
    assert "current_time" in system_context.current_time_context()
