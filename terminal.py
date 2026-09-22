# -*- coding: utf-8 -*-
"""Cross-platform terminal module for safe process execution."""
from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess  # noqa: F401  spawn boundary lives in process_tree; kept as the patch target
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator
from enum import Enum

from command_policy import (
    CommandAssessment,
    RiskLevel,
    _diff_has_output_option,
    _file_has_compile_option,
    _git_has_external_helper_option,
    _git_has_remerge_diff_option,
    _git_ref_filter_has_signature_atom,
    _git_has_signature_verification_option,
    _git_has_unsafe_submodule_option,
    _git_tag_has_signing_or_verification_option,
    _git_read_operation_arguments,
    _has_long_output_option,
    _hostname_is_read_only,
    _sort_has_output_or_program_option,
    _tree_has_output_option,
    _uniq_has_output_operand,
    assess_command,
    canonical_executable_name,
    is_bare_executable_token,
    is_exact_version_probe,
    is_find_action_token,
)
from approvals import ApprovalGate, TerminalApprovalGate
from env_utils import read_int_env
from permission_policy import append_allow_rule, load_policy
from process_tree import (
    ProcessSupervisionError,
    cgroup_supervision_available,
    run_supervised,
)
from protocol import MAX_SHELL_TIMEOUT_SECONDS
from redaction import redact_text, sanitize_display_text
from sandbox import (
    SandboxMode,
    detect_sandbox_mode,
    normalize_sandbox_request,
    resolve_bubblewrap_path,
    sandbox_network_allowed,
    validated_external_tool_path,
    wrap_command_for_sandbox,
)
from secret_paths import (
    UnsafeSecretPathError,
    count_workspace_inode_aliases,
    secret_mask_digest,
)

# ── Global HITL lock ─────────────────────────────────────────────
# Prevents background TUI rendering while the user is typing y/N.
_hitl_lock = threading.Lock()
_hitl_active = False


def is_hitl_active() -> bool:
    """True if a Human-in-the-Loop prompt is currently waiting for input."""
    with _hitl_lock:
        return _hitl_active


def _enter_hitl() -> None:
    global _hitl_active
    with _hitl_lock:
        _hitl_active = True


def _exit_hitl() -> None:
    global _hitl_active
    with _hitl_lock:
        _hitl_active = False


@contextmanager
def _alt_screen(console: Any) -> Iterator[None]:
    """Draw an approval prompt inside the terminal's alternate screen buffer.

    Entering the alternate buffer preserves the current screen; leaving it
    restores that screen exactly, so the prompt has hard boundaries and can
    never erase output above it. Outside a TTY it is a no-op.
    """
    use_alt = sys.stdin.isatty() and console.is_terminal
    if use_alt:
        console.set_alt_screen(True)
    try:
        yield
    finally:
        if use_alt:
            console.set_alt_screen(False)


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


# ---------------------------------------------------------------------------
# Executables that are unconditionally forbidden
# ---------------------------------------------------------------------------
_FORBIDDEN_EXECUTABLES: frozenset[str] = frozenset({
    # Deletion
    "rm", "rmdir", "del",
    # Privilege escalation
    "sudo", "su", "runas",
    # Destructive system commands
    "mkfs", "format", "dd",
    "shutdown", "reboot", "poweroff",
    # Remote access
    "ssh", "scp", "sftp", "telnet", "ftp",
    # Windows registry
    "reg", "regedit",
    # Generic execution wrappers can otherwise hide a forbidden executable.
    "env", "xargs", "parallel", "nohup", "nice", "timeout", "setsid",
    "chroot", "unshare", "nsenter", "busybox", "toybox",
})

_RESTRICTED_EXTRA_FORBIDDEN: frozenset[str] = frozenset({
    "python", "python3", "py",
    "pytest", "pip", "pip3", "uv",
    "node", "npm", "npx", "pnpm", "yarn",
    "cmake", "ctest", "make",
    "cargo", "go", "java", "javac", "dotnet",
    "powershell", "pwsh", "cmd", "cmd.exe",
    "bash", "sh", "zsh", "fish",
    "ruby", "perl", "php",
})

# ---------------------------------------------------------------------------
# Shell operators forbidden as standalone arguments
# ---------------------------------------------------------------------------
_FORBIDDEN_OPERATORS: frozenset[str] = frozenset({
    ";", "&&", "||",
    ">", ">>", "<", "<<", "<<<", "2>", "2>>", "|&",
    "&",
})

# ---------------------------------------------------------------------------
# Regex for detecting command substitution and dangerous constructs
# inside arguments ($(…), `…`, ${…})
# ---------------------------------------------------------------------------
_SUBST_RE = re.compile(r"\$\(|`|\$\{")

# ---------------------------------------------------------------------------
# Secret material paths: forbidden as an argument of ANY command,
# including read-only ones (cat, grep, find, ...), relative or absolute.
# ---------------------------------------------------------------------------
_TOKEN_PATH_PREFIX = r"(?:(?<![A-Za-z0-9_.-])|(?<=-[A-Za-z]))"
_TOKEN_PATH_SUFFIX = r"(?=$|[\\/]|[^A-Za-z0-9_.-])"
_SECRET_TOKEN_RE = re.compile(
    rf"{_TOKEN_PATH_PREFIX}(?:\.env(?:\.[\w.-]*)?|\.ssh|\.aws|\.gnupg)"
    rf"{_TOKEN_PATH_SUFFIX}"
    rf"|{_TOKEN_PATH_PREFIX}(?:id_rsa|id_ed25519|id_ecdsa|credentials)"
    rf"{_TOKEN_PATH_SUFFIX}"
    r"|\.(?:pem|key|p12|pfx|crt)(?=$|[^A-Za-z0-9_.-])"
    rf"|{_TOKEN_PATH_PREFIX}(?:\.netrc|\.npmrc|\.pypirc|\.git-credentials)"
    rf"{_TOKEN_PATH_SUFFIX}"
    rf"|{_TOKEN_PATH_PREFIX}(?:\.kube|\.azure){_TOKEN_PATH_SUFFIX}"
    rf"|{_TOKEN_PATH_PREFIX}proc[\\/](?:self|\d+)[\\/]"
    rf"(?:environ|cmdline|mem|fd|root){_TOKEN_PATH_SUFFIX}",
    re.IGNORECASE,
)

_INTERNAL_TOKEN_RE = re.compile(
    rf"{_TOKEN_PATH_PREFIX}\.kvist{_TOKEN_PATH_SUFFIX}", re.IGNORECASE
)

_AWK_SYSTEM_RE = re.compile(r"\bsystem\s*\(")
_AWK_LOAD_RE = re.compile(r"(^|[\s;{}])@load\b")
_AWK_INCLUDE_RE = re.compile(r"(^|[\s;{}])@include\b")
_AWK_EXTERNAL_PIPE_RE = re.compile(r"(?<!\|)\|(?!\|)|\|&")
_AWK_SAFE_LONG_OPTIONS: frozenset[str] = frozenset({
    "--characters-as-bytes", "--csv", "--traditional", "--posix", "--lint",
    "--lint-old", "--non-decimal-data", "--bignum", "--use-lc-numeric",
    "--optimize", "--no-optimize", "--gen-pot", "--version", "--help",
    "--usage", "--copyleft", "--copyright", "--re-interval", "--sandbox",
})
_AWK_SAFE_SHORT_OPTIONS: frozenset[str] = frozenset({
    "-b", "-c", "-C", "-g", "-h", "-L", "-M", "-N", "-O", "-P",
    "-r", "-S", "-t", "-V",
})

# GNU sed constructs that execute commands or write files:
# the standalone `e`/`w`/`W` commands and the `e` flag of s///.
_SED_STANDALONE_WRITE_RE = re.compile(
    r"(?:^|[;\n{}])\s*"
    r"(?:(?:\d+(?:~\d+)?|[+~]\d+|\$|/(?:\\.|[^/])*/)"
    r"(?:\s*,\s*(?:\d+(?:~\d+)?|[+~]\d+|\$|/(?:\\.|[^/])*/))?\s*)?"
    r"!?\s*[eWw](?:\s|$)"
)

# ---------------------------------------------------------------------------
# Allowed commands: common and platform-specific
# Value is a set of permitted subcommands; None = any arguments.
# ---------------------------------------------------------------------------
_COMMON_ALLOWED: dict[str, set[str] | None] = {
    "git": {
        "status", "diff", "rev-parse", "branch", "log",
        "show", "tag", "remote", "stash", "ls-files",
    },
}

_KNOWN_GIT_SUBCOMMANDS: frozenset[str] = frozenset({
    "add", "am", "archive", "bisect", "blame", "branch", "bundle", "checkout",
    "cherry-pick", "clean", "clone", "commit", "config", "describe", "diff",
    "fetch", "format-patch", "gc", "grep", "init", "log", "ls-files", "merge",
    "mv", "notes", "pull", "push", "range-diff", "rebase", "reflog", "remote",
    "reset", "restore", "revert", "rev-parse", "rm", "show", "show-branch",
    "sparse-checkout", "stash", "status", "submodule", "switch", "tag", "worktree",
})

_DEV_COMMON_ALLOWED: dict[str, set[str] | None] = {
    "python": None,
    "python3": None,
    "py": None,
    "pytest": None,
    "pip": {
        "install", "list", "show", "freeze", "check", "search", "uninstall",
    },
    "pip3": {
        "install", "list", "show", "freeze", "check", "search", "uninstall",
    },
    "uv": None,
    "node": None,
    "npm": None,
    "npx": None,
    "pnpm": None,
    "yarn": None,
    "cmake": None,
    "ctest": None,
    "make": None,
    "cargo": None,
    "go": None,
    "java": None,
    "javac": None,
    "dotnet": None,
    "docker": None,
    "rg": None,
    "systemctl": {
        "status",
        "is-active",
        "is-enabled",
        "list-units",
        "list-unit-files",
        "show",
    },
}

_UNIX_ALLOWED: dict[str, set[str] | None] = {
    "ls": None,
    "pwd": None,
    "cat": None,
    "head": None,
    "tail": None,
    "find": None,
    "grep": None,
    "wc": None,
    "echo": None,
    "which": None,
    "file": None,
    "uname": None,
    "diff": None,
    "sort": None,
    "uniq": None,
    "sed": None,
    "awk": None,
    "test": None,
}

_WINDOWS_ALLOWED: dict[str, set[str] | None] = {
    "dir": None,
    "cd": None,
    "type": None,
    "where": None,
    "echo": None,
    "findstr": None,
    "tree": None,
    "ver": None,
    "hostname": None,
    "whoami": None,
}

# Commands where absolute paths are safe in arguments (read-only inspection)
_ABSOLUTE_PATH_SAFE_COMMANDS: frozenset[str] = frozenset({
    "cat", "ls", "grep", "head", "tail", "wc", "file", "find",
    "sed", "awk", "sort", "uniq",
})

_DEV_UNIX_ALLOWED: dict[str, set[str] | None] = {
    "bash": None,
    "sh": None,
    "hostnamectl": None,
    "lscpu": None,
    "free": None,
    "df": None,
    "lsblk": None,
    "lsusb": None,
    "lspci": None,
    "sysctl": None,
    "lshw": None,
}

_DEV_WINDOWS_ALLOWED: dict[str, set[str] | None] = {
    "cmd": None,
    "cmd.exe": None,
    "powershell": None,
    "pwsh": None,
    "where": None,
    "type": None,
    "dir": None,
    "tree": None,
    "hostname": None,
    "whoami": None,
}

# CMD built-in commands (no separate .exe, need cmd /c wrapper)
# "set" intentionally excluded: environment variable leakage is prohibited by prompt (section 22)
_WINDOWS_CMD_BUILTINS: frozenset[str] = frozenset({
    "dir", "cd", "type", "echo", "ver",
})

_WINDOWS_CMD_BUILTIN_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_.:\\\/\- ]+$")
_CMD_DANGEROUS_INLINE_RE: re.Pattern[str] = re.compile(r"(?:&&|\|\||>>|[&|<>;])")
_POWERSHELL_FILE_FLAGS: frozenset[str] = frozenset({"-file", "-f"})
_POWERSHELL_COMMAND_FLAGS: frozenset[str] = frozenset({"-command", "-c"})
_POWERSHELL_SAFE_FLAGS: frozenset[str] = frozenset({"-nologo", "-noninteractive", "-noprofile"})
_POWERSHELL_SAFE_FLAGS_WITH_VALUE: frozenset[str] = frozenset({"-executionpolicy"})
_SCRIPT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "bash": (".sh",),
    "sh": (".sh",),
    "powershell": (".ps1",),
    "pwsh": (".ps1",),
    "cmd": (".bat", ".cmd"),
    "cmd.exe": (".bat", ".cmd"),
}
_TERMINAL_PROFILES: frozenset[str] = frozenset({"restricted", "dev"})

_FALLBACK_READ_ONLY_EXECUTABLES: frozenset[str] = frozenset({
    "ls", "dir", "pwd", "cat", "head", "tail", "echo", "whoami", "which",
    "where", "file", "uname", "hostname", "wc", "grep", "sort", "uniq",
    "diff", "test", "tree", "ver", "findstr", "type", "find",
})

_SAFE_CHILD_ENV_NAMES: frozenset[str] = frozenset({
    "LANG", "LC_ALL", "TERM", "COLORTERM", "CI", "NO_COLOR", "FORCE_COLOR",
})
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:"
    r"API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_?KEY|"
    r"CREDENTIAL[A-Z0-9]*|AUTH(?:ORIZATION)?|COOKIE|SESSION|DSN|"
    r"CONNECTION_STRING"
    r")(?:$|_)",
    re.IGNORECASE,
)
_SECRET_CONNECTION_ENV_RE = re.compile(
    r"(?:^|_)(?:DATABASE|DB|REDIS|MONGODB|MONGO|POSTGRES|MYSQL|AMQP|BROKER)_URL"
    r"(?:$|_)",
    re.IGNORECASE,
)
_CONTROL_ENV_NAMES: frozenset[str] = frozenset({
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT", "NODE_OPTIONS",
    "BASH_ENV", "ENV", "RUBYOPT", "RUBYLIB", "PERL5OPT", "PERL5LIB", "PHP_INI_SCAN_DIR",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
    "SSH_ASKPASS", "SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS", "DOCKER_HOST",
    "PROMPT_COMMAND", "CDPATH", "IFS", "SHELLOPTS", "PS4", "MAKEFLAGS", "MAKEFILES",
    "GCONV_PATH", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS",
    "CLASSPATH", "DOTNET_STARTUP_HOOKS", "PHPRC", "RUSTFLAGS", "ZDOTDIR",
    "LESSOPEN", "LESSCLOSE", "PAGER", "MANPAGER", "EDITOR", "VISUAL", "BROWSER",
    "RIPGREP_CONFIG_PATH", "AWKPATH", "AWKLIBPATH",
    "KUBECONFIG", "NETRC", "PGPASSFILE", "MYSQL_PWD", "MYSQL_HISTFILE",
    "BOTO_CONFIG", "CLOUDSDK_CONFIG", "GNUPGHOME", "PIP_CONFIG_FILE",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "JAVA_HOME", "RUSTUP_HOME", "LOCPATH", "NLSPATH", "TZDIR",
    "GIO_EXTRA_MODULES", "GTK_PATH", "QT_PLUGIN_PATH", "GI_TYPELIB_PATH",
    "COMSPEC", "PATHEXT", "PSMODULEPATH", "SYSTEMROOT", "WINDIR",
    "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "TEMP", "TMP",
})
_CONTROL_ENV_PREFIXES: tuple[str, ...] = (
    "LD_", "DYLD_", "GIT_", "PYTHON", "NODE_", "RUBY", "PERL", "BASH_",
    "SSH_", "DOCKER_", "NPM_CONFIG_", "CARGO_", "RUSTC_",
)
_SECRET_ENV_PREFIXES: tuple[str, ...] = (
    "OPENAI_", "ANTHROPIC_", "AWS_", "AZURE_", "GOOGLE_", "GITHUB_", "GITLAB_",
)

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
    cwd_identity: FileIdentity | None
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

