# -*- coding: utf-8 -*-
"""Small rules the runner applies to Kvist's output: key scrubbing, provider errors, machine limits."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger("kvist_runner")
REDACTED = "[redacted]"
MIN_SECRET_LENGTH = 8

# Kvist reports model failures as text (agent_model.py); a structured event would be better.
_AUTH = re.compile(
    r"\b(401|403)\b|authentication|invalid[ _-]?api[ _-]?key|incorrect api key|unauthori[sz]ed",
    re.I,
)
_TRANSIENT = re.compile(
    r"\b(429|5\d\d)\b|rate limit|too many requests|overloaded|server error|service unavailable|upstream", re.I
)


def scrub(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if len(secret) >= MIN_SECRET_LENGTH and secret in text:
            text = text.replace(secret, REDACTED)
    return text


def provider_trouble(event: dict) -> str:
    """'auth' for a wrong key, 'transient' for 429/5xx, '' for anything else."""
    kind = event.get("type")
    if kind == "error":
        text = str(event.get("message", ""))
    elif kind == "status" and event.get("is_error"):
        text = str(event.get("user_message", ""))
    else:
        return ""
    if _AUTH.search(text):
        return "auth"
    if _TRANSIENT.search(text):
        return "transient"
    return ""


_systemd: bool | None = None


def systemd_available() -> bool:
    """Can this user start transient scopes? Checked once."""
    global _systemd
    if _systemd is not None:
        return _systemd
    if shutil.which("systemd-run") is None:
        _systemd = False
        return False
    try:
        probe = subprocess.run(
            ["systemd-run", "--user", "--scope", "-q", "--", "true"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _systemd = False
        return False
    _systemd = probe.returncode == 0
    return _systemd


def with_machine_limits(argv: list[str], vcpu: int, memory_gb: int) -> list[str]:
    """Kvist and every command it starts share one scope with the plan's CPU and memory."""
    if vcpu <= 0 or memory_gb <= 0:
        return argv  # the Remote plan: the owner's own machine, the owner's own limits
    if not systemd_available():
        log.warning("systemd-run is not available: Kvist runs without CPU and memory limits")
        return argv
    return [
        "systemd-run",
        "--user",
        "--scope",
        "-q",
        "-p",
        f"MemoryMax={memory_gb}G",
        "-p",
        f"CPUQuota={vcpu * 100}%",
        "--",
        *argv,
    ]
