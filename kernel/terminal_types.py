# -*- coding: utf-8 -*-
"""Result, error and execution-binding types shared by the terminal modules."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(slots=True)
class TerminalResult:
    """Result of a terminal command execution."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    denied: bool = False


class TerminalError(Exception):
    """Terminal command validation or execution error."""


class HardDenyError(TerminalError):
    """Non-overridable security boundary violation."""


class ApprovalRequiredError(TerminalError):
    """Execution is eligible only after an exact per-execution approval."""


class CommandNotAllowedError(ApprovalRequiredError):
    """Allowlist miss (unknown executable or subcommand).

    Unlike hard validation errors (forbidden executables, secret paths,
    shell operators), an allowlist miss may be overridden by an explicit
    allow-rule in the project permission policy.
    """


FileIdentity = tuple[str, int, int, int, int, int]


class ExecutableOrigin(str, Enum):
    TRUSTED_SYSTEM = "trusted_system"
    VERIFIED_VENV = "verified_venv"
    WORKSPACE_FILE = "workspace_file"
    WORKSPACE_ALIAS = "workspace_alias"
    EXTERNAL_TOOL = "external_tool"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ExecutableBinding:
    """The requested and effective executable selected for one segment.

    ``token`` is the name/path supplied in argv, while ``lookup_path`` is the
    entry found either explicitly or through PATH and ``resolved_path`` is the
    final binary the OS will execute.  The remaining origin fields make that
    distinction explicit so security decisions do not have to reconstruct it.
    """

    token: str
    requested_name: str
    effective_name: str | None
    # Exact entry selected by explicit argv lookup or the controlled PATH.
    lookup_path: str | None
    lookup_in_workspace: bool
    target_in_workspace: bool
    lookup_is_symlink: bool
    lookup_is_alias: bool
    origin: ExecutableOrigin
    # Final symlink-resolved executable and its policy-level identity.
    resolved_path: str | None
    requested_identity: str
    effective_identity: str | None
    # ``None`` means no effective executable could be resolved.
    identity_matches: bool | None
    identity_mismatch: bool
    lookup_identity: FileIdentity | None
    target_identity: FileIdentity | None
    trusted_system: bool
    content_digest: str | None
    # Interpreter selected by a ``#!`` line when the resolved target is an
    # executable workspace script the kernel would hand to another program.
    interpreter_path: str | None = None
    interpreter_identity: FileIdentity | None = None
    interpreter_digest: str | None = None
    interpreter_trusted_system: bool = False
    # Narrow exception: true only when this binding is a verified project
    # venv Python launcher (workspace-relative .venv|venv/bin[/Scripts]
    # launcher, attested pyvenv.cfg, final target is a trusted-system Python
    # interpreter — never Bash/sh/env/Perl/Node).  This is the *only* case in
    # which a workspace-lookup symlink to a trusted system target is treated
    # as a trusted execution origin; a generic workspace-to-system alias
    # (e.g. ``tools/cat -> /usr/bin/bash``) is never granted this status.
    venv_launcher: bool = False
    venv_pyvenv_cfg_identity: FileIdentity | None = None
    venv_pyvenv_cfg_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Security-relevant snapshot bound to one approval decision."""

    argv: tuple[str, ...]
    cwd: str
    cwd_identity: tuple[str, int, int] | None
    role: str
    sandbox_mode: str
    sandbox_profile: str
    network_mode: str
    environment_digest: str
    policy_digest: str
    sandbox_executable_identity: FileIdentity | None
    executable_bindings: tuple[ExecutableBinding, ...]
    referenced_files: tuple[tuple[str, str], ...]
    secret_mask_digest: str
    tool_paths: tuple[str, ...]