# Per-profile timeout ceiling: restricted keeps the old tight cap, dev
# allows real builds and test runs.
_TIMEOUT_CEILINGS: dict[str, float] = {
    "restricted": 30.0,
    "dev": MAX_SHELL_TIMEOUT_SECONDS,
}
_MAX_COMMAND_ITEMS = 512
_MAX_COMMAND_CHARS = 2000


@dataclass(slots=True)
class ResourceLimits:
    """Unix rlimits applied to child processes via preexec_fn."""

    cpu_seconds: int
    address_space_bytes: int
    nproc: int
    fsize_bytes: int
    nofile: int


_MB = 1024 * 1024

_PROFILE_RESOURCE_DEFAULTS: dict[str, ResourceLimits] = {
    # dev: wide enough for builds and test suites, still bounded.
    "dev": ResourceLimits(
        cpu_seconds=120,
        address_space_bytes=2048 * _MB,
        nproc=4096,
        fsize_bytes=64 * _MB,
        nofile=256,
    ),
    # restricted: the historical tight limits.
    "restricted": ResourceLimits(
        cpu_seconds=8,
        address_space_bytes=256 * _MB,
        nproc=256,
        fsize_bytes=1 * _MB,
        nofile=64,
    ),
}


def resolve_resource_limits(profile: str) -> ResourceLimits:
    """Profile defaults, overridable via AGENT_RLIMIT_* env vars."""
    base = _PROFILE_RESOURCE_DEFAULTS.get(profile, _PROFILE_RESOURCE_DEFAULTS["dev"])
    cpu = read_int_env("AGENT_RLIMIT_CPU", default=base.cpu_seconds, min_value=1, max_value=3600)
    as_mb = read_int_env(
        "AGENT_RLIMIT_AS_MB",
        default=base.address_space_bytes // _MB,
        min_value=64,
        max_value=16384,
    )
    nproc = read_int_env("AGENT_RLIMIT_NPROC", default=base.nproc, min_value=16, max_value=65536)
    return ResourceLimits(
        cpu_seconds=cpu,
        address_space_bytes=as_mb * _MB,
        nproc=nproc,
        fsize_bytes=base.fsize_bytes,
        nofile=base.nofile,
    )

# Sandbox note is printed at most once per process to avoid log spam
# when many TerminalExecutor instances are created (tests, delegation).
_sandbox_note_shown = False


