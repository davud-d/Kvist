from __future__ import annotations

from command_policy import RiskLevel, assess_command


def test_assess_command_classifies_safe_read_only_command():
    result = assess_command(["git", "status"])
    assert result.risk is RiskLevel.SAFE
    assert result.requires_approval is False


def test_assess_command_blocks_empty_and_unconditionally_dangerous_commands():
    assert assess_command([]).risk is RiskLevel.DANGEROUS
    rm = assess_command(["rm", "-rf", "build"])
    assert rm.risk is RiskLevel.DANGEROUS
    assert rm.requires_approval is True


def test_assess_command_raises_risk_for_dangerous_flags():
    result = assess_command(["git", "reset", "--hard"])
    assert result.risk is RiskLevel.DANGEROUS
    assert "dangerous flags" in result.reason


def test_assess_command_applies_devops_downgrade():
    coder = assess_command(["apt", "install", "pkg"], role="coder")
    devops = assess_command(["apt", "install", "pkg"], role="devops")
    assert coder.risk is RiskLevel.DANGEROUS
    assert devops.risk is RiskLevel.MODERATE


def test_assess_command_unknown_command_is_moderate_without_approval():
    result = assess_command(["custom-tool", "arg"])
    assert result.risk is RiskLevel.MODERATE
    assert result.requires_approval is False
