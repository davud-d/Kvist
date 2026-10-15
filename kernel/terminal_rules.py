# -*- coding: utf-8 -*-
"""Static command rules: forbidden executables and operators, secret-token patterns, per-profile allowlists."""
from __future__ import annotations

import re


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

# ---------------------------------------------------------------------------
# Allowed commands: common and platform-specific
# Value is a set of permitted subcommands; None = any arguments.
# ---------------------------------------------------------------------------
_COMMON_ALLOWED: dict[str, set[str] | None] = {
    "git": {
        "status", "diff", "rev-parse", "branch", "log",
        "show", "tag", "remote", "stash", "ls-files",
        "rev-list", "shortlog", "ls-tree", "merge-base", "show-ref",
    },
}

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