class TerminalExecutor:
    """Cross-platform safe process execution within workspace.

    Capabilities:
    - Command allowlists with platform profiles
    - Argument validation without false positives
    - Blocking dangerous commands and shell operators
    - Working directory strictly within workspace
    - Env overrides for child processes
    - Configurable timeout and stdout/stderr limits
    - Resource limits on Unix (CPU, memory, files)
    - CREATE_NO_WINDOW on Windows
    """

    # Human-in-the-Loop callbacks — set by SafeExecutor at init time.
    _hitl_suspend_fn: Callable[[], None] | None = None
    _hitl_resume_fn: Callable[[], None] | None = None

    @classmethod
    def set_hitl_callbacks(
        cls,
        suspend_fn: Callable[[], None],
        resume_fn: Callable[[], None],
    ) -> None:
        """Register UI pause/resume hooks for HITL prompts."""
        cls._hitl_suspend_fn = suspend_fn
        cls._hitl_resume_fn = resume_fn

    def __init__(
        self,
        workspace: Path,
        timeout: float = 15.0,
        max_output_chars: int = 12000,
        profile: str = "dev",
        extra_allowed: dict[str, set[str] | None] | None = None,
        extra_forbidden: set[str] | None = None,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._approvals = approvals or TerminalApprovalGate()
        # Set by the caller around one command to watch its output live; the
        # captured, bounded result is unaffected.
        self.output_sink: Callable[[str], None] | None = None
        self.workspace = workspace.resolve()
        self.max_output_chars = max(1000, min(max_output_chars, 200_000))
        self._is_windows = os.name == "nt"
        normalized_profile = (profile or "dev").strip().lower()
        if normalized_profile not in _TERMINAL_PROFILES:
            normalized_profile = "dev"
        self.profile = normalized_profile
        self._timeout_ceiling = _TIMEOUT_CEILINGS[self.profile]
        self.timeout = max(1.0, min(timeout, self._timeout_ceiling))
        self._resource_limits = resolve_resource_limits(self.profile)
        self._authorization_lock = threading.Lock()
        self._authorized_context_ids: set[int] = set()
        self._run_lock = threading.RLock()
        self._child_home_manager: tempfile.TemporaryDirectory[str] | None = None
        self._child_home = Path()
        self._rotate_child_home()

        # Project permission policy (.kvist/permissions.json). A broken
        # file yields an empty policy plus a note surfaced by the UI layer.
        self._policy, self._policy_note = load_policy(self.workspace)

        # Build allowlist: common + platform-specific + user-provided
        self._allowed = self._build_allowed_commands()
        if extra_allowed:
            self._allowed.update(extra_allowed)

        # Build forbidden set
        self._forbidden: set[str] = set(_FORBIDDEN_EXECUTABLES)
        if self.profile == "restricted":
            self._forbidden.update(_RESTRICTED_EXTRA_FORBIDDEN)
        if extra_forbidden:
            self._forbidden.update(extra_forbidden)

        # Capture requested/effective sandbox state.  Auto selects bwrap when
        # available; its no-bwrap fallback gets stricter approval semantics.
        global _sandbox_note_shown
        self.sandbox_request = normalize_sandbox_request()
        self.sandbox_mode, self._sandbox_note = detect_sandbox_mode()
        self._bwrap_path = resolve_bubblewrap_path() if self.sandbox_mode is SandboxMode.BUBBLEWRAP else None
        self._sandbox_required_unavailable = (
            self.sandbox_request == "bubblewrap" and self.sandbox_mode is not SandboxMode.BUBBLEWRAP
        )
        self._automatic_restricted_fallback = (
            self.sandbox_request in {"auto", "invalid"}
            and self.sandbox_mode is SandboxMode.WORKSPACE_ONLY
        )
        self._external_tool_paths = _external_tool_paths(self.workspace)
        # Process-tree teardown of detached (setsid) descendants needs either
        # the Bubblewrap PID namespace or a cgroup v2 scope.
        self._cgroup_available = cgroup_supervision_available()
        self._tree_control_available = (
            self.sandbox_mode is SandboxMode.BUBBLEWRAP or self._cgroup_available
        )
        if self._sandbox_note and _sandbox_note_shown:
            self._sandbox_note = ""
        elif self._sandbox_note:
            _sandbox_note_shown = True

    @property
    def allowed_commands(self) -> list[str]:
        """Sorted list of allowed commands."""
        return sorted(self._allowed)

    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
    ) -> TerminalResult:
        """Execute one serialized run with a fresh, private child HOME."""
        with self._run_lock:
            self._rotate_child_home()
            return self._run_once(command, cwd, env_overrides, timeout, role)

    def _run_once(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
    ) -> TerminalResult:
        """Execute through the non-bypassable validation and approval pipeline."""
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise HardDenyError("Hard security denial: command must be a non-empty list of strings")
        if (
            len(command) > _MAX_COMMAND_ITEMS
            or sum(len(item) for item in command) + len(command) - 1
            > _MAX_COMMAND_CHARS
        ):
            raise HardDenyError(
                "Hard security denial: command is too large for safe validation and approval"
            )
        # Own the mutable argv so callers cannot race our checks by retaining
        # and mutating their list from another thread.
        command = list(command)
        role = role if role in {"coder", "devops", "chat"} else "coder"
        self._refresh_policy()

        # Project deny has first precedence and is itself non-approvable.
        decision = self._policy_decision(command)
        if decision == "deny":
            raise HardDenyError(
                "Hard security denial: command blocked by the project permission policy (deny rule)"
            )

        work_dir = self._resolve_cwd(cwd)
        if self._sandbox_required_unavailable:
            raise HardDenyError(
                "Hard security denial: mandatory Bubblewrap sandbox is unavailable"
            )
        environment = self._build_env(env_overrides)
        bindings = self._resolve_executable_bindings(command, work_dir, environment)
        if (
            self._automatic_restricted_fallback
            and not self._cgroup_available
            and self._command_needs_arbitrary_code(command, bindings)
        ):
            raise HardDenyError(
                "Hard security denial: automatic fallback cannot control detached "
                "descendants without a Bubblewrap PID namespace or cgroup v2 scope; "
                "arbitrary-code execution is refused"
            )
        self._validate_executable_boundaries(command, work_dir, bindings)
        self._hard_validate(command, work_dir, bindings)
        initial_context = self._build_execution_context(
            command,
            work_dir,
            role,
            environment,
            bindings,
            sandbox_mode=self.sandbox_mode,
            network_allowed=sandbox_network_allowed(),
            bwrap_path=self._bwrap_path,
        )
        assessment = self._assess_risk(command, role)

        allowlist_error: CommandNotAllowedError | None = None
        try:
            self._validate_allowlist(command, bindings)
        except CommandNotAllowedError as error:
            allowlist_error = error

        fallback_approval = self._fallback_requires_approval(command, bindings)
        origin_approval = self._requires_executable_origin_approval(command, bindings)
        authorization_token = object()

        def execute_authorized(expected_context: ExecutionContext) -> TerminalResult:
            """Mint a one-shot spawn capability inside this validated run only."""
            token_id = id(authorization_token)
            with self._authorization_lock:
                self._authorized_context_ids.add(token_id)
            try:
                return self._execute_validated(
                    command,
                    cwd,
                    env_overrides,
                    timeout,
                    role,
                    expected_context=expected_context,
                    authorization_token=authorization_token,
                )
            finally:
                with self._authorization_lock:
                    self._authorized_context_ids.discard(token_id)

        # Intrinsic DANGEROUS always wins over ask/allow and is never
        # persistable as a repository allow rule.
        if assessment.risk is RiskLevel.DANGEROUS:
            return self._approve_or_deny(
                command,
                f"Risk assessment: {assessment.reason}",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if decision == "ask":
            return self._approve_or_deny(
                command,
                "Project permission policy requires confirmation (ask rule)",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if fallback_approval:
            return self._approve_or_deny(
                command,
                "Automatic sandbox fallback (no Bubblewrap: no secret masking or "
                ".kvist isolation) requires per-execution approval for arbitrary-code "
                "commands",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if origin_approval:
            return self._approve_or_deny(
                command,
                "Executable origin is not eligible for trusted-name or project-allow suppression",
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        # A project allow may suppress approval only after hard validation and
        # only for non-dangerous, non-fallback execution.
        if decision == "allow":
            return execute_authorized(initial_context)

        if allowlist_error is not None:
            return self._approve_or_deny(
                command,
                str(allowlist_error),
                lambda: execute_authorized(initial_context),
                offer_rule=False,
            )

        if assessment.requires_approval:
            return self._approve_or_deny(
                command,
                f"Risk assessment: {assessment.reason}",
                lambda: execute_authorized(initial_context),
                offer_rule=assessment.risk is RiskLevel.MODERATE,
            )

        return execute_authorized(initial_context)

    def _approve_or_deny(
        self,
        command: list[str],
        reason: str,
        execute_authorized: Callable[[], TerminalResult],
        offer_rule: bool,
    ) -> TerminalResult:
        """HITL gate: run on approval, denied TerminalResult otherwise.

        ``offer_rule`` — after an approval of a risk-graded command, offer
        to persist a project allow-rule. Never offered for hard validation
        failures: those are non-interactive hard denials.
        """
        if not self._request_user_approval(command, reason):
            return TerminalResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=(
                    "Error: Command execution denied by user for security reasons. "
                    f"{reason}. Find an alternative way to solve the task without this command."
                ),
                denied=True,
            )
        result = execute_authorized()
        if offer_rule:
            self._offer_allow_rule(command)
        return result

    def _command_needs_arbitrary_code(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...] | None = None
    ) -> bool:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index] if bindings is not None else None
            if is_exact_version_probe(segment) and (
                binding is None or binding.trusted_system
            ):
                continue
            if canonical_executable_name(segment[0]) not in _FALLBACK_READ_ONLY_EXECUTABLES:
                return True
        return False

    def _fallback_requires_approval(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...] | None = None
    ) -> bool:
        if not self._automatic_restricted_fallback:
            return False
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index] if bindings is not None else None
            if is_exact_version_probe(segment) and (
                binding is None or binding.trusted_system
            ):
                continue
            if canonical_executable_name(segment[0]) not in _FALLBACK_READ_ONLY_EXECUTABLES:
                return True
        return False

    def _policy_decision(self, command: list[str]) -> str | None:
        """Worst-per-segment policy decision: deny > ask > allow.

        "allow" requires EVERY pipe segment to match an allow-rule; one
        deny/ask segment decides for the whole pipeline. A malformed pipe
        is left for _validate to report.
        """
        if not command:
            return None
        try:
            segments = self._split_pipe_segments(command)
        except TerminalError:
            return None
        decisions: list[str | None] = []
        for segment in segments:
            normalized = [canonical_executable_name(segment[0]), *segment[1:]]
            candidates = {self._policy.decide(segment), self._policy.decide(normalized)}
            if "deny" in candidates:
                decisions.append("deny")
            elif "ask" in candidates:
                decisions.append("ask")
            elif "allow" in candidates:
                decisions.append("allow")
            else:
                decisions.append(None)
        if "deny" in decisions:
            return "deny"
        if "ask" in decisions:
            return "ask"
        if all(item == "allow" for item in decisions):
            return "allow"
        return None

    def _refresh_policy(self) -> None:
        policy, note = load_policy(self.workspace)
        self._policy = policy
        self._policy_note = note

    def _offer_allow_rule(self, command: list[str]) -> None:
        """After an interactive approval, offer "always allow in this repo"."""
        if not sys.stdin.isatty() or "|" in command:
            return
        pattern = _suggest_allow_pattern(command)
        try:
            accepted = self._approvals.confirm(
                title="Permission policy",
                body=(
                    f"Save allow rule {pattern} to .kvist/permissions.json?\n"
                    "Matching commands will run without approval in this repo."
                ),
                question=f"Always allow '{pattern}' in this repo?",
            )
        except TerminalError:
            return
        if not accepted:
            return
        try:
            append_allow_rule(self.workspace, pattern)
        except (OSError, ValueError) as error:
            print(f"Failed to save allow rule: {error}", file=sys.stderr)
            return
        self._policy, self._policy_note = load_policy(self.workspace)

    def _assess_risk(self, command: list[str], role: str) -> CommandAssessment:
        """Risk-grade a validated command via command_policy.

        Piped commands are assessed per segment so `safe | dangerous` cannot
        hide behind the first executable; the riskiest segment wins.
        """
        worst: CommandAssessment | None = None
        requires_approval = False
        for segment in self._split_pipe_segments(command):
            candidate = assess_command(segment, role=role)
            requires_approval = requires_approval or candidate.requires_approval
            if worst is None or _risk_rank(candidate.risk) > _risk_rank(worst.risk):
                worst = candidate
        assert worst is not None  # _split_pipe_segments rejects empty commands
        if requires_approval and not worst.requires_approval:
            worst = CommandAssessment(
                command=command,
                risk=worst.risk,
                reason=worst.reason,
                requires_approval=True,
            )
        return worst

    def _execute_validated(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
        role: str = "coder",
        *,
        expected_context: ExecutionContext,
        authorization_token: object,
    ) -> TerminalResult:
        """Repeat hard validation/context construction immediately before spawn."""
        token_id = id(authorization_token)
        with self._authorization_lock:
            if token_id not in self._authorized_context_ids:
                raise HardDenyError(
                    "Hard security denial: final execution lacks a one-shot authorization capability"
                )
            self._authorized_context_ids.remove(token_id)
        command_snapshot = list(command)
        sandbox_mode = self.sandbox_mode
        network_allowed = sandbox_network_allowed()
        bwrap_path = self._bwrap_path
        workspace = self.workspace
        self._refresh_policy()
        if self._policy_decision(command_snapshot) == "deny":
            raise HardDenyError(
                "Hard security denial: command became blocked by a project deny rule before execution"
            )
        work_dir = self._resolve_cwd(cwd)
        effective_timeout = self._clamp_timeout(timeout)
        env = self._build_env(env_overrides)
        bindings = self._resolve_executable_bindings(command_snapshot, work_dir, env)
        self._validate_executable_boundaries(command_snapshot, work_dir, bindings)
        self._hard_validate(command_snapshot, work_dir, bindings)
        context = self._build_execution_context(
            command_snapshot,
            work_dir,
            role,
            env,
            bindings,
            sandbox_mode=sandbox_mode,
            network_allowed=network_allowed,
            bwrap_path=bwrap_path,
        )
        if context != expected_context:
            raise HardDenyError(
                "Hard security denial: approved execution context changed before process creation"
            )
        missing = next(
            (binding.token for binding in bindings[: len(self._split_pipe_segments(command_snapshot))] if not binding.resolved_path),
            None,
        )
        if missing is not None:
            raise TerminalError(f"Executable not found before process creation: {missing}")

        actual_command = self._materialize_executables(command_snapshot, bindings)
        actual_command = self._wrap_for_platform(actual_command, bindings)

        # Auto-detect pipe usage: if command contains "|", run via sh -c.
        # Each non-pipe token is shell-quoted so arguments with spaces or
        # metacharacters cannot be reinterpreted by sh.
        if "|" in actual_command:
            joined = " ".join(
                token if token == "|" else shlex.quote(token)
                for token in actual_command
            )
            shell_binding = bindings[-1]
            if not shell_binding.resolved_path or not shell_binding.trusted_system:
                raise HardDenyError("Hard security denial: trusted pipeline shell is unavailable")
            actual_command = [shell_binding.resolved_path, "-c", joined]

        try:
            actual_command = wrap_command_for_sandbox(
                actual_command,
                sandbox_mode,
                workspace,
                work_dir,
                bwrap_path=bwrap_path,
                network_allowed=network_allowed,
                tool_paths=self._external_tool_paths,
            )
        except ValueError as error:
            raise HardDenyError(f"Hard security denial: {error}") from error

        rlimit_setter = (
            None
            if self._is_windows
            else functools.partial(_unix_resource_limits, self._resource_limits)
        )
        try:
            outcome = run_supervised(
                actual_command,
                cwd=work_dir,
                env=env,
                timeout=effective_timeout,
                max_capture_bytes=self.max_output_chars * 4,
                rlimit_setter=rlimit_setter,
                is_windows=self._is_windows,
                use_cgroup=self._cgroup_available,
                on_output=self.output_sink,
            )
        except FileNotFoundError:
            raise TerminalError(f"Executable not found: {command_snapshot[0]}")
        except ProcessSupervisionError as exc:
            raise TerminalError(f"Process supervision error: {exc}") from exc
        except OSError as exc:
            raise TerminalError(f"Process launch error: {exc}")

        stdout = self._bound_output(outcome.stdout, outcome.stdout_truncated)
        stderr = self._bound_output(outcome.stderr, outcome.stderr_truncated)
        if outcome.timed_out:
            stderr = (
                f"Timeout: command did not complete within {effective_timeout} sec\n"
                + stderr
            )
        return TerminalResult(
            command=command_snapshot,
            returncode=outcome.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=outcome.timed_out,
        )

    def _request_user_approval(self, command: list[str], reason: str) -> bool:
        """Dangerous-command HITL gate, delegated to the injected approval gate.

        Kept as a method with this exact ``(command, reason)`` signature so
        the call site (``self._request_user_approval(command, reason)``) and
        the test patch points remain stable. The default gate reproduces the
        historical terminal prompt and stays fail-closed off a TTY.
        """
        return self._approvals.confirm_command(command, reason)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, command: list[str], cwd: str | None = None) -> None:
        """Backward-compatible full validator used by tests and callers."""
        work_dir = self._resolve_cwd(cwd)
        self._hard_validate(command, work_dir)
        self._validate_allowlist(command)

    def _hard_validate(
        self,
        command: list[str],
        work_dir: Path,
        bindings: tuple[ExecutableBinding, ...] | None = None,
    ) -> None:
        """Authoritative non-overridable validation for every pipe segment."""
        if not command:
            raise HardDenyError("Hard security denial: Empty command")
        if not command[0].strip():
            raise HardDenyError("Hard security denial: executable token is empty")
        if any("\x00" in token for token in command):
            raise HardDenyError("Hard security denial: NUL bytes are forbidden in command arguments")

        if (
            len(command) > _MAX_COMMAND_ITEMS
            or sum(len(item) for item in command) + len(command) - 1
            > _MAX_COMMAND_CHARS
        ):
            raise HardDenyError(
                "Hard security denial: command is too large for safe validation and approval"
            )

        for index, segment in enumerate(self._split_pipe_segments(command)):
            trusted_probe = bindings is None or bindings[index].trusted_system
            self._hard_validate_single(segment, work_dir, trusted_probe=trusted_probe)

    def _validate_allowlist(
        self,
        command: list[str],
        bindings: tuple[ExecutableBinding, ...] | None = None,
    ) -> None:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            trusted_probe = bindings is None or bindings[index].trusted_system
            if is_exact_version_probe(segment) and trusted_probe:
                continue
            executable = canonical_executable_name(segment[0])
            self._require_allowlisted(segment, executable)

    def _split_pipe_segments(self, command: list[str]) -> list[list[str]]:
        segments: list[list[str]] = [[]]
        for token in command:
            if token == "|":
                segments.append([])
            else:
                segments[-1].append(token)
        if any(not segment for segment in segments):
            raise HardDenyError("Hard security denial: Empty command segment around '|' is forbidden")
        return segments

    def _hard_validate_single(
        self, command: list[str], work_dir: Path, *, trusted_probe: bool = True
    ) -> None:
        if not command[0].strip():
            raise HardDenyError("Hard security denial: executable token is empty")
        executable = canonical_executable_name(command[0])

        self._validate_common_tokens(
            [command[0]], executable, allow_executable_path=True, work_dir=work_dir
        )

        if executable in self._forbidden and not (
            is_exact_version_probe(command) and trusted_probe
        ):
            raise HardDenyError(
                f"Hard security denial: command '{command[0]}' is unconditionally forbidden"
            )

        if executable in {"bash", "sh", "powershell", "pwsh", "cmd", "cmd.exe"}:
            self._validate_shell_command(command, executable, work_dir)
            return

        if executable == "find":
            self._validate_find_arguments(command[1:])
        if executable == "sort" and _sort_has_output_or_program_option(command[1:]):
            raise HardDenyError("Hard security denial: sort output/external-program modes are forbidden")
        if executable == "tree" and _tree_has_output_option(command[1:]):
            raise HardDenyError("Hard security denial: tree output-file mode is forbidden")
        if executable == "hostname" and not _hostname_is_read_only(command[1:]):
            raise HardDenyError("Hard security denial: hostname mutation is forbidden")
        if executable == "diff" and _diff_has_output_option(command[1:]):
            raise HardDenyError("Hard security denial: diff output-file mode is forbidden")
        if executable == "uniq" and _uniq_has_output_operand(command[1:]):
            raise HardDenyError("Hard security denial: uniq output-file operand is forbidden")
        if executable == "file" and _file_has_compile_option(command[1:]):
            raise HardDenyError("Hard security denial: file magic compilation mode is forbidden")
        if executable == "wc" and any(
            token == "--files0-from" or token.startswith("--files0-from=")
            for token in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: wc indirect file-list mode is forbidden"
            )
        if executable == "git":
            self._validate_git_arguments(command, work_dir)
        self._validate_common_tokens(command[1:], executable, work_dir=work_dir)
        if executable == "grep":
            self._validate_grep_arguments(command[1:])
        if executable == "rg":
            self._validate_rg_arguments(command, work_dir)
        if executable == "make":
            self._validate_make_argument_clusters(command[1:])
        self._validate_interpreter_modes(command, executable, work_dir)

    def _validate_git_arguments(self, command: list[str], work_dir: Path) -> None:
        """Reject Git syntax that can hide subcommands or launch configured helpers."""
        if is_exact_version_probe(command):
            return
        # Kvist's allowlist, risk policy, and deterministic hardening all bind
        # to direct ``git SUBCOMMAND`` syntax. Ambiguous/global-option forms
        # fail closed; callers can select a repository with ``cwd`` instead.
        if len(command) > 1 and command[1].startswith("-"):
            raise HardDenyError(
                "Hard security denial: Git global options before the subcommand are forbidden"
            )
        if len(command) > 1 and command[1].casefold() not in _KNOWN_GIT_SUBCOMMANDS:
            raise HardDenyError(
                "Hard security denial: unknown Git subcommands/aliases are forbidden"
            )
        if (
            len(command) > 1
            and command[1].casefold() == "tag"
            and _git_tag_has_signing_or_verification_option(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: Git tag signing/verification helpers are forbidden"
            )
        if (
            len(command) > 1
            and command[1].casefold() in {"branch", "tag"}
            and _git_ref_filter_has_signature_atom(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: Git ref signature formatting is forbidden"
            )
        git_dir = self._git_metadata_directory(work_dir)
        subcommand = command[1].casefold() if len(command) > 1 else ""
        self._deny_git_repository_helpers(git_dir, subcommand)
        read_args = _git_read_operation_arguments(command)
        if len(command) > 1 and command[1].casefold() in {"status", "diff"}:
            self._deny_git_worktree_filters(git_dir)
        if (
            len(command) > 1
            and command[1].casefold() == "status"
            and _git_has_unsafe_submodule_option(command[2:])
        ):
            raise HardDenyError(
                "Hard security denial: recursive Git submodule inspection is forbidden"
            )
        if read_args is None:
            return
        if _has_long_output_option(read_args):
            raise HardDenyError("Hard security denial: Git output-file mode is forbidden")
        if _git_has_external_helper_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git external diff/textconv execution is forbidden"
            )
        if _git_has_remerge_diff_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git remerge diffs may execute custom merge drivers"
            )
        if _git_has_unsafe_submodule_option(read_args):
            raise HardDenyError(
                "Hard security denial: recursive Git submodule inspection is forbidden"
            )
        if _git_has_signature_verification_option(read_args):
            raise HardDenyError(
                "Hard security denial: Git signature-verifier execution is forbidden"
            )

    def _git_metadata_directory(self, work_dir: Path) -> Path | None:
        """Locate repository metadata without allowing a gitdir workspace escape."""
        current = work_dir
        while _is_relative_to(current, self.workspace):
            marker = current / ".git"
            if marker.is_dir():
                resolved = marker.resolve(strict=True)
                if not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: Git metadata resolves outside the workspace"
                    )
                return resolved
            if marker.is_file():
                try:
                    pointer = marker.resolve(strict=True)
                    if not _is_relative_to(pointer, self.workspace):
                        raise HardDenyError(
                            "Hard security denial: Git metadata pointer resolves outside the workspace"
                        )
                    content = pointer.read_text(encoding="utf-8", errors="strict")
                except HardDenyError:
                    raise
                except (OSError, UnicodeError) as error:
                    raise HardDenyError(
                        f"Hard security denial: cannot inspect Git metadata pointer: {error}"
                    ) from error
                prefix, separator, value = content.strip().partition(":")
                if prefix.casefold() != "gitdir" or not separator or not value.strip():
                    raise HardDenyError("Hard security denial: malformed .git metadata pointer")
                resolved = (current / value.strip()).resolve(strict=True)
                if not resolved.is_dir() or not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: Git metadata resolves outside the workspace"
                    )
                return resolved
            if current == self.workspace or current.parent == current:
                break
            current = current.parent
        if (work_dir / "HEAD").is_file() and (work_dir / "config").is_file():
            return work_dir
        return None

    def _deny_git_worktree_filters(self, git_dir: Path | None) -> None:
        """SAFE Git worktree reads may not invoke clean/process filter commands."""
        if git_dir is None:
            return
        for config in self._git_configuration_paths(git_dir):
            content = self._read_git_configuration(config)
            if re.search(r"(?im)^\s*\[\s*(?:filter|include|includeIf)\b", content):
                raise HardDenyError(
                    "Hard security denial: Git worktree filters/includes may execute helpers"
                )

    def _deny_git_repository_helpers(
        self, git_dir: Path | None, subcommand: str
    ) -> None:
        """Reject effective repository config that can launch hidden helpers."""
        if git_dir is None:
            return
        for config in self._git_configuration_paths(git_dir):
            content = self._read_git_configuration(config)
            if re.search(r"(?im)^\s*\[\s*include(?:If)?\b", content):
                raise HardDenyError(
                    "Hard security denial: Git configuration includes may import executable settings"
                )
            alternate_commands = _git_config_section_values(
                content, "core", "alternateRefsCommand"
            )
            if alternate_commands or re.search(
                r"(?im)^\s*core\.alternateRefsCommand\s*=", content
            ):
                raise HardDenyError(
                    "Hard security denial: Git alternate-refs commands are forbidden"
                )
            if subcommand in {"branch", "tag"}:
                for value in _git_config_section_values(content, subcommand, "sort"):
                    normalized = value.casefold().lstrip("-")
                    if normalized.startswith("signature:") or re.search(
                        r"%\(\*?signature", normalized
                    ):
                        raise HardDenyError(
                            "Hard security denial: Git ref signature sorting is forbidden"
                        )

    def _read_git_configuration(self, config: Path) -> str:
        try:
            resolved = config.resolve(strict=True)
            if not _is_relative_to(resolved, self.workspace):
                raise HardDenyError(
                    "Hard security denial: Git configuration resolves outside the workspace"
                )
            if resolved.stat().st_size > 1024 * 1024:
                raise HardDenyError("Hard security denial: Git configuration is too large")
            return resolved.read_text(encoding="utf-8", errors="strict")
        except HardDenyError:
            raise
        except (OSError, UnicodeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect Git configuration: {error}"
            ) from error

    def _git_common_directory(self, git_dir: Path) -> Path:
        """Resolve a linked worktree's common metadata directory fail-closed."""
        marker = git_dir / "commondir"
        if not marker.exists() and not marker.is_symlink():
            return git_dir
        try:
            resolved_marker = marker.resolve(strict=True)
            if not _is_relative_to(resolved_marker, self.workspace):
                raise HardDenyError(
                    "Hard security denial: Git commondir pointer resolves outside the workspace"
                )
            if resolved_marker.stat().st_size > 4096:
                raise HardDenyError("Hard security denial: Git commondir pointer is too large")
            value = resolved_marker.read_text(encoding="utf-8", errors="strict").strip()
            if not value or "\n" in value or "\r" in value or "\x00" in value:
                raise HardDenyError("Hard security denial: malformed Git commondir pointer")
            common_dir = (git_dir / value).resolve(strict=True)
        except HardDenyError:
            raise
        except (OSError, UnicodeError, RuntimeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect Git commondir pointer: {error}"
            ) from error
        if not common_dir.is_dir() or not _is_relative_to(common_dir, self.workspace):
            raise HardDenyError(
                "Hard security denial: Git common metadata resolves outside the workspace"
            )
        return common_dir

    def _git_configuration_paths(self, git_dir: Path) -> list[Path]:
        """Return existing common and per-worktree Git configuration files."""
        common_dir = self._git_common_directory(git_dir)
        candidates = [common_dir / "config", git_dir / "config", git_dir / "config.worktree"]
        paths: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            key = str(candidate)
            if key not in seen:
                seen.add(key)
                paths.append(candidate)
        return paths

    def _require_allowlisted(self, command: list[str], executable: str) -> None:
        if executable not in self._allowed:
            raise CommandNotAllowedError(
                f"Command '{command[0]}' is not allowed. "
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

        # Subcommand validation (for git and similar)
        allowed_sub = self._allowed.get(executable)
        if allowed_sub is not None and len(command) > 1:
            sub = command[1].lstrip("-").casefold()
            if sub and sub not in allowed_sub:
                raise CommandNotAllowedError(
                    f"Subcommand '{command[1]}' is not allowed for '{command[0]}'"
                )

    def _validate_find_arguments(self, args: list[str]) -> None:
        for token in args:
            if is_find_action_token(token):
                raise HardDenyError(
                    f"Hard security denial: find action predicate '{token}' is forbidden: "
                    "it deletes files or executes commands"
                )

    def _validate_make_argument_clusters(self, args: list[str]) -> None:
        """Reject Make syntax that can hide execution inputs or inline code."""
        options_with_values = {"C", "E", "f", "I", "j", "l", "o", "W"}
        canonical_long_inputs = {"--directory", "--file", "--makefile"}
        assignment_operators = (":::=", "::=", ":=", "+=", "?=", "!=", "=")
        makefile_options = {"-f", "--file", "--makefile"}
        for index, token in enumerate(args):
            flag = token.partition("=")[0]
            stripped = token.lstrip()
            makefiles_suffix = (
                stripped[len("MAKEFILES") :].lstrip()
                if stripped.startswith("MAKEFILES")
                else ""
            )
            if makefiles_suffix and makefiles_suffix.startswith(assignment_operators):
                raise HardDenyError(
                    "Hard security denial: command-line MAKEFILES inputs are forbidden"
                )
            if (
                token == "-f-"
                or token in {"--file=-", "--makefile=-"}
                or (
                    token in makefile_options
                    and index + 1 < len(args)
                    and args[index + 1] == "-"
                )
            ):
                raise HardDenyError(
                    "Hard security denial: Makefile input from stdin is forbidden"
                )
            if flag != "--" and flag.startswith("--"):
                if "--temp-stdin".startswith(flag):
                    raise HardDenyError(
                        "Hard security denial: Make temporary-stdin input is forbidden"
                    )
                if "--eval".startswith(flag):
                    raise HardDenyError(
                        "Hard security denial: Make inline eval is forbidden"
                    )
                if any(
                    option.startswith(flag) and flag != option
                    for option in canonical_long_inputs
                ):
                    raise HardDenyError(
                        f"Hard security denial: abbreviated Make input option '{flag}' is forbidden"
                    )
                continue
            if token == "-E":
                raise HardDenyError(
                    "Hard security denial: Make inline eval is forbidden"
                )
            if not token.startswith("-") or token.startswith("--") or len(token) <= 2:
                continue
            for position, option in enumerate(token[1:]):
                if option == "E":
                    raise HardDenyError(
                        "Hard security denial: Make inline eval is forbidden"
                    )
                if option not in options_with_values:
                    continue
                if position > 0:
                    raise HardDenyError(
                        f"Hard security denial: ambiguous clustered Make option '{token}' is forbidden"
                    )
                # The remainder is this option's value, not more flags.
                break

    def _validate_grep_arguments(self, args: list[str]) -> None:
        index = 0
        while index < len(args):
            token = args[index]
            lowered = token.casefold()
            if lowered in {"-r", "--recursive", "--dereference-recursive"}:
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if token.startswith("-") and not token.startswith("--") and any(
                flag in token[1:] for flag in ("r", "R")
            ):
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if lowered.startswith("--directories=") and lowered.partition("=")[2] == "recurse":
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            if lowered == "-d" and index + 1 < len(args):
                if args[index + 1].casefold() == "recurse":
                    raise HardDenyError("Hard security denial: recursive grep is forbidden")
                index += 2
                continue
            if lowered.startswith("-d") and lowered[2:] == "recurse":
                raise HardDenyError("Hard security denial: recursive grep is forbidden")
            index += 1

    def _validate_rg_arguments(self, command: list[str], work_dir: Path) -> None:
        """Allow ripgrep only over explicit regular files, never directory trees."""
        safe_flags = {
            "-n", "--line-number", "-N", "--no-line-number",
            "-i", "--ignore-case", "-s", "--case-sensitive", "-S", "--smart-case",
            "-F", "--fixed-strings", "-w", "--word-regexp", "-x", "--line-regexp",
            "-v", "--invert-match", "-q", "--quiet", "-c", "--count",
            "--count-matches", "--no-messages", "--json", "--pcre2",
        }
        positionals: list[str] = []
        after_separator = False
        for token in command[1:]:
            if token == "--" and not after_separator:
                after_separator = True
                continue
            if not after_separator and token in safe_flags:
                continue
            if not after_separator and token.startswith("-"):
                raise HardDenyError(
                    f"Hard security denial: unsupported ripgrep option '{token}' could enable recursive access"
                )
            positionals.append(token)
        if len(positionals) < 2:
            raise HardDenyError(
                "Hard security denial: ripgrep requires explicit regular-file operands"
            )
        for token in positionals[1:]:
            path = self._resolve_workspace_reference(token, work_dir, "ripgrep input")
            if not path.is_file():
                raise HardDenyError(
                    "Hard security denial: recursive ripgrep directory traversal is forbidden"
                )

    def _validate_shell_command(self, command: list[str], executable: str, work_dir: Path) -> None:
        if executable in {"bash", "sh"}:
            if len(command) != 2:
                raise HardDenyError(
                    f"Hard security denial: for '{command[0]}' only a workspace script file is allowed"
                )
            self._validate_script_argument(command[1], executable, work_dir)
            return

        if executable in {"powershell", "pwsh"}:
            if len(command) < 3:
                raise HardDenyError(f"Hard security denial: for '{command[0]}' only -File is allowed")

            script_index: int | None = None
            index = 1
            while index < len(command):
                token = command[index]
                lowered = token.lower()
                if lowered in _POWERSHELL_COMMAND_FLAGS:
                    raise HardDenyError(f"Hard security denial: inline mode '{token}' is forbidden")
                if lowered in _POWERSHELL_FILE_FLAGS:
                    script_index = index + 1
                    break
                if lowered in _POWERSHELL_SAFE_FLAGS:
                    index += 1
                    continue
                if lowered in _POWERSHELL_SAFE_FLAGS_WITH_VALUE:
                    if index + 1 >= len(command):
                        raise HardDenyError(f"Hard security denial: flag '{token}' requires a value")
                    self._validate_common_tokens([command[index + 1]], work_dir=work_dir)
                    index += 2
                    continue
                raise HardDenyError(f"Hard security denial: for '{command[0]}' only -File is allowed")

            if script_index is None or script_index >= len(command):
                raise HardDenyError(f"Hard security denial: no script follows -File for '{command[0]}'")

            if len(command) != script_index + 1:
                raise HardDenyError(f"Hard security denial: only one PowerShell script is allowed")

            script = command[script_index]
            self._validate_common_tokens(command[1:script_index], work_dir=work_dir)
            self._validate_script_argument(script, executable, work_dir)
            return

        self._validate_windows_cmd_command(command, work_dir)

    def _validate_windows_cmd_command(self, command: list[str], work_dir: Path) -> None:
        if len(command) < 3 or command[1].lower() != "/c":
            raise HardDenyError(f"Hard security denial: for '{command[0]}' only /c is allowed")

        if len(command) != 3:
            raise HardDenyError(f"Hard security denial: only one safe argument is allowed after /c")

        payload = command[2].strip()
        if not payload:
            raise HardDenyError("Hard security denial: empty string after /c is forbidden")
        if _CMD_DANGEROUS_INLINE_RE.search(payload):
            raise HardDenyError("Hard security denial: dangerous shell operators in cmd /c")
        if _SUBST_RE.search(payload):
            raise HardDenyError("Hard security denial: command substitution in cmd /c")

        if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS["cmd"]):
            self._validate_script_argument(payload, "cmd", work_dir)
            return

        builtin, separator, remainder = payload.partition(" ")
        if builtin.lower() not in _WINDOWS_CMD_BUILTINS:
            raise HardDenyError(f"Hard security denial: unsupported cmd /c payload")
        if not separator:
            return

        remainder_tokens = [token for token in remainder.split() if token]
        if not remainder_tokens:
            return

        if not all(_WINDOWS_CMD_BUILTIN_RE.fullmatch(token) for token in remainder_tokens):
            raise HardDenyError("Hard security denial: CMD builtin arguments contain invalid characters")

        self._validate_common_tokens(remainder_tokens, work_dir=work_dir)

    def _validate_common_tokens(
        self,
        tokens: list[str],
        executable: str = "",
        *,
        allow_executable_path: bool = False,
        work_dir: Path | None = None,
    ) -> None:
        joined_tokens = " ".join(tokens)
        if redact_text(joined_tokens) != joined_tokens:
            raise HardDenyError(
                "Hard security denial: credential-bearing command arguments are forbidden"
            )
        for token_index, token in enumerate(tokens):
            if "\x00" in token:
                raise HardDenyError(
                    "Hard security denial: command arguments cannot contain NUL"
                )
            # Exact match only with shell operator (not substring!)
            if token in _FORBIDDEN_OPERATORS:
                raise HardDenyError(f"Hard security denial: shell operator '{token}' is forbidden")

            # Detect command substitution inside arguments: $(…), `…`, ${…}
            if _SUBST_RE.search(token):
                raise HardDenyError(
                    "Hard security denial: Command substitution ($(), ``, ${}) is forbidden"
                )

            # Secret paths are forbidden for every command, including
            # read-only ones, on both relative and absolute paths.
            self._check_secret_token(token)
            if _INTERNAL_TOKEN_RE.search(token.replace("\\", "/")):
                raise HardDenyError("Hard security denial: access to internal .kvist paths is forbidden")

            # Reapply path-boundary checks to attached option/response values;
            # ``--config=/host`` must not hide a path that ``/host`` would
            # have rejected as a standalone token.
            docker_host_path = (
                executable.casefold() == "docker"
                and _docker_path_is_risk_graded(tokens, token_index)
            )
            for fragment in _argument_path_fragments(token):
                if (
                    self._is_absolute_path(fragment)
                    and not allow_executable_path
                    and executable.casefold() not in _ABSOLUTE_PATH_SAFE_COMMANDS
                    and not docker_host_path
                ):
                    raise HardDenyError(
                        "Hard security denial: Absolute paths are forbidden in arguments"
                    )
                if fragment.startswith("\\\\") and not docker_host_path:
                    raise HardDenyError("Hard security denial: UNC paths are forbidden in arguments")
                segments = [s for s in fragment.replace("\\", "/").split("/") if s]
                if ".." in segments and not docker_host_path:
                    raise HardDenyError("Hard security denial: Path traversal (..) is forbidden")

            if work_dir is not None and not allow_executable_path:
                self._check_existing_sensitive_reference(token, work_dir)

    def _check_secret_token(self, token: str) -> None:
        if _SECRET_TOKEN_RE.search(token.replace("\\", "/")):
            raise HardDenyError("Hard security denial: access to secret files is forbidden")

    def _check_existing_sensitive_reference(self, token: str, work_dir: Path) -> None:
        """Re-check canonical targets for direct, attached, and response paths.

        Relative arguments are workspace references even for otherwise safe
        read-only tools.  Resolve them before execution so a symlink cannot
        turn ``cat local-name`` (or an attached option value) into an
        out-of-workspace read.  Resolution errors for relative references are
        security relevant and therefore fail closed.

        Explicit absolute paths retain their separately risk-graded behaviour
        for the small read-only command allowlist.
        """
        for fragment in _argument_path_fragments(token):
            if not fragment or "\x00" in fragment:
                continue
            try:
                raw = Path(fragment).expanduser()
            except (OSError, RuntimeError, ValueError) as error:
                raise HardDenyError(
                    "Hard security denial: cannot resolve command argument"
                ) from error
            is_explicit_absolute = raw.is_absolute()
            candidate = raw if is_explicit_absolute else work_dir / raw

            if not is_explicit_absolute:
                try:
                    resolved = candidate.resolve(strict=False)
                except (OSError, RuntimeError, ValueError) as error:
                    raise HardDenyError(
                        "Hard security denial: cannot resolve relative command "
                        "argument or symlink"
                    ) from error
                if not _is_relative_to(resolved, self.workspace):
                    raise HardDenyError(
                        "Hard security denial: command argument resolves "
                        "outside the workspace"
                    )

            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                canonical = candidate.resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as error:
                if not is_explicit_absolute:
                    raise HardDenyError(
                        "Hard security denial: cannot resolve relative command "
                        "argument or symlink"
                    ) from error
                continue
            if not is_explicit_absolute and not _is_relative_to(
                canonical,
                self.workspace,
            ):
                raise HardDenyError(
                    "Hard security denial: command argument resolves outside "
                    "the workspace"
                )
            if not is_explicit_absolute:
                self._check_workspace_regular_file_hardlinks(
                    canonical,
                    "command argument",
                )
            self._check_resolved_sensitive_path(canonical, "command argument")

    def _check_workspace_regular_file_hardlinks(
        self,
        candidate: Path,
        kind: str,
    ) -> None:
        try:
            info = candidate.stat()
        except OSError as error:
            raise HardDenyError(
                f"Hard security denial: cannot inspect {kind} identity"
            ) from error
        self._check_inode_hardlink_containment(info, kind)

    def _check_inode_hardlink_containment(
        self,
        info: os.stat_result,
        kind: str,
    ) -> None:
        """Reject an inode whose extra names may live outside the workspace.

        A link count above one is only dangerous when some of those names are
        unreachable from the workspace: that is how an external inode is
        imported under a workspace pathname.  Aliases that are all inside the
        workspace import nothing; secret aliases among them are neutralised by
        the sandbox mask, not by denying the argument.  A count below one means
        the inode is already unlinked by a concurrent replace and has fewer,
        never more, names.
        """
        if not stat.S_ISREG(info.st_mode) or info.st_nlink <= 1:
            return
        try:
            aliases = count_workspace_inode_aliases(
                self.workspace,
                (info.st_dev, info.st_ino),
            )
        except UnsafeSecretPathError as error:
            raise HardDenyError(
                f"Hard security denial: {kind} has multiple hard links and the "
                "workspace cannot be scanned to prove containment"
            ) from error
        if aliases < info.st_nlink:
            raise HardDenyError(
                f"Hard security denial: {kind} has multiple hard links and "
                "cannot be proven to stay inside the workspace"
            )

    def _validate_interpreter_modes(
        self,
        command: list[str],
        executable: str,
        work_dir: Path,
    ) -> None:
        if is_exact_version_probe(command):
            return
        if executable in {"python", "python3", "py"} and any(
            arg == "-c" or arg.startswith("-c") for arg in command[1:]
        ):
            raise HardDenyError(f"Hard security denial: Inline mode '-c' is forbidden for Python")
        if executable == "node" and any(
            arg in {"-e", "--eval", "-p", "--print"}
            or arg.startswith(("--eval=", "--print=", "-e"))
            for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: inline Node execution is forbidden")
        if executable == "node" and any(
            arg in {"-r", "--require", "--loader", "--import"}
            or arg.startswith(("-r", "--require=", "--loader=", "--experimental-loader=", "--import="))
            or arg in {"--experimental-loader", "--run"}
            or arg.startswith("--run=")
            for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: Node preload/loader execution is forbidden")
        if executable == "ruby" and any(
            arg == "-e"
            or arg.startswith("-e")
            or arg in {"-r", "--require", "-I", "--include", "-C", "-X", "-S", "-x"}
            or arg.startswith(("--require=", "--include=", "-r", "-I", "-C", "-X", "-S", "-x"))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: Ruby inline/preload/search/chdir modes are forbidden"
            )
        if executable == "perl" and any(
            arg in {"-e", "-E", "-M", "-m", "-I", "-S", "-x"}
            or arg.startswith(("-e", "-E", "-M", "-m", "-I", "-S", "-x"))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: Perl inline/preload/search/chdir modes are forbidden"
            )
        if executable == "php" and any(
            arg in {"-a", "-r", "-B", "-R", "-E", "-d", "-c", "-S", "--interactive", "--define", "--php-ini", "--server", "--process-begin", "--process-code", "--process-end"}
            or arg.startswith(("-r", "-B", "-R", "-E", "-d", "-c", "-S", "--define=", "--php-ini=", "--server=", "--process-begin=", "--process-code=", "--process-end="))
            for arg in command[1:]
        ):
            raise HardDenyError(
                "Hard security denial: PHP inline/config/server execution modes are forbidden"
            )
        if executable in {"python", "python3", "py"} and any(
            arg in {"-i", "--inspect"} or arg.startswith("--inspect=")
            for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: Python interactive mode is forbidden")
        if executable == "node" and any(
            arg in {"-i", "--interactive"} for arg in command[1:]
        ):
            raise HardDenyError("Hard security denial: Node interactive mode is forbidden")
        if executable in {"python", "python3", "py", "node", "ruby", "perl", "php"}:
            mode, script = _interpreter_target(command, executable)
            if script == "-":
                raise HardDenyError("Hard security denial: interpreter stdin execution is forbidden")
            if mode == "none":
                raise HardDenyError(
                    f"Hard security denial: '{command[0]}' must select a concrete workspace script"
                )
        if executable in {"awk", "gawk"}:
            self._validate_awk(command, work_dir)
        if executable == "sed":
            self._validate_sed(command, work_dir)

    def _validate_awk(self, command: list[str], work_dir: Path) -> None:
        programs: list[str] = []
        index = 1
        while index < len(command):
            token = command[index]
            if (
                token in {"-p", "-o", "-d", "-D", "--profile", "--pretty-print", "--dump-variables", "--debug"}
                or token.startswith(("--profile=", "--pretty-print=", "--dump-variables=", "--debug="))
                or (
                    token.startswith("-")
                    and not token.startswith("--")
                    and len(token) > 2
                    and token[1] in {"p", "o", "d", "D"}
                )
            ):
                raise HardDenyError("Hard security denial: AWK diagnostic/output-file modes are forbidden")
            if (
                token in {"-i", "--include"}
                or (token.startswith("-i") and token != "-i")
                or token.startswith("--include=")
            ):
                raise HardDenyError("Hard security denial: AWK source inclusion is forbidden")
            if (
                token in {"-l", "--load"}
                or (token.startswith("-l") and token != "-l")
                or token.startswith("--load=")
            ):
                raise HardDenyError("Hard security denial: dynamic AWK extensions are forbidden")
            if token in {"-f", "--file", "-E", "--exec"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK program-file option requires a file")
                programs.append(self._read_inspectable_script(command[index + 1], work_dir, "AWK"))
                index += 2
                continue
            if token.startswith("-f") and token != "-f":
                programs.append(self._read_inspectable_script(token[2:], work_dir, "AWK"))
                index += 1
                continue
            if token == "--":
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK requires a program after '--'")
                programs.append(command[index + 1])
                break
            if token.startswith("-E") and token != "-E":
                programs.append(self._read_inspectable_script(token[2:], work_dir, "AWK"))
                index += 1
                continue
            if token.startswith("--file="):
                programs.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "AWK"))
                index += 1
                continue
            if token.startswith("--exec="):
                programs.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "AWK"))
                index += 1
                continue
            if token in {"-e", "--source"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: AWK source option requires a program")
                programs.append(command[index + 1])
                index += 2
                continue
            if token.startswith("-e") and token != "-e":
                programs.append(token[2:])
                index += 1
                continue
            if token.startswith("--source="):
                programs.append(token.partition("=")[2])
                index += 1
                continue
            if token in {"-v", "--assign", "-F", "--field-separator"}:
                if index + 1 >= len(command):
                    raise HardDenyError(f"Hard security denial: AWK option '{token}' requires a value")
                index += 2
                continue
            if (
                (token.startswith("-v") and token != "-v")
                or (token.startswith("-F") and token != "-F")
                or token.startswith(("--assign=", "--field-separator="))
            ):
                index += 1
                continue
            if token == "-W" or token.startswith("-W"):
                raise HardDenyError(
                    "Hard security denial: implementation-specific AWK -W modes are forbidden"
                )
            if token in _AWK_SAFE_LONG_OPTIONS or token.startswith("--lint="):
                index += 1
                continue
            if token.startswith("--"):
                raise HardDenyError(
                    f"Hard security denial: unsupported or abbreviated AWK option '{token}'"
                )
            if token in _AWK_SAFE_SHORT_OPTIONS:
                index += 1
                continue
            if token.startswith("-"):
                raise HardDenyError(
                    f"Hard security denial: unsupported or ambiguous AWK option '{token}'"
                )
            programs.append(token)
            # The first positional token is the inline program; remaining
            # positionals are input files and need no language scan.
            break

        for program in programs:
            stripped = _strip_awk_strings(program.replace("\\\n", ""))
            if _AWK_SYSTEM_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK system() calls are forbidden")
            if _AWK_LOAD_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK dynamic extension loading is forbidden")
            if _AWK_INCLUDE_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK source inclusion is forbidden")
            if re.search(r"@[A-Za-z_][A-Za-z0-9_:]*\s*\(", stripped):
                raise HardDenyError("Hard security denial: indirect AWK function execution is forbidden")
            if _AWK_EXTERNAL_PIPE_RE.search(stripped):
                raise HardDenyError("Hard security denial: AWK getline with a pipe/external pipe is forbidden")
            if re.search(r">{1,2}", stripped):
                raise HardDenyError("Hard security denial: AWK output redirection is forbidden")

    def _validate_sed(self, command: list[str], work_dir: Path) -> None:
        scripts: list[str] = []
        explicit_scripts = False
        index = 1
        while index < len(command):
            token = command[index]
            lowered = token.casefold()
            if lowered == "--in-place" or lowered.startswith("--in-place="):
                raise HardDenyError("Hard security denial: In-place sed mode is forbidden")
            if token in {"-e", "--expression"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: sed expression flag requires a script")
                scripts.append(command[index + 1])
                explicit_scripts = True
                index += 2
                continue
            if token.startswith("--expression="):
                scripts.append(token.partition("=")[2])
                explicit_scripts = True
                index += 1
                continue
            if token in {"-f", "--file"}:
                if index + 1 >= len(command):
                    raise HardDenyError("Hard security denial: sed -f requires a script file")
                scripts.append(self._read_inspectable_script(command[index + 1], work_dir, "sed"))
                explicit_scripts = True
                index += 2
                continue
            if token.startswith("--file="):
                scripts.append(self._read_inspectable_script(token.partition("=")[2], work_dir, "sed"))
                explicit_scripts = True
                index += 1
                continue
            if lowered in {
                "--quiet", "--silent", "--regexp-extended", "--posix", "--sandbox",
                "--unbuffered", "--null-data", "--debug", "--separate",
            }:
                index += 1
                continue
            if token.startswith("--"):
                raise HardDenyError(f"Hard security denial: unsupported sed option '{token}'")
            if token.startswith("-") and not token.startswith("--"):
                cluster = token[1:]
                position = 0
                consumed_script = False
                while position < len(cluster):
                    option = cluster[position]
                    if option == "i":
                        raise HardDenyError("Hard security denial: In-place sed mode is forbidden")
                    if option == "e":
                        attached = cluster[position + 1 :]
                        if attached:
                            scripts.append(attached)
                            consumed_script = True
                        elif index + 1 < len(command):
                            scripts.append(command[index + 1])
                            index += 1
                            consumed_script = True
                        else:
                            raise HardDenyError("Hard security denial: sed -e requires a script")
                        explicit_scripts = True
                        break
                    if option == "f":
                        attached = cluster[position + 1 :]
                        if not attached:
                            if index + 1 >= len(command):
                                raise HardDenyError("Hard security denial: sed -f requires a script file")
                            attached = command[index + 1]
                            index += 1
                        scripts.append(self._read_inspectable_script(attached, work_dir, "sed"))
                        explicit_scripts = True
                        consumed_script = True
                        break
                    if option == "l":
                        attached = cluster[position + 1 :]
                        if attached:
                            if not attached.isdigit():
                                raise HardDenyError(
                                    "Hard security denial: ambiguous sed -l option is forbidden"
                                )
                        else:
                            if index + 1 >= len(command) or not command[index + 1].isdigit():
                                raise HardDenyError(
                                    "Hard security denial: sed -l requires a numeric line length"
                                )
                            index += 1
                        break
                    position += 1
                index += 1
                if consumed_script:
                    continue
                continue
            if not explicit_scripts and not scripts:
                scripts.append(token)
            index += 1

        for script in scripts:
            if _sed_script_is_dangerous(script):
                raise HardDenyError(
                    "Hard security denial: sed execute/write commands (e, w, W, s///e, s///w) are forbidden"
                )

    def _read_inspectable_script(self, token: str, work_dir: Path, language: str) -> str:
        if token == "-":
            raise HardDenyError(
                f"Hard security denial: {language} program input from stdin is forbidden"
            )
        path = self._resolve_workspace_reference(token, work_dir, f"{language} script")
        if not path.exists():
            raise HardDenyError(f"Hard security denial: {language} script file does not exist")
        if not path.is_file():
            raise HardDenyError(f"Hard security denial: {language} script is not a regular file")
        try:
            if path.stat().st_size > 1024 * 1024:
                raise HardDenyError(f"Hard security denial: {language} script is too large to inspect")
            return path.read_text(encoding="utf-8", errors="strict")
        except UnicodeError as error:
            raise HardDenyError(f"Hard security denial: {language} script is not UTF-8") from error
        except OSError as error:
            raise HardDenyError(f"Hard security denial: cannot inspect {language} script: {error}") from error

    def _validate_script_argument(self, script: str, executable: str, work_dir: Path) -> None:
        self._validate_common_tokens([script], work_dir=work_dir)
        if not self._looks_like_relative_script(script, _SCRIPT_EXTENSIONS[executable]):
            raise HardDenyError(
                f"Hard security denial: for '{executable}' only a workspace script file is allowed"
            )
        self._resolve_workspace_reference(script, work_dir, f"{executable} script")

    def _looks_like_relative_script(self, path_token: str, extensions: tuple[str, ...]) -> bool:
        normalized = path_token.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized.lower().endswith(extensions)

    def _build_allowed_commands(self) -> dict[str, set[str] | None]:
        allowed: dict[str, set[str] | None] = dict(_COMMON_ALLOWED)
        platform_cmds = _WINDOWS_ALLOWED if self._is_windows else _UNIX_ALLOWED
        allowed.update(platform_cmds)
        if self.profile == "dev":
            allowed.update(_DEV_COMMON_ALLOWED)
            allowed.update(_DEV_WINDOWS_ALLOWED if self._is_windows else _DEV_UNIX_ALLOWED)
        return allowed

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _is_absolute_path(self, token: str) -> bool:
        """Absolute path: Unix (/..., ~/...) or Windows (C:\\...)."""
        if token.startswith("/") or token.startswith("~"):
            return True
        if len(token) >= 3 and token[1] == ":" and token[2] in ("/", "\\"):
            return True
        return False

    def _resolve_cwd(self, cwd: str | None) -> Path:
        """Resolves working directory strictly within workspace."""
        if not cwd:
            candidate = self.workspace
        else:
            if self._is_absolute_path(cwd) or cwd.startswith("\\\\"):
                raise HardDenyError("Hard security denial: absolute paths are forbidden for cwd")
            if ".." in [part for part in cwd.replace("\\", "/").split("/") if part]:
                raise HardDenyError("Hard security denial: working directory is outside workspace")
            try:
                candidate = (self.workspace / cwd).resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise HardDenyError(
                    f"Hard security denial: cannot resolve working directory: {error}"
                ) from error
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise HardDenyError("Hard security denial: working directory is outside workspace")

        if not candidate.is_dir():
            raise HardDenyError(f"Hard security denial: working directory not found: {cwd}")

        self._check_resolved_sensitive_path(candidate, "working directory")

        return candidate

    def _clamp_timeout(self, timeout: float | None) -> float:
        if timeout is None:
            return self.timeout
        return max(1.0, min(timeout, self._timeout_ceiling))

    def _resolve_workspace_reference(self, token: str, work_dir: Path, kind: str) -> Path:
        raw = Path(token).expanduser()
        try:
            candidate = (raw if raw.is_absolute() else work_dir / raw).resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise HardDenyError(
                f"Hard security denial: cannot resolve {kind}: {error}"
            ) from error
        try:
            candidate.relative_to(self.workspace)
        except ValueError as error:
            raise HardDenyError(
                f"Hard security denial: {kind} resolves outside the workspace"
            ) from error
        self._check_resolved_sensitive_path(candidate, kind)
        return candidate

    def _check_resolved_sensitive_path(self, candidate: Path, kind: str) -> None:
        normalized = candidate.as_posix()
        if _INTERNAL_TOKEN_RE.search(normalized):
            raise HardDenyError(
                f"Hard security denial: {kind} points into internal .kvist state"
            )
        if _SECRET_TOKEN_RE.search(normalized):
            raise HardDenyError(
                f"Hard security denial: {kind} points into secret material"
            )

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str]:
        """Build a child environment from an allowlist; never inherit wholesale."""
        self._validate_child_home()
        env: dict[str, str] = {
            "PATH": _controlled_child_path(self.workspace, self._external_tool_paths),
            "HOME": str(self._child_home),
            "TMPDIR": "/tmp",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PIP_NO_INPUT": "1",
        }
        explicitly_allowed = _explicit_child_env_names()
        for name, value in os.environ.items():
            upper = name.upper()
            if _is_blocked_child_env_name(upper):
                continue
            if upper in _SAFE_CHILD_ENV_NAMES or upper.startswith("LC_") or upper in explicitly_allowed:
                if redact_text(value) != value:
                    continue
                env[name] = value

        if overrides is None:
            return env
        if not isinstance(overrides, dict):
            raise HardDenyError("Hard security denial: environment overrides must be a string mapping")
        for name, value in overrides.items():
            if not isinstance(name, str) or not _ENV_NAME_RE.fullmatch(name):
                raise HardDenyError("Hard security denial: invalid environment override name")
            if not isinstance(value, str):
                raise HardDenyError(f"Hard security denial: environment override '{name}' is not a string")
            upper = name.upper()
            if _is_blocked_child_env_name(upper) or upper in {"PATH", "HOME", "TMPDIR"}:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' is secret-bearing or execution-controlling"
                )
            if upper not in _SAFE_CHILD_ENV_NAMES and not upper.startswith("LC_") and upper not in explicitly_allowed:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' is not in AGENT_CHILD_ENV_ALLOW"
                )
            if redact_text(value) != value:
                raise HardDenyError(
                    f"Hard security denial: environment override '{name}' contains credential-bearing data"
                )
            env[name] = value
        return env

    def _rotate_child_home(self) -> None:
        previous = self._child_home_manager
        manager = tempfile.TemporaryDirectory(
            prefix="kvist-home-", ignore_cleanup_errors=True
        )
        path = Path(manager.name)
        try:
            path.chmod(0o700)
        except OSError as error:
            manager.cleanup()
            raise HardDenyError(
                f"Hard security denial: cannot secure child HOME: {error}"
            ) from error
        self._child_home_manager = manager
        self._child_home = path
        if previous is not None:
            previous.cleanup()

    def _validate_child_home(self) -> None:
        try:
            info = self._child_home.lstat()
            entries = list(self._child_home.iterdir())
        except OSError as error:
            raise HardDenyError(
                f"Hard security denial: child HOME is unavailable: {error}"
            ) from error
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise HardDenyError("Hard security denial: child HOME is not a real directory")
        if os.name == "nt":
            _validate_windows_private_directory(self._child_home)
        else:
            if info.st_mode & 0o077:
                raise HardDenyError("Hard security denial: child HOME permissions are not private")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise HardDenyError("Hard security denial: child HOME has the wrong owner")
        if entries:
            raise HardDenyError("Hard security denial: child HOME is not empty")

    def _build_execution_context(
        self,
        command: list[str],
        work_dir: Path,
        role: str,
        env: dict[str, str],
        bindings: tuple[ExecutableBinding, ...],
        *,
        sandbox_mode: SandboxMode,
        network_allowed: bool,
        bwrap_path: str | None,
    ) -> ExecutionContext:
        referenced = tuple(self._hash_referenced_files(command, work_dir))
        environment_payload = json.dumps(sorted(env.items()), ensure_ascii=False, separators=(",", ":"))
        policy_payload = json.dumps(
            {
                "allow": self._policy.allow,
                "deny": self._policy.deny,
                "ask": self._policy.ask,
                "note": self._policy_note,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sandbox_identity = (
            _path_identity(Path(bwrap_path)) if bwrap_path else None
        )
        try:
            mask_digest = secret_mask_digest(self.workspace)
        except UnsafeSecretPathError as error:
            raise HardDenyError(
                f"Hard security denial: cannot build a complete secret mask: {error}"
            ) from error
        return ExecutionContext(
            argv=tuple(command),
            cwd=str(work_dir),
            cwd_identity=_path_identity(work_dir),
            role=role,
            sandbox_mode=sandbox_mode.value,
            sandbox_profile=self.profile,
            network_mode="allow" if network_allowed else "deny",
            environment_digest=hashlib.sha256(environment_payload.encode("utf-8")).hexdigest(),
            policy_digest=hashlib.sha256(policy_payload.encode("utf-8")).hexdigest(),
            sandbox_executable_identity=sandbox_identity,
            executable_bindings=bindings,
            referenced_files=referenced,
            secret_mask_digest=mask_digest,
            tool_paths=tuple(str(path) for path in self._external_tool_paths),
        )

    def _resolve_executable(self, token: str, work_dir: Path, env: dict[str, str]) -> Path | None:
        binding = self._resolve_executable_binding(token, work_dir, env)
        return Path(binding.resolved_path) if binding.resolved_path else None

    def _resolve_executable_bindings(
        self, command: list[str], work_dir: Path, env: dict[str, str]
    ) -> tuple[ExecutableBinding, ...]:
        bindings: list[ExecutableBinding] = []
        for segment in self._split_pipe_segments(command):
            token = segment[0]
            if (
                self._is_windows
                and is_bare_executable_token(token)
                and token.casefold() in _WINDOWS_CMD_BUILTINS
            ):
                cmd_binding = self._resolve_executable_binding("cmd.exe", work_dir, env)
                binding = ExecutableBinding(
                    token=token,
                    lookup_path=cmd_binding.lookup_path,
                    lookup_in_workspace=cmd_binding.lookup_in_workspace,
                    lookup_is_symlink=cmd_binding.lookup_is_symlink,
                    resolved_path=cmd_binding.resolved_path,
                    requested_identity=canonical_executable_name(token),
                    effective_identity=cmd_binding.effective_identity,
                    identity_matches=(
                        canonical_executable_name(token) == cmd_binding.effective_identity
                        if cmd_binding.effective_identity is not None
                        else None
                    ),
                    lookup_identity=cmd_binding.lookup_identity,
                    target_identity=cmd_binding.target_identity,
                    trusted_system=cmd_binding.trusted_system,
                    content_digest=cmd_binding.content_digest,
                    requested_name=canonical_executable_name(token),
                    effective_name=cmd_binding.effective_name,
                    target_in_workspace=cmd_binding.target_in_workspace,
                    lookup_is_alias=cmd_binding.lookup_is_alias,
                    origin=cmd_binding.origin,
                    identity_mismatch=False,
                )
            else:
                binding = self._resolve_executable_binding(token, work_dir, env)
            bindings.append(binding)
        if "|" in command:
            bindings.append(self._resolve_executable_binding("sh", work_dir, env))
        return tuple(bindings)

    def _resolve_executable_binding(
        self, token: str, work_dir: Path, env: dict[str, str], *, attest_shebang: bool = True
    ) -> ExecutableBinding:
        lookup: Path | None
        if not is_bare_executable_token(token):
            raw = Path(token).expanduser()
            lookup = raw if raw.is_absolute() else work_dir / raw
            lookup = Path(os.path.abspath(lookup))
        else:
            found = shutil.which(token, path=env["PATH"])
            lookup = Path(found).absolute() if found else None

        resolved: Path | None = None
        if lookup is not None:
            try:
                candidate = lookup.resolve(strict=True)
                if candidate.is_file():
                    resolved = candidate
            except (OSError, RuntimeError):
                resolved = None

        trusted = bool(
            lookup is not None
            and resolved is not None
            and _is_trusted_system_executable(lookup, resolved)
        )
        digest: str | None = None
        if resolved is not None and not trusted:
            try:
                digest = _sha256_file(resolved)
            except OSError as error:
                raise HardDenyError(
                    f"Hard security denial: cannot hash executable '{token}': {error}"
                ) from error

        interpreter = (
            self._attest_shebang_interpreter(resolved, work_dir, env)
            if attest_shebang and resolved is not None and not trusted
            else None
        )
        venv_verified: tuple[FileIdentity, str] | None = None
        if not trusted and lookup is not None and resolved is not None:
            venv_verified = _verify_venv_python_launcher(
                self.workspace,
                lookup,
                resolved,
                allow_external_interpreter=self.sandbox_request == "workspace",
            )
        requested_identity = canonical_executable_name(token)
        effective_identity = (
            canonical_executable_name(resolved.name) if resolved is not None else None
        )
        requested_name = requested_identity
        effective_name = effective_identity

        lookup_in_workspace = bool(
            lookup is not None and _is_relative_to(lookup, self.workspace)
        )
        target_in_workspace = bool(
            resolved is not None and _is_relative_to(resolved, self.workspace)
        )
        lookup_is_symlink = _path_entry_is_symlink(lookup)
        lookup_is_alias = bool(
            lookup is not None
            and resolved is not None
            and (lookup_is_symlink or Path(os.path.abspath(lookup)) != resolved)
        )

        if resolved is None:
            origin = ExecutableOrigin.UNRESOLVED
        elif trusted:
            origin = ExecutableOrigin.TRUSTED_SYSTEM
        elif venv_verified is not None:
            origin = ExecutableOrigin.VERIFIED_VENV
        elif lookup_in_workspace and lookup_is_alias:
            origin = ExecutableOrigin.WORKSPACE_ALIAS
        elif lookup_in_workspace:
            origin = ExecutableOrigin.WORKSPACE_FILE
        else:
            origin = ExecutableOrigin.EXTERNAL_TOOL

        identity_matches = (
            (requested_name == effective_name)
            if effective_name is not None
            else None
        )
        identity_mismatch = bool(
            requested_name
            and effective_name
            and not _same_executable_family(requested_name, effective_name)
        )
        if venv_verified is not None:
            identity_mismatch = False

        return ExecutableBinding(
            token=token,
            lookup_path=str(lookup) if lookup is not None else None,
            resolved_path=str(resolved) if resolved is not None else None,
            lookup_identity=(
                _path_entry_identity(lookup)
                if lookup is not None
                else None
            ),
            target_identity=(
                _path_identity(resolved)
                if resolved is not None
                else None
            ),
            trusted_system=trusted,
            content_digest=digest,

            requested_name=requested_name,
            effective_name=effective_name,
            requested_identity=requested_identity,
            effective_identity=effective_identity,
            identity_matches=identity_matches,
            lookup_in_workspace=lookup_in_workspace,
            target_in_workspace=target_in_workspace,
            lookup_is_symlink=lookup_is_symlink,
            lookup_is_alias=lookup_is_alias,
            origin=origin,
            identity_mismatch=identity_mismatch,

            interpreter_path=interpreter[0] if interpreter else None,
            interpreter_identity=interpreter[1] if interpreter else None,
            interpreter_digest=interpreter[2] if interpreter else None,
            interpreter_trusted_system=interpreter[3] if interpreter else False,

            venv_launcher=venv_verified is not None,
            venv_pyvenv_cfg_identity=(
                venv_verified[0]
                if venv_verified
                else None
            ),
            venv_pyvenv_cfg_digest=(
                venv_verified[1]
                if venv_verified
                else None
            ),
        )

    def _attest_shebang_interpreter(
        self, script: Path, work_dir: Path, env: dict[str, str]
    ) -> tuple[str, FileIdentity | None, str | None, bool] | None:
        """Attest the interpreter the kernel selects from a script's ``#!`` line.

        The kernel execs ``parts[0]`` literally, so that binary is what we must
        attest — regardless of its name.  Only when ``parts[0]`` resolves to a
        *trusted-system* ``env`` do we follow its argument to the effective
        interpreter (``env`` re-executes it via PATH).  An ``env``-named binary
        anywhere else is attested as itself, closing the impersonation bypass.
        """
        try:
            with open(script, "rb") as handle:
                head = handle.read(256)
        except OSError:
            return None
        parts = _parse_shebang(head)
        if parts is None:
            return None
        if not parts:
            raise HardDenyError(
                "Hard security denial: unsupported or ambiguous shebang interpreter"
            )
        binding = self._resolve_executable_binding(
            parts[0], work_dir, env, attest_shebang=False
        )
        if (
            binding.resolved_path is not None
            and binding.trusted_system
            and os.path.basename(binding.resolved_path) == "env"
        ):
            arg = _env_interpreter_name(parts[1:])
            if not arg:
                raise HardDenyError(
                    "Hard security denial: unsupported or ambiguous env shebang interpreter"
                )
            binding = self._resolve_executable_binding(
                arg, work_dir, env, attest_shebang=False
            )
        if binding.resolved_path is None:
            # Interpreter cannot be resolved with the sandboxed PATH; the exec
            # would fail, but do not fabricate an attestation for it.
            return (binding.token, None, None, False)
        interpreter = Path(binding.resolved_path)
        in_workspace = _is_relative_to(interpreter, self.workspace)
        if not binding.trusted_system and not in_workspace:
            raise HardDenyError(
                "Hard security denial: shebang interpreter is outside trusted system "
                "roots and the workspace"
            )
        return (
            binding.resolved_path,
            binding.target_identity,
            binding.content_digest,
            binding.trusted_system,
        )

    def _validate_executable_boundaries(
        self,
        command: list[str],
        work_dir: Path,
        bindings: tuple[ExecutableBinding, ...],
    ) -> None:
        segments = self._split_pipe_segments(command)
        for index, segment in enumerate(segments):
            token = segment[0]
            binding = bindings[index]
            if binding.lookup_in_workspace and binding.lookup_path:
                lookup = Path(binding.lookup_path)
                try:
                    # ``lstat``: a launcher symlink is attested through its own
                    # target binding, not through the target's link count.
                    lookup_info = lookup.lstat()
                except OSError as error:
                    raise HardDenyError(
                        "Hard security denial: cannot inspect workspace "
                        "executable identity"
                    ) from error
                self._check_inode_hardlink_containment(
                    lookup_info,
                    "workspace executable",
                )
            if binding.target_in_workspace and binding.resolved_path:
                self._check_workspace_regular_file_hardlinks(
                    Path(binding.resolved_path),
                    "workspace executable",
                )
            if binding.resolved_path:
                self._check_resolved_sensitive_path(
                    Path(binding.resolved_path), "executable"
                )
            if is_bare_executable_token(token):
                continue

            # A verified project venv Python launcher (e.g.
            # .venv/bin/python -> /usr/bin/python3) may resolve to a trusted,
            # user-immutable system interpreter; the launcher's own lstat
            # identity and its venv's pyvenv.cfg are attested, so swapping the
            # symlink or pyvenv.cfg invalidates approval.  This is the only
            # workspace-to-system escape permitted — a generic alias (e.g.
            # ``.venv/bin/cat -> /usr/bin/bash``) is never trusted merely
            # because its target lives under a system root; escapes to any
            # other target, or from any other basename/location, stay denied.
            # In explicit ``AGENT_SANDBOX_MODE=workspace`` only, the target
            # need not live under a trusted system root (e.g. a uv-managed
            # interpreter under the user's HOME) — see
            # ``_verify_venv_python_launcher``'s ``allow_external_interpreter``.
            # ``auto``/``bubblewrap`` keep the stricter trusted-root
            # requirement unchanged.
            if (
                binding.lookup_in_workspace
                and not binding.target_in_workspace
                and not binding.venv_launcher
            ):
                raise HardDenyError(
                    "Hard security denial: executable symlink resolves"
                    " outside the workspace: "
                    f"requested={binding.token!r}, "
                    f"lookup={binding.lookup_path!r}, "
                    f"effective={binding.resolved_path!r}"
                )
            if not binding.lookup_in_workspace and not binding.trusted_system:
                raise HardDenyError(
                    "Hard security denial: path-qualified executable is outside trusted system roots"
                )

        if "|" in command:
            shell_binding = bindings[-1]
            if not shell_binding.resolved_path or not shell_binding.trusted_system:
                raise HardDenyError(
                    "Hard security denial: trusted pipeline shell is unavailable"
                )

    def _requires_executable_origin_approval(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...]
    ) -> bool:
        for index, segment in enumerate(self._split_pipe_segments(command)):
            binding = bindings[index]
            if binding.trusted_system:
                continue
            # Only a verified project venv Python launcher is treated as a
            # trusted origin; a generic workspace symlink/PATH alias landing
            # on a trusted-looking target (e.g. a workspace ``cat`` aliasing
            # ``/usr/bin/bash``) must still require per-execution approval so
            # a project allow rule keyed on the alias name cannot silently
            # authorize a different effective executable.
            if binding.venv_launcher:
                continue
            executable = canonical_executable_name(segment[0])
            if executable in self._allowed or is_exact_version_probe(segment):
                return True
        return False

    def _materialize_executables(
        self,
        command: list[str],
        bindings: tuple[ExecutableBinding, ...],
    ) -> list[str]:
        materialized: list[str] = []
        for segment_index, segment in enumerate(self._split_pipe_segments(command)):
            if segment_index:
                materialized.append("|")
            binding = bindings[segment_index]
            is_builtin = (
                self._is_windows
                and is_bare_executable_token(segment[0])
                and segment[0].casefold() in _WINDOWS_CMD_BUILTINS
            )
            materialized.append(self._materialized_argv0(segment[0], binding, is_builtin))
            args = list(segment[1:])
            if canonical_executable_name(segment[0]) == "git" and not is_exact_version_probe(segment):
                args = _harden_git_arguments(args)
            materialized.extend(args)
        return materialized

    def _materialized_argv0(self, token: str, binding: ExecutableBinding, is_builtin: bool) -> str:
        if is_builtin or not binding.resolved_path:
            return token
        # A project launcher symlink (e.g. .venv/bin/python) must keep its own
        # path as argv[0] so the interpreter finds pyvenv.cfg and its
        # site-packages; substituting the resolved system binary would break
        # venv semantics.  The launcher inode is attested, so a swap between
        # approval and spawn invalidates the bound context.
        if (
            not is_bare_executable_token(token)
            and binding.lookup_path
            and binding.lookup_path != binding.resolved_path
            and binding.venv_launcher
        ):
            return binding.lookup_path
        return binding.resolved_path

    def _hash_referenced_files(self, command: list[str], work_dir: Path) -> list[tuple[str, str]]:
        paths: dict[str, Path] = {}
        for segment in self._split_pipe_segments(command):
            for path in self._referenced_files_for_segment(segment, work_dir):
                if not path.exists():
                    continue
                try:
                    canonical = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise HardDenyError(
                        f"Hard security denial: cannot resolve referenced execution input: {error}"
                    ) from error
                if not canonical.is_file():
                    raise HardDenyError("Hard security denial: referenced execution input is not a regular file")
                try:
                    relative = canonical.relative_to(self.workspace).as_posix()
                except ValueError as error:
                    raise HardDenyError(
                        "Hard security denial: referenced execution input is outside the workspace"
                    ) from error
                self._check_resolved_sensitive_path(canonical, "referenced execution input")
                paths[relative] = canonical
        hashed: list[tuple[str, str]] = []
        for relative, path in sorted(paths.items()):
            try:
                digest = _sha256_file(path)
            except OSError as error:
                raise HardDenyError(
                    f"Hard security denial: cannot hash referenced execution input '{relative}': {error}"
                ) from error
            hashed.append((relative, digest))
        return hashed

    def _referenced_files_for_segment(self, command: list[str], work_dir: Path) -> list[Path]:
        executable = canonical_executable_name(command[0])
        found: list[Path] = []

        if is_exact_version_probe(command):
            return found

        if executable in {"bash", "sh"} and len(command) == 2:
            found.append(self._resolve_workspace_reference(command[1], work_dir, "script"))
        elif executable in {"powershell", "pwsh"}:
            script = _option_value(command, {"-file", "-f"}, casefold=True)
            if script:
                found.append(self._resolve_workspace_reference(script, work_dir, "PowerShell script"))
        elif executable in {"cmd", "cmd.exe"} and len(command) == 3:
            payload = command[2]
            if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS[executable]):
                found.append(self._resolve_workspace_reference(payload, work_dir, "CMD script"))
        elif executable in {"python", "python3", "py", "node", "ruby", "perl", "php"}:
            script = _direct_interpreter_script(command, executable)
            if script:
                found.append(self._resolve_workspace_reference(script, work_dir, "interpreter script"))
            found.extend(self._existing_workspace_file_arguments(command[1:], work_dir))
        elif executable in {"awk", "gawk", "sed"}:
            for token in _script_file_arguments(command):
                found.append(self._resolve_workspace_reference(token, work_dir, f"{executable} script"))
        elif executable == "make":
            make_dir = self._effective_option_directory(
                command, work_dir, {"-C", "--directory"}, sequential=True
            )
            makefiles = _option_values(command, {"-f", "--file", "--makefile"})
            if makefiles:
                for makefile in makefiles:
                    found.append(self._resolve_workspace_reference(makefile, make_dir, "Makefile"))
            else:
                for name in ("GNUmakefile", "makefile", "Makefile"):
                    candidate = make_dir / name
                    if candidate.is_file():
                        found.append(candidate)
                        break
        elif executable == "cargo":
            manifest = _option_value(command, {"--manifest-path"})
            if manifest:
                found.append(self._resolve_workspace_reference(manifest, work_dir, "Cargo manifest"))
            else:
                candidate = _find_parent_file(work_dir, self.workspace, "Cargo.toml")
                if candidate:
                    found.append(candidate)
        elif executable == "go":
            go_dir = self._effective_option_directory(command, work_dir, {"-C"})
            candidate = _find_parent_file(go_dir, self.workspace, "go.mod")
            if candidate:
                found.append(candidate)
            found.extend(self._existing_workspace_file_arguments(command[1:], go_dir))
        elif executable == "cmake":
            source = _option_value(command, {"-S", "--source"})
            if source:
                source_dir = self._resolve_workspace_reference(source, work_dir, "CMake source")
            else:
                source_dir = self._cmake_positional_source(command, work_dir) or work_dir
            found.append(source_dir / "CMakeLists.txt")
        elif executable == "ninja":
            ninja_dir = self._effective_option_directory(command, work_dir, {"-C"})
            build_files = _option_values(command, {"-f"}) or ["build.ninja"]
            for build_file in build_files:
                found.append(self._resolve_workspace_reference(build_file, ninja_dir, "Ninja build file"))
        elif executable in {"npm", "npx", "pnpm", "yarn"}:
            directory_flags = {
                "npm": {"--prefix"},
                "npx": {"--prefix"},
                "pnpm": {"--dir", "--prefix", "-C"},
                "yarn": {"--cwd"},
            }[executable]
            package_dir = self._effective_option_directory(command, work_dir, directory_flags)
            found.append(package_dir / "package.json")
        elif executable == "git":
            git_dir = self._git_metadata_directory(work_dir)
            if git_dir is not None:
                commondir = git_dir / "commondir"
                if commondir.exists() or commondir.is_symlink():
                    found.append(commondir)
                found.extend(self._git_configuration_paths(git_dir))
                common_dir = self._git_common_directory(git_dir)
                for hooks in {git_dir / "hooks", common_dir / "hooks"}:
                    if hooks.is_dir():
                        for hook in hooks.iterdir():
                            if hook.is_file() and os.access(hook, os.X_OK):
                                found.append(hook)
        return found

    def _effective_option_directory(
        self,
        command: list[str],
        work_dir: Path,
        names: set[str],
        *,
        sequential: bool = False,
    ) -> Path:
        current = work_dir
        for value in _option_values(command, names):
            base = current if sequential else work_dir
            current = self._resolve_workspace_reference(value, base, "tool working directory")
            if not current.is_dir():
                # The tool will report a missing directory; keeping the
                # canonical path still makes the approval context stable.
                break
        return current

    def _cmake_positional_source(self, command: list[str], work_dir: Path) -> Path | None:
        for token in command[1:]:
            if token.startswith("-"):
                continue
            try:
                candidate = self._resolve_workspace_reference(token, work_dir, "CMake source")
            except HardDenyError:
                raise
            if candidate.is_dir() and (candidate / "CMakeLists.txt").exists():
                return candidate
        return None

    def _existing_workspace_file_arguments(self, args: list[str], work_dir: Path) -> list[Path]:
        """Bind plausible directly named files without guessing option arity."""
        found: dict[str, Path] = {}
        for token in args:
            candidates = [token]
            if "=" in token:
                candidates.append(token.partition("=")[2])
            for value in candidates:
                if not value or value.startswith("-"):
                    continue
                try:
                    raw = Path(value).expanduser()
                    candidate = raw if raw.is_absolute() else work_dir / raw
                    if not candidate.exists() and not candidate.is_symlink():
                        continue
                    resolved = self._resolve_workspace_reference(value, work_dir, "interpreter input")
                except (OSError, RuntimeError, ValueError):
                    continue
                if resolved.is_file():
                    found[str(resolved)] = resolved
        return list(found.values())

    @staticmethod
    def _escape_cmd_arg(arg: str) -> str:
        """Escapes CMD special characters for safe passing through cmd.exe /c."""
        # Characters that CMD interprets as operators
        _CMD_META = set('()%!^"<>&|')
        if not any(ch in _CMD_META for ch in arg):
            return arg
        # Escape each CMD metacharacter with ^
        escaped = []
        for ch in arg:
            if ch in _CMD_META:
                escaped.append("^")
            escaped.append(ch)
        return "".join(escaped)

    def _wrap_for_platform(
        self, command: list[str], bindings: tuple[ExecutableBinding, ...]
    ) -> list[str]:
        """On Windows wraps CMD built-in commands through cmd.exe /c."""
        if self._is_windows and command[0].lower() in _WINDOWS_CMD_BUILTINS:
            cmd_path = bindings[0].resolved_path
            if not cmd_path or not bindings[0].trusted_system:
                raise HardDenyError("Hard security denial: trusted cmd.exe is unavailable")
            # Escape arguments to prevent shell injection via & | && etc.
            safe_args = [self._escape_cmd_arg(a) for a in command]
            return [cmd_path, "/c"] + safe_args
        return command

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[: self.max_output_chars]
            + f"\n[TRUNCATED: showing first {self.max_output_chars} of {len(text)} characters]"
        )

    def _bound_output(self, data: bytes, byte_truncated: bool) -> str:
        """Decode captured bytes and apply the character display cap.

        ``byte_truncated`` marks output already clipped at the supervisor's
        in-memory byte ceiling; surface that even when the decoded prefix fits
        the character cap so truncation is never silent.
        """
        text = _decode_process_output(data)
        if len(text) > self.max_output_chars:
            return (
                text[: self.max_output_chars]
                + f"\n[TRUNCATED: showing first {self.max_output_chars} of "
                f"{len(text)}+ characters]"
            )
        if byte_truncated:
            return text + "\n[TRUNCATED: output exceeded the capture byte limit]"
        return text


