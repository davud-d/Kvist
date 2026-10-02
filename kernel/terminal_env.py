# -*- coding: utf-8 -*-
"""Child-process environment: allowed and blocked variables, controlled PATH, private-directory checks."""
from __future__ import annotations

import os
import re
from pathlib import Path

from kernel.sandbox import validated_external_tool_path
from kernel.terminal_paths import _is_relative_to
from kernel.terminal_trust import _is_trusted_system_directory
from kernel.terminal_types import HardDenyError


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


def _windows_exec_dirs() -> list[Path]:
    """System directories plus the PATH entries that pass the executable trust check.

    ``os.defpath`` on Windows is ``.;C:\\bin``, which holds no executables. The
    entries of the real PATH are filtered by the same check that trusts an
    executable, so user-writable tool directories stay out as on Unix.
    """
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidates = [system_root / "System32", system_root]
    candidates += [Path(part) for part in os.environ.get("PATH", "").split(os.pathsep) if part]
    trusted: list[Path] = []
    for candidate in candidates:
        if candidate.is_absolute() and _is_trusted_system_directory(candidate) and candidate not in trusted:
            trusted.append(candidate)
    return trusted


def _system_exec_dirs() -> list[Path]:
    if os.name == "nt":
        return _windows_exec_dirs()
    return [Path(p) for p in ("/usr/sbin", "/usr/bin", "/sbin", "/bin") if Path(p).is_dir()]


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
