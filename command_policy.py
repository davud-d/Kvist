# -*- coding: utf-8 -*-
"""Policy layer for shell command risk classification.

Analyzes commands at the subcommand and argument level, not just
the first word. Takes agent role into account during assessment.

Examples:
    git status       → SAFE
    git diff         → SAFE
    git reset --hard → DANGEROUS
    docker ps        → SAFE
    docker compose up → DANGEROUS (for coder), MODERATE (for devops)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RiskLevel(Enum):
    """Command risk level."""
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"


@dataclass(slots=True)
class CommandAssessment:
    """Result of a command risk assessment."""
    command: list[str]
    risk: RiskLevel
    reason: str
    requires_approval: bool


# ── Subcommand rules ────────────────────────────────────────

_SUBCOMMAND_RULES: dict[str, dict[str, RiskLevel]] = {
    "git": {
        "status": RiskLevel.SAFE,
        "diff": RiskLevel.SAFE,
        "branch": RiskLevel.SAFE,
        "log": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "tag": RiskLevel.SAFE,
        "remote": RiskLevel.SAFE,
        "stash": RiskLevel.SAFE,
        "ls-files": RiskLevel.SAFE,
        "rev-parse": RiskLevel.SAFE,
        "add": RiskLevel.MODERATE,
        "commit": RiskLevel.MODERATE,
        "push": RiskLevel.MODERATE,
        "pull": RiskLevel.MODERATE,
        "merge": RiskLevel.MODERATE,
        "fetch": RiskLevel.SAFE,
        "clone": RiskLevel.MODERATE,
        "checkout": RiskLevel.MODERATE,
        "reset": RiskLevel.DANGEROUS,
        "clean": RiskLevel.DANGEROUS,
        "rebase": RiskLevel.DANGEROUS,
        "force-push": RiskLevel.DANGEROUS,
    },
    "docker": {
        "ps": RiskLevel.SAFE,
        "images": RiskLevel.SAFE,
        "logs": RiskLevel.SAFE,
        "inspect": RiskLevel.SAFE,
        "stats": RiskLevel.SAFE,
        "info": RiskLevel.SAFE,
        "version": RiskLevel.SAFE,
        "build": RiskLevel.MODERATE,
        "pull": RiskLevel.MODERATE,
        "tag": RiskLevel.MODERATE,
        "run": RiskLevel.MODERATE,
        "exec": RiskLevel.MODERATE,
        "start": RiskLevel.MODERATE,
        "stop": RiskLevel.MODERATE,
        "restart": RiskLevel.MODERATE,
        "compose": RiskLevel.DANGEROUS,
        "rm": RiskLevel.DANGEROUS,
        "rmi": RiskLevel.DANGEROUS,
        "prune": RiskLevel.DANGEROUS,
        "system": RiskLevel.DANGEROUS,
        "volume": RiskLevel.MODERATE,
        "network": RiskLevel.MODERATE,
    },
    "pip": {
        "list": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "freeze": RiskLevel.SAFE,
        "check": RiskLevel.SAFE,
        "install": RiskLevel.MODERATE,
        "uninstall": RiskLevel.DANGEROUS,
    },
    "pip3": {
        "list": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "freeze": RiskLevel.SAFE,
        "check": RiskLevel.SAFE,
        "install": RiskLevel.MODERATE,
        "uninstall": RiskLevel.DANGEROUS,
    },
    "npm": {
        "list": RiskLevel.SAFE,
        "ls": RiskLevel.SAFE,
        "view": RiskLevel.SAFE,
        "info": RiskLevel.SAFE,
        "outdated": RiskLevel.SAFE,
        "audit": RiskLevel.SAFE,
        "run": RiskLevel.MODERATE,
        "test": RiskLevel.MODERATE,
        "install": RiskLevel.MODERATE,
        "ci": RiskLevel.MODERATE,
        "uninstall": RiskLevel.DANGEROUS,
        "prune": RiskLevel.DANGEROUS,
    },
    "systemctl": {
        "status": RiskLevel.SAFE,
        "is-active": RiskLevel.SAFE,
        "is-enabled": RiskLevel.SAFE,
        "list-units": RiskLevel.SAFE,
        "list-unit-files": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "start": RiskLevel.DANGEROUS,
        "stop": RiskLevel.DANGEROUS,
        "restart": RiskLevel.DANGEROUS,
        "reload": RiskLevel.DANGEROUS,
        "enable": RiskLevel.DANGEROUS,
        "disable": RiskLevel.DANGEROUS,
        "daemon-reload": RiskLevel.DANGEROUS,
        "mask": RiskLevel.DANGEROUS,
    },
    "apt": {
        "list": RiskLevel.SAFE,
        "search": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "policy": RiskLevel.SAFE,
        "install": RiskLevel.DANGEROUS,
        "remove": RiskLevel.DANGEROUS,
        "purge": RiskLevel.DANGEROUS,
        "update": RiskLevel.MODERATE,
        "upgrade": RiskLevel.DANGEROUS,
        "autoremove": RiskLevel.DANGEROUS,
    },
}

# ── Commands with unconditional risk level ──────────────────────────

_ALWAYS_SAFE: frozenset[str] = frozenset({
    "ls", "dir", "pwd", "cat", "head", "tail", "echo", "whoami",
    "which", "where", "file", "uname", "hostname", "wc",
    "find", "grep", "sort", "uniq", "diff", "test",
    "tree", "ver", "findstr", "type",
})

_ALWAYS_DANGEROUS: frozenset[str] = frozenset({
    "rm", "rmdir", "del",
    "sudo", "su", "runas",
    "chmod", "chown",
    "mkfs", "format", "dd",
    "shutdown", "reboot", "poweroff",
    "curl", "wget",
    "nc", "ncat", "netcat",
    "ssh", "scp", "sftp",
    "telnet", "ftp",
})

# ── Dangerous flags (regardless of command) ────────────────────────

_DANGEROUS_FLAGS: frozenset[str] = frozenset({
    "--hard", "--force", "-f", "--no-preserve-root",
    "--recursive", "-rf", "-fr",
})

# ── DevOps: risk downgrades for specific commands ──────────────

_DEVOPS_DOWNGRADES: dict[str, dict[str, RiskLevel]] = {
    "docker": {
        "compose": RiskLevel.MODERATE,
        "run": RiskLevel.MODERATE,
        "stop": RiskLevel.MODERATE,
        "restart": RiskLevel.MODERATE,
        "rm": RiskLevel.MODERATE,
    },
    "systemctl": {
        "start": RiskLevel.MODERATE,
        "stop": RiskLevel.MODERATE,
        "restart": RiskLevel.MODERATE,
        "reload": RiskLevel.MODERATE,
        "enable": RiskLevel.MODERATE,
        "disable": RiskLevel.MODERATE,
        "daemon-reload": RiskLevel.MODERATE,
    },
    "apt": {
        "install": RiskLevel.MODERATE,
        "update": RiskLevel.SAFE,
        "remove": RiskLevel.MODERATE,
    },
    "pip": {
        "install": RiskLevel.SAFE,
    },
    "pip3": {
        "install": RiskLevel.SAFE,
    },
    "npm": {
        "install": RiskLevel.SAFE,
    },
}


def assess_command(command: list[str], role: str = "coder") -> CommandAssessment:
    """Analyzes a command and determines its risk level.

    Args:
        command: List of command strings, e.g. ["git", "reset", "--hard"].
        role: Agent role ("coder", "devops", "chat").

    Returns:
        CommandAssessment with risk level and approval recommendation.
    """
    if not command:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="Empty command",
            requires_approval=True,
        )

    executable = command[0].lower()

    # Unconditionally dangerous commands
    if executable in _ALWAYS_DANGEROUS:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason=f"Command '{executable}' is unconditionally dangerous",
            requires_approval=True,
        )

    # Unconditionally safe commands
    if executable in _ALWAYS_SAFE:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.SAFE,
            reason=f"Command '{executable}' is safe (read-only)",
            requires_approval=False,
        )

    # Version commands are always safe
    if len(command) >= 2 and command[1] in {"--version", "-V", "-v", "version"}:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.SAFE,
            reason=f"Version check for '{executable}'",
            requires_approval=False,
        )

    # Subcommand analysis
    subcommand = command[1].lstrip("-") if len(command) > 1 else ""
    risk = _assess_with_subcommand(executable, subcommand, role)

    # Check for dangerous flags (raises risk)
    has_dangerous_flag = any(arg in _DANGEROUS_FLAGS for arg in command[1:])
    if has_dangerous_flag and risk != RiskLevel.DANGEROUS:
        risk = RiskLevel.DANGEROUS

    # Build reason
    reason = _build_reason(executable, subcommand, risk, has_dangerous_flag)

    # Determine if approval is needed
    requires_approval = _requires_approval(risk, role)

    return CommandAssessment(
        command=command,
        risk=risk,
        reason=reason,
        requires_approval=requires_approval,
    )


def _assess_with_subcommand(
    executable: str,
    subcommand: str,
    role: str,
) -> RiskLevel:
    """Determines risk based on executable + subcommand + role."""
    # Check subcommand rules
    if executable in _SUBCOMMAND_RULES and subcommand:
        rules = _SUBCOMMAND_RULES[executable]
        base_risk = rules.get(subcommand, RiskLevel.MODERATE)

        # For devops: apply risk downgrade
        if role == "devops" and executable in _DEVOPS_DOWNGRADES:
            downgraded = _DEVOPS_DOWNGRADES[executable].get(subcommand)
            if downgraded is not None:
                base_risk = downgraded

        return base_risk

    # For executables without subcommand rules
    if executable in _SUBCOMMAND_RULES:
        # Executable is known, but subcommand is unrecognized
        return RiskLevel.MODERATE

    # Completely unknown command
    return RiskLevel.MODERATE


def _build_reason(
    executable: str,
    subcommand: str,
    risk: RiskLevel,
    has_dangerous_flag: bool,
) -> str:
    """Builds a human-readable explanation."""
    parts = [f"'{executable}"]
    if subcommand:
        parts[0] += f" {subcommand}'"
    else:
        parts[0] += "'"

    if has_dangerous_flag:
        parts.append("contains dangerous flags")

    parts.append(f"→ {risk.value}")
    return " ".join(parts)


def _requires_approval(risk: RiskLevel, role: str) -> bool:
    """Determines whether to ask the user."""
    if risk == RiskLevel.SAFE:
        return False
    if risk == RiskLevel.DANGEROUS:
        return True
    # MODERATE
    if role == "devops":
        return False  # DevOps can execute moderate without asking
    return False  # Coder can also do moderate (pip install, python, etc.)