def _git_config_section_values(content: str, section: str, key: str) -> list[str]:
    """Extract simple scalar values from an exact, unsubsectioned Git section."""
    values: list[str] = []
    section_pattern = re.compile(
        rf"(?ims)^\s*\[\s*{re.escape(section)}\s*\]\s*"
        rf"(.*?)(?=^\s*\[|\Z)"
    )
    key_pattern = re.compile(
        rf"(?im)^\s*{re.escape(key)}\s*=\s*(.*?)\s*$"
    )
    for match in section_pattern.finditer(content):
        values.extend(value.group(1) for value in key_pattern.finditer(match.group(1)))
    return values


def _harden_git_arguments(args: list[str]) -> list[str]:
    """Disable repository-configured hooks/helpers for approved Git reads."""
    hardened = [
        "-c", "core.fsmonitor=false",
        "-c", "log.showSignature=false",
        "-c", "format.pretty=medium",
        "-c", "log.diffMerges=off",
        "-c", "diff.submodule=short",
        "-c", "diff.ignoreSubmodules=all",
        "-c", "core.alternateRefsCommand=",
        "-c", "branch.sort=refname",
        "-c", "tag.sort=refname",
        "--no-optional-locks",
    ]
    if not args:
        return hardened
    subcommand = args[0].casefold()
    helper_flags = ["--no-ext-diff", "--no-textconv"]
    submodule_flags = ["--ignore-submodules=all"]
    if subcommand == "status":
        return hardened + [args[0]] + submodule_flags + args[1:]
    if subcommand in {"diff", "log", "show"}:
        present = set(args[1:])
        return (
            hardened
            + [args[0]]
            + [flag for flag in helper_flags if flag not in present]
            + submodule_flags
            + args[1:]
        )
    if (
        subcommand == "stash"
        and len(args) >= 2
        and args[1].casefold() in {"list", "show"}
    ):
        present = set(args[2:])
        return (
            hardened
            + args[:2]
            + [flag for flag in helper_flags if flag not in present]
            + submodule_flags
            + args[2:]
        )
    return hardened + args


