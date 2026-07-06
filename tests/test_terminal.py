from __future__ import annotations

import pytest

from terminal import TerminalError, TerminalExecutor


def test_terminal_allows_common_read_only_command(tmp_path):
    executor = TerminalExecutor(tmp_path, timeout=2)
    result = executor.run(["python", "--version"])

    assert result.returncode == 0
    assert "Python" in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "command,error",
    [
        ([], "Empty command"),
        (["rm", "-rf", "x"], "forbidden"),
        (["git", "reset", "--hard"], "Subcommand"),
        (["python", "-c", "print(1)"], "Inline mode"),
        (["echo", "$(whoami)"], "Command substitution"),
        (["cat", "../secret"], "Path traversal"),
        (["echo", "/etc/passwd"], "Absolute paths"),
        (["sed", "-i", "s/a/b/", "file"], "In-place"),
    ],
)
def test_terminal_validation_rejects_dangerous_inputs(tmp_path, command, error):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match=error):
        executor._validate(command)


def test_terminal_restricted_profile_blocks_dev_tools(tmp_path):
    executor = TerminalExecutor(tmp_path, profile="restricted")
    with pytest.raises(TerminalError, match="forbidden"):
        executor._validate(["python", "--version"])


def test_terminal_resolves_cwd_inside_workspace_only(tmp_path):
    (tmp_path / "sub").mkdir()
    executor = TerminalExecutor(tmp_path)

    assert executor._resolve_cwd("sub") == (tmp_path / "sub").resolve()
    with pytest.raises(TerminalError, match="outside workspace|Absolute paths"):
        executor._resolve_cwd("../outside")


def test_terminal_clamps_timeout_and_truncates_output(tmp_path):
    executor = TerminalExecutor(tmp_path, timeout=99, max_output_chars=1000)
    assert executor.timeout == 30.0
    assert executor._clamp_timeout(0.1) == 1.0
    assert executor._clamp_timeout(99) == 30.0
    assert "[TRUNCATED" in executor._truncate("x" * 1001)


# ── DevOps systemctl allowlist ──────────────────────────────────────

def test_systemctl_status_allowed_in_dev_profile(tmp_path):
    """systemctl status nginx --no-pager passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    # Must not raise TerminalError
    executor._validate(["systemctl", "status", "nginx", "--no-pager"])


def test_systemctl_is_active_allowed(tmp_path):
    """systemctl is-active nginx passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "is-active", "nginx"])


def test_systemctl_is_enabled_allowed(tmp_path):
    """systemctl is-enabled nginx passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "is-enabled", "nginx"])


def test_systemctl_list_units_allowed(tmp_path):
    """systemctl list-units --type=service passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "list-units", "--type=service"])


def test_systemctl_list_unit_files_allowed(tmp_path):
    """systemctl list-unit-files nginx.service passes terminal validation."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    executor._validate(["systemctl", "list-unit-files", "nginx.service"])


@pytest.mark.parametrize(
    "command",
    [
        ["systemctl", "start", "nginx"],
        ["systemctl", "stop", "nginx"],
        ["systemctl", "restart", "nginx"],
        ["systemctl", "reload", "nginx"],
        ["systemctl", "enable", "nginx"],
        ["systemctl", "disable", "nginx"],
        ["systemctl", "daemon-reload"],
        ["systemctl", "mask", "nginx"],
    ],
)
def test_systemctl_dangerous_subcommands_blocked(tmp_path, command):
    """Dangerous systemctl subcommands are blocked by subcommand validator."""
    executor = TerminalExecutor(tmp_path, profile="dev")
    with pytest.raises(TerminalError, match="Subcommand"):
        executor._validate(command)


def test_systemctl_blocked_in_restricted_profile(tmp_path):
    """systemctl is NOT available in restricted profile (dev tools not loaded)."""
    executor = TerminalExecutor(tmp_path, profile="restricted")
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["systemctl", "status", "nginx", "--no-pager"])
