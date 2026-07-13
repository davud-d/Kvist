# -*- coding: utf-8 -*-
"""OS-level sandbox detection and command wrapping for shell execution.

Honest model of what each mode guarantees:

- WORKSPACE_ONLY — NOT a real OS sandbox. It means: cwd is confined to the
  workspace, agent file actions are path-checked by SafeExecutor, and Unix
  resource limits are applied to child processes. The child process itself
  still runs with the full permissions of the current user and can, in
  principle, touch anything the user can.
- BUBBLEWRAP — shell commands are wrapped in ``bwrap`` with a read-only root
  filesystem and a writable workspace bind. Linux-only, opt-in via
  ``AGENT_SANDBOX_MODE=bubblewrap``. Network stays shared so package
  installs and web tooling keep working.
- NONE — no boundary at all. Never used by the terminal; exists so callers
  can report a truthful status.
"""
from __future__ import annotations

import os
import shutil
import sys
from enum import Enum
from pathlib import Path


class SandboxMode(Enum):
    NONE = "none"
    WORKSPACE_ONLY = "workspace_only"
    BUBBLEWRAP = "bubblewrap"


def bubblewrap_available() -> bool:
    return sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def detect_sandbox_mode(requested: str | None = None) -> tuple[SandboxMode, str]:
    """Resolve the effective sandbox mode and a human-readable note.

    Args:
        requested: Override for the ``AGENT_SANDBOX_MODE`` env var
            (``auto`` | ``workspace`` | ``bubblewrap``). ``None`` reads the env.

    Returns:
        ``(mode, note)``. ``note`` is non-empty when the executor runs in a
        degraded mode relative to the request, or when a stronger mode is
        available but not enabled.
    """
    value = requested if requested is not None else os.getenv("AGENT_SANDBOX_MODE", "auto")
    value = (value or "auto").strip().lower()

    if value == "bubblewrap":
        if bubblewrap_available():
            return SandboxMode.BUBBLEWRAP, ""
        return (
            SandboxMode.WORKSPACE_ONLY,
            "AGENT_SANDBOX_MODE=bubblewrap requested, but bwrap was not found on this system. "
            "Running degraded: workspace-only boundary, shell commands are NOT OS-sandboxed.",
        )

    if value in {"auto", "workspace", "workspace_only", ""}:
        note = ""
        if value == "auto" and bubblewrap_available():
            note = (
                "bubblewrap detected but not enabled; shell commands run with user permissions "
                "(workspace-only boundary). Set AGENT_SANDBOX_MODE=bubblewrap to enable OS sandboxing."
            )
        return SandboxMode.WORKSPACE_ONLY, note

    return (
        SandboxMode.WORKSPACE_ONLY,
        f"Unknown AGENT_SANDBOX_MODE={value!r}; using workspace-only boundary.",
    )


def wrap_command_for_sandbox(
    command: list[str],
    mode: SandboxMode,
    workspace: Path,
    cwd: Path,
) -> list[str]:
    """Wrap an already validated command for the given sandbox mode.

    WORKSPACE_ONLY / NONE return the command unchanged: those modes rely on
    the caller's cwd confinement and validation, not on process isolation.
    """
    if mode is not SandboxMode.BUBBLEWRAP:
        return command
    return [
        "bwrap",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", str(workspace), str(workspace),
        "--unshare-pid",
        "--die-with-parent",
        "--chdir", str(cwd),
        "--",
        *command,
    ]