def _argument_path_fragments(token: str) -> set[str]:
    """Return direct and common attached option/response path candidates."""
    fragments = {token}
    if "=" in token:
        fragments.add(token.partition("=")[2])
    if token.startswith("@") and len(token) > 1:
        fragments.add(token[1:])
    for prefix in ("-f", "-E", "-C", "-S"):
        if token.startswith(prefix) and token != prefix:
            fragments.add(token[len(prefix) :])
    return fragments


def _docker_path_is_risk_graded(tokens: list[str], index: int) -> bool:
    """Host-path Docker options are authorized by DANGEROUS risk, not path hiding."""
    path_flags = {
        "-v", "--volume", "--mount", "--mount-add", "--volumes-from", "--device",
        "--build-context", "--secret", "--ssh", "--output", "-o", "--cache-from",
        "--cache-to", "--file", "-f",
    }
    token = tokens[index]
    flag = token.partition("=")[0]
    if flag in path_flags or (
        token.startswith("-o") and not token.startswith("--")
    ):
        return True
    if token.startswith("-") and not token.startswith("--") and "v" in token[1:]:
        return True
    if index == 0:
        return False
    previous = tokens[index - 1]
    previous_flag = previous.partition("=")[0]
    return previous_flag in path_flags or (
        previous.startswith("-")
        and not previous.startswith("--")
        and "v" in previous[1:]
    )


_PROJECT_TOOL_SUBDIRS: tuple[str, ...] = (
    ".venv/bin", "venv/bin", ".venv/Scripts", "venv/Scripts", "node_modules/.bin",
)


def _project_tool_dirs(workspace: Path) -> list[Path]:
    """Trusted project-local tool directories that exist inside the workspace."""
    dirs: list[Path] = []
    for relative in _PROJECT_TOOL_SUBDIRS:
        candidate = workspace / relative
        try:
            if candidate.is_dir() and _is_relative_to(candidate.resolve(strict=True), workspace):
                dirs.append(candidate)
        except OSError:
            continue
    return dirs


def _external_tool_paths(workspace: Path | None = None) -> tuple[Path, ...]:
    """Validated absolute toolchain directories from AGENT_TOOL_PATHS.

    Each entry must be absolute, exist as a directory, and not be group- or
    world-writable.  Broad host and project trees are never added wholesale.
    """
    raw = os.getenv("AGENT_TOOL_PATHS", "")
    paths: list[Path] = []
    seen: set[str] = set()
    for item in raw.split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        resolved = validated_external_tool_path(Path(item), workspace)
        if resolved is None:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        paths.append(resolved)
    return tuple(paths)


def _system_exec_dirs() -> list[Path]:
    if os.name == "nt":
        return [Path(part) for part in os.defpath.split(os.pathsep) if part]
    return [Path(p) for p in ("/usr/sbin", "/usr/bin", "/sbin", "/bin") if Path(p).is_dir()]


def _parse_shebang(head: bytes) -> list[str] | None:
    """Return the ``#!`` token list, ``[]`` for an ambiguous line, or None.

    ``None`` means the file has no shebang (e.g. an ELF binary).  An empty list
    means the shebang exists but is empty/undecodable and the caller must fail
    closed.  The first token is the program the kernel actually execs.
    """
    if not head.startswith(b"#!"):
        return None
    line = head[2:].split(b"\n", 1)[0]
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return []
    return text.split()


def _env_interpreter_name(args: list[str]) -> str:
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-S", "--split-string"):
            index += 1
            continue
        if token.startswith("-S"):
            inner = token[2:].strip()
            if inner:
                return inner.split()[0]
            index += 1
            continue
        if token.startswith("--split-string="):
            inner = token.split("=", 1)[1].strip()
            return inner.split()[0] if inner else ""
        if token in ("-u", "--unset", "-C", "--chdir", "-P"):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if "=" in token:
            index += 1
            continue
        return token.split()[0] if " " in token else token
    return ""


def _controlled_child_path(
    workspace: Path | None = None, tool_paths: tuple[Path, ...] = ()
) -> str:
    ordered: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        text = str(path)
        if text not in seen:
            seen.add(text)
            ordered.append(text)

    if workspace is not None:
        for path in _project_tool_dirs(workspace):
            add(path)
    for path in tool_paths:
        add(path)
    for path in _system_exec_dirs():
        add(path)
    return os.pathsep.join(ordered) or os.defpath


def _explicit_child_env_names() -> set[str]:
    raw = os.getenv("AGENT_CHILD_ENV_ALLOW", "")
    names: set[str] = set()
    for item in raw.split(","):
        name = item.strip().upper()
        if _ENV_NAME_RE.fullmatch(name) and not _is_blocked_child_env_name(name):
            names.add(name)
    return names


def _is_blocked_child_env_name(upper: str) -> bool:
    return (
        bool(_SECRET_ENV_RE.search(upper))
        or bool(_SECRET_CONNECTION_ENV_RE.search(upper))
        or upper.startswith(_SECRET_ENV_PREFIXES)
        or upper in _CONTROL_ENV_NAMES
        or upper.startswith(_CONTROL_ENV_PREFIXES)
    )


def _path_identity(path: Path) -> FileIdentity | None:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return None
    return (
        str(resolved),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _path_entry_identity(path: Path) -> FileIdentity | None:
    try:
        absolute = Path(os.path.abspath(path))
        info = absolute.lstat()
    except OSError:
        return None
    return (
        str(absolute),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _path_entry_is_symlink(path: Path | None) -> bool:
    """Return whether the exact lookup entry is a symlink, without following it."""
    if path is None:
        return False
    try:
        return stat.S_ISLNK(Path(os.path.abspath(path)).lstat().st_mode)
    except OSError:
        return False


def _same_executable_family(a: str, b: str) -> bool:
    """Return True when *a* and *b* name the same tool family.

    ``python`` / ``python3`` / ``python3.11`` are the same family;
    ``python`` / ``node`` are not.
    """
    if a == b:
        return True
    a_base = re.sub(r"\d[\d.]*$", "", a)
    b_base = re.sub(r"\d[\d.]*$", "", b)
    return a_base == b_base


def _is_relative_to(path: Path, boundary: Path) -> bool:
    try:
        path.relative_to(boundary)
    except ValueError:
        return False
    return True


_VENV_LAUNCHER_DIRS: frozenset[str] = frozenset(
    (".venv/bin", "venv/bin", ".venv/Scripts", "venv/Scripts")
)
# python, python3, python3.11, ... and the Windows .exe suffix.  Deliberately
# excludes bash/sh/env/perl/node/etc so a launcher aliasing another family is
# never mistaken for a venv Python interpreter.
_VENV_PYTHON_BASENAME_RE = re.compile(r"^python(3(\.\d{1,2})?)?(\.exe)?$", re.IGNORECASE)


def _verify_venv_python_launcher(
    workspace: Path,
    lookup: Path,
    resolved: Path,
    *,
    allow_external_interpreter: bool = False,
) -> tuple[FileIdentity, str] | None:
    """Verify ``lookup`` is a genuine project venv Python launcher.

    This is the *only* exception that lets a workspace-owned (attacker- or
    tool-writable) path stand in for a trusted system executable.  Every
    condition below is required; any failure returns ``None`` and the caller
    denies the workspace-to-system escape like any other untrusted alias.
    Returns ``(pyvenv_cfg_identity, pyvenv_cfg_digest)`` on success so the
    execution context binds the launcher to its venv's declared identity —
    mutating pyvenv.cfg between approval and spawn invalidates the approval.

    ``allow_external_interpreter`` is set only by the caller for explicit
    ``AGENT_SANDBOX_MODE=workspace`` (never ``auto``/``bubblewrap``, which
    already provide OS isolation and keep the stricter trusted-system-root
    requirement).  It relaxes *where* the final interpreter may live — a
    venv builder such as ``uv`` places it under the user's HOME, not under
    ``/usr``/``/bin``/``/sbin`` — but still requires that ``pyvenv.cfg``
    itself, attested below, declares that exact resolved path as the venv's
    interpreter, so a venv can only vouch for the interpreter it was built
    with, never for an arbitrary target.
    """
    try:
        if not _is_relative_to(lookup, workspace):
            return None
        rel_parts = lookup.relative_to(workspace).parts
    except ValueError:
        return None
    if len(rel_parts) != 3:
        return None
    venv_name, bin_name, launcher_name = rel_parts
    if f"{venv_name}/{bin_name}" not in _VENV_LAUNCHER_DIRS:
        return None
    if not _VENV_PYTHON_BASENAME_RE.match(launcher_name):
        return None

    venv_root = workspace / venv_name
    try:
        if stat.S_ISLNK(venv_root.lstat().st_mode):
            return None
        venv_root_resolved = venv_root.resolve(strict=True)
    except OSError:
        return None
    if not _is_relative_to(venv_root_resolved, workspace) or venv_root_resolved == workspace:
        return None

    pyvenv_cfg = venv_root / "pyvenv.cfg"
    try:
        cfg_lstat = pyvenv_cfg.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(cfg_lstat.st_mode):
        return None

    # The final target the kernel would actually exec must be a trusted,
    # user-immutable system Python — never Bash, sh, env, Perl, Node, or any
    # other binary, regardless of what the launcher basename claims.
    if not _VENV_PYTHON_BASENAME_RE.match(resolved.name):
        return None
    trusted_root = _is_trusted_system_executable(resolved, resolved)
    if not trusted_root:
        if not allow_external_interpreter:
            return None
        # Weaker workspace-only boundary: the interpreter may live outside
        # the trusted system roots, but only when the venv's own pyvenv.cfg
        # declares this exact resolved path as its interpreter — the venv
        # cannot vouch for a target it did not itself record at creation.
        declared = _read_pyvenv_cfg_executable(pyvenv_cfg)
        if declared is None:
            return None
        try:
            declared_resolved = declared.resolve(strict=True)
        except OSError:
            return None
        if declared_resolved != resolved:
            return None

    cfg_identity = _path_entry_identity(pyvenv_cfg)
    if cfg_identity is None:
        return None
    try:
        cfg_digest = _sha256_file(pyvenv_cfg)
    except OSError:
        return None
    return (cfg_identity, cfg_digest)


def _read_pyvenv_cfg_executable(pyvenv_cfg: Path) -> Path | None:
    """Return the ``executable`` path a venv's ``pyvenv.cfg`` declares, if any."""
    try:
        text = pyvenv_cfg.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip().lower() != "executable":
            continue
        value = value.strip()
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = pyvenv_cfg.parent / candidate
        return candidate
    return None


_WINDOWS_BROAD_ACCESS_SID_STRINGS = (
    "S-1-1-0",       # Everyone
    "S-1-5-11",      # Authenticated Users
    "S-1-5-32-545",  # BUILTIN\\Users
)


def _validate_windows_private_directory(path: Path) -> None:
    """Verify a directory's real Windows ACL denies broad-access principals.

    Windows has no POSIX permission bits: os.stat().st_mode is synthetic and
    chmod() does not restrict access the way it does on POSIX, so privacy has
    to be checked against the actual DACL instead of st_mode.
    """
    try:
        import ntsecuritycon
        import pywintypes
        import win32security
    except ImportError as error:
        raise HardDenyError(
            f"Hard security denial: cannot verify child HOME privacy on Windows: {error}"
        ) from error

    try:
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
    except pywintypes.error as error:
        raise HardDenyError(
            f"Hard security denial: cannot read child HOME ACL: {error}"
        ) from error

    if dacl is None:
        raise HardDenyError(
            "Hard security denial: child HOME has no DACL (unrestricted access)"
        )

    broad_sid_strings = set(_WINDOWS_BROAD_ACCESS_SID_STRINGS)
    for index in range(dacl.GetAceCount()):
        (ace_type, _ace_flags), mask, sid = dacl.GetAce(index)
        if ace_type != ntsecuritycon.ACCESS_ALLOWED_ACE_TYPE or mask == 0:
            continue
        if win32security.ConvertSidToStringSid(sid) in broad_sid_strings:
            raise HardDenyError(
                "Hard security denial: child HOME permissions are not private "
                "(broad-access principal granted)"
            )


def _is_trusted_system_executable(lookup: Path, resolved: Path) -> bool:
    """Trust only fixed system roots that the current user cannot mutate."""
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        roots = [system_root / "System32", system_root]
    else:
        roots = [Path("/usr"), Path("/bin"), Path("/sbin")]

    canonical_roots: list[Path] = []
    for root in roots:
        try:
            candidate = root.resolve(strict=True)
        except OSError:
            continue
        if candidate not in canonical_roots:
            canonical_roots.append(candidate)

    try:
        lookup_parent = lookup.parent.resolve(strict=True)
        target = resolved.resolve(strict=True)
    except OSError:
        return False
    matching_root = next(
        (
            root
            for root in canonical_roots
            if _is_relative_to(lookup_parent, root) and _is_relative_to(target, root)
        ),
        None,
    )
    if matching_root is None:
        return False

    paths_to_check = [target]
    for start in (lookup_parent, target.parent):
        current = start
        while True:
            if current not in paths_to_check:
                paths_to_check.append(current)
            if current == matching_root:
                break
            if current.parent == current:
                return False
            current = current.parent
    return not any(_path_is_user_mutable(path) for path in paths_to_check)


def _path_is_user_mutable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return True
    if not hasattr(os, "geteuid"):
        return bool(info.st_mode & stat.S_IWOTH)
    if info.st_uid == os.geteuid():
        return True
    try:
        if os.access(path, os.W_OK, effective_ids=True):
            return True
    except TypeError:
        if os.access(path, os.W_OK):
            return True
    if info.st_mode & stat.S_IWOTH:
        return True
    try:
        groups = {os.getegid(), *os.getgroups()}
    except OSError:
        groups = {os.getegid()}
    return bool(info.st_mode & stat.S_IWGRP and info.st_gid in groups)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_process_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _strip_awk_strings(program: str) -> str:
    """Blank quoted AWK string contents while preserving operators outside."""
    output: list[str] = []
    quote: str | None = None
    escaped = False
    for char in program:
        if escaped:
            output.append(" ")
            escaped = False
            continue
        if quote is not None:
            if char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            output.append(" ")
            continue
        # POSIX AWK has only double-quoted string literals. Apostrophes are
        # ordinary regexp/program characters and must not hide later tokens.
        if char == '"':
            quote = '"'
            output.append(" ")
            continue
        output.append(char)
    return "".join(output)


def _sed_script_is_dangerous(script: str) -> bool:
    if _SED_STANDALONE_WRITE_RE.search(script):
        return True
    # Check the complete script before splitting on semicolons: GNU sed's
    # alternate-regex addresses may legally contain semicolons inside their
    # delimiter pair (for example ``\%a;b%w file``).
    if _sed_command_after_addresses(script) in {"e", "w", "W"}:
        return True
    for start in [0, *(match.end() for match in re.finditer(r"[;\n{}]", script))]:
        if _sed_command_after_addresses(script[start:]) in {"e", "w", "W"}:
            return True
    length = len(script)
    for index, char in enumerate(script[:-1]):
        if char != "s":
            continue
        delimiter = script[index + 1]
        if delimiter.isalnum() or delimiter.isspace() or delimiter == "\\":
            continue
        position = index + 2
        complete = True
        for _ in range(2):
            escaped = False
            while position < length:
                current = script[position]
                position += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == delimiter:
                    break
            else:
                complete = False
                break
        if not complete:
            continue
        flags_start = position
        while position < length and script[position] not in ";\n":
            position += 1
        flags = script[flags_start:position].lstrip()
        flag_word = flags.split(maxsplit=1)[0] if flags else ""
        # Numeric occurrence selectors and benign g/p/I/M flags are allowed;
        # e or w/W changes the command into execution or a filesystem write.
        if any(flag in flag_word for flag in ("e", "w", "W")):
            return True
    return False


def _sed_command_after_addresses(fragment: str) -> str:
    """Return the command byte after common GNU/POSIX sed addresses."""
    position = 0
    length = len(fragment)

    def skip_space(index: int) -> int:
        while index < length and fragment[index].isspace():
            index += 1
        return index

    def consume_regex(index: int, delimiter: str) -> int:
        escaped = False
        while index < length:
            char = fragment[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == delimiter:
                return index
        return length

    position = skip_space(position)
    for address_index in range(2):
        before = position
        if position < length and fragment[position].isdigit():
            while position < length and fragment[position].isdigit():
                position += 1
            if position < length and fragment[position] == "~":
                position += 1
                while position < length and fragment[position].isdigit():
                    position += 1
        elif position < length and fragment[position] == "$":
            position += 1
        elif position < length and fragment[position] in {"+", "~"}:
            position += 1
            while position < length and fragment[position].isdigit():
                position += 1
        elif position < length and fragment[position] == "/":
            position = consume_regex(position + 1, "/")
            while position < length and fragment[position] in {"I", "M"}:
                position += 1
        elif position + 1 < length and fragment[position] == "\\":
            delimiter = fragment[position + 1]
            position = consume_regex(position + 2, delimiter)
            while position < length and fragment[position] in {"I", "M"}:
                position += 1
        if position == before:
            break
        position = skip_space(position)
        if address_index == 0 and position < length and fragment[position] == ",":
            position = skip_space(position + 1)
            continue
        break
    while position < length and fragment[position] == "!":
        position = skip_space(position + 1)
    return fragment[position] if position < length else ""


_SAFE_PYTHON_FLAGS: frozenset[str] = frozenset({
    "-B", "-E", "-I", "-O", "-OO", "-P", "-q", "-s", "-S", "-u",
})


def _interpreter_target(command: list[str], executable: str) -> tuple[str, str | None]:
    """Parse a deliberately small, unambiguous direct-interpreter grammar."""
    args = command[1:]
    if executable in {"python", "python3", "py"}:
        index = 0
        while index < len(args) and args[index] in _SAFE_PYTHON_FLAGS:
            index += 1
        if index >= len(args):
            return "none", None
        token = args[index]
        if token == "-m":
            if index + 1 >= len(args) or not args[index + 1] or args[index + 1].startswith("-"):
                raise HardDenyError("Hard security denial: Python -m requires a module name")
            return "module", args[index + 1]
        if token == "--":
            return ("script", args[index + 1]) if index + 1 < len(args) else ("none", None)
        if token.startswith("-"):
            raise HardDenyError(
                f"Hard security denial: unsupported or clustered Python option '{token}'"
            )
        return "script", token

    if not args:
        return "none", None
    token = args[0]
    if executable == "php" and token in {"-f", "--file"}:
        if len(args) < 2:
            raise HardDenyError("Hard security denial: PHP file mode requires a script")
        return "script", args[1]
    if executable == "php" and token.startswith("--file="):
        return "script", token.partition("=")[2]
    if token == "--":
        return ("script", args[1]) if len(args) > 1 else ("none", None)
    if token.startswith("-"):
        raise HardDenyError(
            f"Hard security denial: unsupported or clustered {executable} option '{token}'"
        )
    return "script", token


def _direct_interpreter_script(command: list[str], executable: str) -> str | None:
    mode, target = _interpreter_target(command, executable)
    return target if mode == "script" else None


def _script_file_arguments(command: list[str]) -> list[str]:
    values: list[str] = []
    executable = canonical_executable_name(command[0]) if command else ""
    index = 1
    while index < len(command):
        token = command[index]
        if token in {"-f", "--file", "-E", "--exec"} and index + 1 < len(command):
            values.append(command[index + 1])
            index += 2
            continue
        if token.startswith("--file="):
            values.append(token.partition("=")[2])
        elif token.startswith("--exec="):
            values.append(token.partition("=")[2])
        elif token.startswith("-f") and token != "-f":
            values.append(token[2:])
        elif token.startswith("-E") and token != "-E":
            values.append(token[2:])
        elif executable == "sed" and token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            for position, option in enumerate(cluster):
                if option == "f":
                    attached = cluster[position + 1 :]
                    if attached:
                        values.append(attached)
                    elif index + 1 < len(command):
                        values.append(command[index + 1])
                        index += 1
                    break
                # These options consume the remainder (or next token), so a
                # later 'f' byte is script data/numeric data, not an option.
                if option in {"e", "l"}:
                    break
        index += 1
    return values


def _option_values(
    command: list[str],
    names: set[str],
    *,
    casefold: bool = False,
) -> list[str]:
    """Collect split, equals, and attached single-letter option values."""
    normalized_names = {name.casefold() if casefold else name for name in names}
    values: list[str] = []
    index = 1
    while index < len(command):
        token = command[index]
        comparable = token.casefold() if casefold else token
        flag, separator, value = comparable.partition("=")
        if flag in normalized_names:
            if separator:
                # Slice the original token so path case is preserved.
                values.append(token.partition("=")[2])
                index += 1
                continue
            if index + 1 < len(command):
                values.append(command[index + 1])
                index += 2
                continue
            index += 1
            continue
        matched_attached = False
        for name in normalized_names:
            if len(name) == 2 and name.startswith("-") and comparable.startswith(name) and comparable != name:
                values.append(token[len(name) :])
                matched_attached = True
                break
        index += 1
        if matched_attached:
            continue
    return values


def _option_value(
    command: list[str], names: set[str], *, casefold: bool = False
) -> str | None:
    values = _option_values(command, names, casefold=casefold)
    return values[0] if values else None


def _find_parent_file(start: Path, boundary: Path, name: str) -> Path | None:
    current = start.resolve()
    boundary = boundary.resolve()
    while True:
        candidate = current / name
        if candidate.is_file():
            return candidate.resolve()
        if current == boundary:
            return None
        if boundary not in current.parents:
            return None
        current = current.parent


def _risk_rank(risk: RiskLevel) -> int:
    return {RiskLevel.SAFE: 0, RiskLevel.MODERATE: 1, RiskLevel.DANGEROUS: 2}[risk]


def _unix_resource_limits(limits: ResourceLimits) -> None:
    """Resource limits for child processes on Unix.

    Called as preexec_fn — runs in the child process before exec.
    RLIMIT_NPROC counts processes per user, so it is a fork-bomb brake,
    not a per-command process cap.
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.address_space_bytes, limits.address_space_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NPROC, (limits.nproc, limits.nproc))
        resource.setrlimit(resource.RLIMIT_FSIZE, (limits.fsize_bytes, limits.fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.nofile, limits.nofile))
    except ImportError:
        pass
    except Exception:
        import sys
        print(
            "[TerminalExecutor] Failed to set resource limits",
            file=sys.stderr,
        )


def _suggest_allow_pattern(command: list[str]) -> str:
    """Derive the allow-rule pattern shown in the "always allow" prompt."""
    if len(command) >= 2 and not command[1].startswith("-"):
        return f"{command[0]} {command[1]}"
    return f"{command[0]} *"


def request_user_confirmation(title: str, body: str, question: str) -> bool:
    """Generic Human-in-the-Loop confirmation with Live-TUI pause.

    Shared by the terminal layer and the file-write approval gate in
    SafeExecutor. Raises TerminalError when stdin is not a TTY so
    non-interactive callers get a deterministic failure.
    """
    if not sys.stdin.isatty():
        raise TerminalError("Interactive confirmation required, but stdin is not a TTY")

    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    if TerminalExecutor._hitl_suspend_fn is not None:
        TerminalExecutor._hitl_suspend_fn()
    _enter_hitl()
    try:
        console = Console()
        with _alt_screen(console):
            console.print(
                Panel(
                    Text(sanitize_display_text(body)),
                    border_style="yellow",
                    title=Text(sanitize_display_text(title)),
                    title_align="left",
                    padding=(1, 2),
                )
            )
            try:
                safe_question = sanitize_display_text(question)
                choice = input(f"{safe_question} [y/N]: ").strip().lower()
            except EOFError:
                return False
        return choice in ("y", "yes")
    finally:
        _exit_hitl()
        if TerminalExecutor._hitl_resume_fn is not None:
            TerminalExecutor._hitl_resume_fn()
