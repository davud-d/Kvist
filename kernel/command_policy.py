# -*- coding: utf-8 -*-
"""Policy layer for shell command risk classification.

Analyzes commands at the subcommand and argument level, not just
the first word. Takes agent role into account during assessment.

Examples:
    git status       → SAFE
    git diff         → SAFE
    git reset --hard → DANGEROUS
    docker ps        → MODERATE (host daemon boundary)
    docker compose up → DANGEROUS for every role
"""
from __future__ import annotations

import re
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
        "branch": RiskLevel.MODERATE,
        "log": RiskLevel.SAFE,
        "show": RiskLevel.SAFE,
        "tag": RiskLevel.MODERATE,
        "remote": RiskLevel.MODERATE,
        "stash": RiskLevel.MODERATE,
        "ls-files": RiskLevel.SAFE,
        "rev-parse": RiskLevel.SAFE,
        "add": RiskLevel.MODERATE,
        "commit": RiskLevel.MODERATE,
        "push": RiskLevel.DANGEROUS,
        "pull": RiskLevel.MODERATE,
        "merge": RiskLevel.MODERATE,
        "fetch": RiskLevel.MODERATE,
        "clone": RiskLevel.MODERATE,
        "checkout": RiskLevel.MODERATE,
        "reset": RiskLevel.DANGEROUS,
        "clean": RiskLevel.DANGEROUS,
        "rebase": RiskLevel.DANGEROUS,
        "force-push": RiskLevel.DANGEROUS,
    },
    "hostnamectl": {
        "status": RiskLevel.SAFE,
        "set-hostname": RiskLevel.DANGEROUS,
        "set-icon-name": RiskLevel.DANGEROUS,
        "set-chassis": RiskLevel.DANGEROUS,
        "set-deployment": RiskLevel.DANGEROUS,
        "set-location": RiskLevel.DANGEROUS,
    },
    "docker": {
        "ps": RiskLevel.MODERATE,
        "images": RiskLevel.MODERATE,
        "logs": RiskLevel.MODERATE,
        "inspect": RiskLevel.MODERATE,
        "stats": RiskLevel.MODERATE,
        "info": RiskLevel.MODERATE,
        "version": RiskLevel.MODERATE,
        "build": RiskLevel.MODERATE,
        "pull": RiskLevel.MODERATE,
        "push": RiskLevel.DANGEROUS,
        "tag": RiskLevel.MODERATE,
        "run": RiskLevel.MODERATE,
        "create": RiskLevel.MODERATE,
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
        "update": RiskLevel.DANGEROUS,
        "upgrade": RiskLevel.DANGEROUS,
        "autoremove": RiskLevel.DANGEROUS,
    },
}

# ── Commands with unconditional risk level ──────────────────────────

_ALWAYS_SAFE: frozenset[str] = frozenset({
    "ls", "dir", "pwd", "cat", "head", "tail", "echo", "whoami",
    "which", "where", "file", "uname", "hostname", "wc",
    "grep", "sort", "uniq", "diff", "test",
    "tree", "ver", "findstr", "type",
})

# find is safe only as a pure search; these predicates turn it into a
# deletion/execution primitive and must never pass silently.
# Public: terminal_validation.py rejects the same tokens at validation time.
FIND_ACTION_TOKENS: frozenset[str] = frozenset({
    "-exec", "-execdir", "-ok", "-okdir", "-delete",
    "-fprint", "-fprintf", "-fprint0", "-fls",
})

# Interpreters and build runners execute arbitrary code from workspace
# files, so running them requires approval (or a project allow-rule).
_INTERPRETER_EXECUTABLES: frozenset[str] = frozenset({
    "python", "python3", "py", "node", "make", "cargo", "go", "awk", "gawk",
    "bash", "sh", "ruby", "perl", "php", "cmake", "ninja",
})

# Version probes are executable-specific and compare the complete argument
# tuple.  A generic token such as ``version`` is unsafe: for example, Node
# treats it as a script path.
VERSION_PROBES: dict[str, frozenset[tuple[str, ...]]] = {
    "python": frozenset({("--version",), ("-V",)}),
    "python3": frozenset({("--version",), ("-V",)}),
    "py": frozenset({("--version",), ("-V",)}),
    "node": frozenset({("--version",), ("-v",)}),
    "git": frozenset({("--version",)}),
    "curl": frozenset({("--version",), ("-V",)}),
    "ssh": frozenset({("-V",)}),
    "dd": frozenset({("--version",)}),
    "go": frozenset({("version",)}),
    "ruby": frozenset({("--version",), ("-v",)}),
    "perl": frozenset({("-v",), ("-V",)}),
    "php": frozenset({("--version",), ("-v",)}),
    "docker": frozenset({("--version",), ("-v",)}),
}

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
    },
}


def canonical_executable_name(token: str) -> str:
    """Return a case-folded basename for policy and validator comparisons."""
    normalized = token.replace("\\", "/").rstrip("/")
    name = normalized.rsplit("/", 1)[-1].casefold()
    if name.endswith(".exe"):
        name = name[:-4]
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", name):
        return "python3" if name.startswith("python3") else "python"
    if name == "nodejs":
        return "node"
    if name in {"gfind", "ggrep", "gsort", "guniq", "gdiff"}:
        return name[1:]
    if name in {"mawk", "nawk"}:
        return "awk"
    if name == "gsed":
        return "sed"
    if name in {"dash", "ash", "zsh", "ksh", "ksh93", "csh", "tcsh", "fish"}:
        return "sh"
    for family in ("ruby", "perl", "php"):
        if re.fullmatch(rf"{family}\d+(?:\.\d+)*", name):
            return family
    return name


def is_bare_executable_token(token: str) -> bool:
    """Return whether *token* can only be resolved through controlled PATH."""
    if not token or token in {".", ".."} or "/" in token or "\\" in token:
        return False
    # ``C:tool.exe`` is drive-relative on Windows even without a separator.
    return re.match(r"^[A-Za-z]:", token) is None


def is_exact_version_probe(command: list[str] | tuple[str, ...]) -> bool:
    """True only for a verified executable-specific complete argv probe."""
    if not command or not is_bare_executable_token(command[0]):
        return False
    executable = canonical_executable_name(command[0])
    return tuple(command[1:]) in VERSION_PROBES.get(executable, frozenset())


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

    executable = canonical_executable_name(command[0])

    # Exact probes are checked before dangerous executable classification so
    # read-only `ssh -V` and `dd --version` remain usable.
    if is_exact_version_probe(command):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.SAFE,
            reason=f"Version check for '{executable}'",
            requires_approval=False,
        )

    if executable == "hostnamectl":
        read_only = _hostnamectl_is_read_only(command[1:])
        risk = RiskLevel.SAFE if read_only else RiskLevel.DANGEROUS
        if risk is RiskLevel.SAFE and not is_bare_executable_token(command[0]):
            risk = RiskLevel.MODERATE
        return CommandAssessment(
            command=command,
            risk=risk,
            reason="hostnamectl query" if read_only else "hostnamectl host administration",
            requires_approval=risk is not RiskLevel.SAFE,
        )

    if executable == "sysctl" and not _sysctl_is_read_only(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="sysctl write/load mode changes host kernel state",
            requires_approval=True,
        )

    if executable == "docker-compose":
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="legacy Docker Compose may create host-integrated workloads",
            requires_approval=True,
        )

    # Unconditionally dangerous commands
    if executable in _ALWAYS_DANGEROUS:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason=f"Command '{executable}' is unconditionally dangerous",
            requires_approval=True,
        )

    if (
        executable in _SUBCOMMAND_RULES
        and executable != "docker"
        and len(command) > 1
        and command[1].startswith("-")
    ):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="options before a structured subcommand fail closed",
            requires_approval=True,
        )

    if executable == "find":
        result = _assess_find(command)
        if result.risk is RiskLevel.SAFE and not is_bare_executable_token(command[0]):
            result.risk = RiskLevel.MODERATE
            result.reason = "Path-qualified executable identity requires confirmation"
            result.requires_approval = True
        return result

    if executable == "sort" and _sort_has_output_or_program_option(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'sort' output/external-program mode is dangerous",
            requires_approval=True,
        )

    if executable == "tree" and _tree_has_output_option(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'tree' output-file mode is dangerous",
            requires_approval=True,
        )

    if executable == "hostname" and not _hostname_is_read_only(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'hostname' mutation is host administration",
            requires_approval=True,
        )

    if executable == "file" and _file_has_compile_option(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'file' magic compilation mode is dangerous",
            requires_approval=True,
        )

    if executable == "diff" and _diff_has_output_option(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'diff' output-file mode is dangerous",
            requires_approval=True,
        )

    if executable == "uniq" and _uniq_has_output_operand(command[1:]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="'uniq' output-file operand is dangerous",
            requires_approval=True,
        )

    git_read_args = _git_read_operation_arguments(command) if executable == "git" else None
    if git_read_args is not None and (
        _has_long_output_option(git_read_args)
        or _git_has_external_helper_option(git_read_args)
        or _git_has_remerge_diff_option(git_read_args)
        or _git_has_unsafe_submodule_option(git_read_args)
        or _git_has_signature_verification_option(git_read_args)
    ):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="Git read operation requests an output file or external helper",
            requires_approval=True,
        )

    if (
        executable == "git"
        and len(command) > 1
        and command[1].casefold() == "status"
        and _git_has_unsafe_submodule_option(command[2:])
    ):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="Git status requests recursive submodule inspection",
            requires_approval=True,
        )

    if (
        executable == "git"
        and len(command) > 1
        and command[1].casefold() in {"branch", "tag"}
        and _git_ref_filter_has_signature_atom(command[2:])
    ):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="Git ref formatting requests signature verification",
            requires_approval=True,
        )

    if (
        executable == "git"
        and len(command) > 1
        and command[1].casefold() == "tag"
        and _git_tag_has_signing_or_verification_option(command[2:])
    ):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason="Git tag operation requests a signing or verification helper",
            requires_approval=True,
        )

    # Unconditionally safe commands
    if executable in _ALWAYS_SAFE and is_bare_executable_token(command[0]):
        return CommandAssessment(
            command=command,
            risk=RiskLevel.SAFE,
            reason=f"Command '{executable}' is safe (read-only)",
            requires_approval=False,
        )

    if executable in _INTERPRETER_EXECUTABLES:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.MODERATE,
            reason=f"'{executable}' executes code from workspace files → moderate",
            requires_approval=True,
        )

    # Subcommand analysis
    subcommand_index = _docker_subcommand_index(command) if executable == "docker" else 1
    subcommand = (
        command[subcommand_index].lstrip("-").casefold()
        if len(command) > subcommand_index
        else ""
    )
    risk = _assess_with_subcommand(executable, subcommand, role)
    if executable == "git" and _git_read_only_form(command):
        risk = RiskLevel.SAFE

    if risk is RiskLevel.SAFE and not is_bare_executable_token(command[0]):
        risk = RiskLevel.MODERATE

    # docker run flags that break container isolation override any
    # role-based downgrade: host mounts / host namespaces are host access.
    if executable == "docker" and _docker_operation_breaks_isolation(
        command, subcommand_index, subcommand
    ):
        risk = RiskLevel.DANGEROUS
    if executable == "docker" and _docker_has_daemon_access_override(
        command, subcommand_index
    ):
        risk = RiskLevel.DANGEROUS
    if executable == "docker" and _docker_has_nested_destructive_verb(command):
        risk = RiskLevel.DANGEROUS

    # Check for dangerous flags (raises risk)
    has_dangerous_flag = any(arg in _DANGEROUS_FLAGS for arg in command[1:])
    if has_dangerous_flag and risk != RiskLevel.DANGEROUS:
        risk = RiskLevel.DANGEROUS

    # Build reason
    reason = _build_reason(executable, subcommand, risk, has_dangerous_flag)

    return CommandAssessment(
        command=command,
        risk=risk,
        reason=reason,
        requires_approval=_requires_approval(risk),
    )


def _assess_find(command: list[str]) -> CommandAssessment:
    """find is read-only unless it carries action predicates."""
    action_tokens = sorted({arg for arg in command[1:] if is_find_action_token(arg)})
    if action_tokens:
        return CommandAssessment(
            command=command,
            risk=RiskLevel.DANGEROUS,
            reason=f"'find' with action predicate(s) {', '.join(action_tokens)} deletes or executes",
            requires_approval=True,
        )
    return CommandAssessment(
        command=command,
        risk=RiskLevel.SAFE,
        reason="'find' without action predicates is safe (read-only)",
        requires_approval=False,
    )


def is_find_action_token(token: str) -> bool:
    """Recognize action predicates conservatively across token variants."""
    normalized = token.casefold().partition("=")[0]
    return normalized in FIND_ACTION_TOKENS


def _sort_has_output_or_program_option(args: list[str]) -> bool:
    for token in args:
        if token.startswith("--"):
            flag = token.partition("=")[0]
            if (
                (flag.startswith("--o") and "--output".startswith(flag))
                or (flag.startswith("--co") and "--compress-program".startswith(flag))
                or (flag.startswith("--t") and "--temporary-directory".startswith(flag))
            ):
                return True
            continue
        if token.startswith("-"):
            for option in token[1:]:
                if option in {"o", "T"}:
                    return True
                if option in {"k", "S", "t"}:
                    break
    return False


def _tree_has_output_option(args: list[str]) -> bool:
    """Recognize tree output-file options, including attached values."""
    for token in args:
        if token in {"--output", "--outfile"} or token.startswith(
            ("--output=", "--outfile=")
        ):
            return True
        if token.startswith("-") and not token.startswith("--"):
            for option in token[1:]:
                if option == "o":
                    return True
                if option in {"L", "P", "I"}:
                    break
    return False


_HOSTNAME_READ_ONLY_FLAGS: frozenset[str] = frozenset({
    "-a", "--alias", "-A", "--all-fqdns", "-d", "--domain",
    "-f", "--fqdn", "--long", "-i", "--ip-address",
    "-I", "--all-ip-addresses", "-s", "--short", "-y", "--yp",
    "--nis", "-V", "--version", "-h", "--help", "-v", "--verbose",
})


def _hostname_is_read_only(args: list[str]) -> bool:
    """hostname is read-only only with no args or exact query/help flags."""
    return not args or all(token in _HOSTNAME_READ_ONLY_FLAGS for token in args)


_HOSTNAMECTL_QUERY_FLAGS: frozenset[str] = frozenset({
    "--static", "--transient", "--pretty", "--no-pager", "--no-legend",
    "--version", "--help", "-h",
})


def _hostnamectl_is_read_only(args: list[str]) -> bool:
    if not args:
        return True
    if all(token in _HOSTNAMECTL_QUERY_FLAGS or token.startswith("--json=") for token in args):
        return True
    command = args[0].casefold()
    if command == "status":
        return all(
            token in _HOSTNAMECTL_QUERY_FLAGS or token.startswith("--json=")
            for token in args[1:]
        )
    return command in {"hostname", "icon-name", "chassis", "deployment", "location"} and len(args) == 1


def _sysctl_is_read_only(args: list[str]) -> bool:
    dangerous_flags = {"-w", "--write", "-p", "--load", "-f", "--system"}
    dangerous_long_flags = {"--write", "--load", "--system"}
    for token in args:
        flag = token.partition("=")[0]
        if flag in dangerous_flags:
            return False
        if flag != "--" and flag.startswith("--") and any(
            target.startswith(flag) for target in dangerous_long_flags
        ):
            return False
        if token.startswith(("-p", "-f")) and token not in {"-p", "-f"}:
            return False
        if "=" in token and not token.startswith("--pattern="):
            return False
    return True


def _file_has_compile_option(args: list[str]) -> bool:
    """Recognize file(1) compile mode, including getopt abbreviations/clusters."""
    for token in args:
        if token.startswith("-") and not token.startswith("--") and "C" in token[1:]:
            return True
        flag = token.partition("=")[0].casefold()
        if flag.startswith("--co") and "--compile".startswith(flag):
            return True
    return False


def _has_long_output_option(args: list[str]) -> bool:
    return any(token.startswith("--out") for token in args)


def _diff_has_output_option(args: list[str]) -> bool:
    return any(token.startswith("--ou") for token in args)


def _git_read_operation_arguments(command: list[str]) -> list[str] | None:
    """Return option args for supported Git read operations with direct syntax."""
    if len(command) < 2 or command[1].startswith("-"):
        return None
    subcommand = command[1].casefold()
    if subcommand in {"diff", "log", "show"}:
        return command[2:]
    if (
        subcommand == "stash"
        and len(command) >= 3
        and command[2].casefold() in {"list", "show"}
    ):
        return command[3:]
    return None


def _git_has_external_helper_option(args: list[str]) -> bool:
    """Detect explicit Git external diff/textconv requests and abbreviations."""
    for token in args:
        flag = token.partition("=")[0].casefold()
        if flag.startswith("--ext") and "--ext-diff".startswith(flag):
            return True
        if flag.startswith("--textc") and "--textconv".startswith(flag):
            return True
        if (
            flag.startswith("--alt")
            and "--alternate-refs".startswith(flag)
        ):
            return True
    return False


def _git_has_remerge_diff_option(args: list[str]) -> bool:
    """Detect historical remerge modes that may invoke custom merge drivers."""
    index = 0
    while index < len(args):
        token = args[index]
        flag = token.partition("=")[0].casefold()
        if (
            flag != "--"
            and flag.startswith("--rem")
            and "--remerge-diff".startswith(flag)
        ):
            return True
        if (
            flag.startswith("--diff-m")
            and "--diff-merges".startswith(flag)
        ):
            if "=" in token:
                value = token.partition("=")[2]
            elif index + 1 < len(args):
                value = args[index + 1]
                index += 1
            else:
                value = ""
            if value.casefold() in {"r", "remerge"}:
                return True
        index += 1
    return False


def _git_has_unsafe_submodule_option(args: list[str]) -> bool:
    """Reject Git diff/status modes that recurse into untrusted submodules."""
    for token in args:
        flag, separator, value = token.partition("=")
        lowered_flag = flag.casefold()
        lowered_value = value.casefold()
        if (
            lowered_flag.startswith("--ignore-su")
            and "--ignore-submodules".startswith(lowered_flag)
        ):
            if separator and lowered_value != "all":
                return True
        if (
            lowered_flag.startswith("--sub")
            and "--submodule".startswith(lowered_flag)
        ):
            if not separator or lowered_value != "short":
                return True
    return False


def _git_has_signature_verification_option(args: list[str]) -> bool:
    """Detect Git read formatting that invokes configured signature verifiers."""
    index = 0
    while index < len(args):
        token = args[index]
        flag = token.partition("=")[0].casefold()
        if flag.startswith("--show-s") and "--show-signature".startswith(flag):
            return True
        value = ""
        is_format = flag.startswith("--for") and "--format".startswith(flag)
        is_pretty = flag.startswith("--pre") and "--pretty".startswith(flag)
        if is_format or is_pretty:
            if "=" in token:
                value = token.partition("=")[2]
            elif is_format and index + 1 < len(args):
                value = args[index + 1]
                index += 1
        if re.search(r"%G[A-Za-z?]", value):
            return True
        if is_pretty and value:
            safe_named = {
                "oneline", "short", "medium", "full", "fuller", "reference",
                "email", "mboxrd", "raw",
            }
            if value.casefold() not in safe_named and not value.startswith(("format:", "tformat:")):
                return True
        index += 1
    return False


def _git_tag_has_signing_or_verification_option(args: list[str]) -> bool:
    """Detect tag modes that invoke configured signing or verification helpers."""
    dangerous_long = {"--verify", "--sign", "--local-user"}
    for token in args:
        flag = token.partition("=")[0].casefold()
        if flag != "--" and flag.startswith("--") and any(
            target.startswith(flag) for target in dangerous_long
        ):
            return True
        if token.startswith("-") and not token.startswith("--") and any(
            option in token[1:] for option in ("s", "u", "v")
        ):
            return True
    return False


def _git_ref_filter_has_signature_atom(args: list[str]) -> bool:
    """Detect branch/tag formatting that invokes configured signature helpers."""
    index = 0
    while index < len(args):
        token = args[index]
        flag = token.partition("=")[0].casefold()
        is_value_option = any(
            flag.startswith(prefix) and option.startswith(flag)
            for prefix, option in (("--for", "--format"), ("--so", "--sort"))
        )
        if not is_value_option:
            index += 1
            continue
        if "=" in token:
            value = token.partition("=")[2]
        elif index + 1 < len(args):
            value = args[index + 1]
            index += 1
        else:
            value = ""
        normalized = value.casefold().lstrip("-")
        if re.search(r"%\(\*?signature", normalized) or normalized.startswith("signature:"):
            return True
        index += 1
    return False


def _uniq_has_output_operand(args: list[str]) -> bool:
    options_with_value = {
        "-f", "--skip-fields", "-s", "--skip-chars", "-w", "--check-chars",
    }
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            positional.extend(args[index + 1 :])
            break
        flag = token.partition("=")[0]
        if flag in options_with_value and "=" not in token and token == flag:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1
    return len(positional) >= 2


_DOCKER_GLOBAL_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({
    "--config", "-c", "--context", "-H", "--host", "-l", "--log-level",
    "--tlscacert", "--tlscert", "--tlskey",
})

_DOCKER_DAEMON_ACCESS_OPTIONS: frozenset[str] = frozenset({
    "--config",
    "--context",
    "-c",
    "--host",
    "-H",
    "--tls",
    "--tlsverify",
    "--tlscacert",
    "--tlscert",
    "--tlskey",
})


def _docker_subcommand_index(command: list[str]) -> int:
    """Locate a Docker subcommand after supported global options."""
    index = 1
    while index < len(command):
        token = command[index]
        flag = token.partition("=")[0]
        if not token.startswith("-"):
            if token.casefold() in {"container", "image", "builder"} and index + 1 < len(command):
                nested = command[index + 1]
                if nested and not nested.startswith("-"):
                    return index + 1
            return index
        if flag in _DOCKER_GLOBAL_OPTIONS_WITH_VALUE and "=" not in token:
            index += 2
        else:
            index += 1
    return index


def _docker_has_daemon_access_override(
    command: list[str],
    subcommand_index: int,
) -> bool:
    """Treat alternate Docker daemons/configuration as a host boundary."""
    for token in command[1:subcommand_index]:
        flag = token.partition("=")[0]
        if flag in _DOCKER_DAEMON_ACCESS_OPTIONS:
            return True
        if token.startswith("-H") and token != "-H":
            return True
        if token.startswith("-c") and token != "-c":
            return True
    return False


def _docker_run_breaks_isolation(args: list[str]) -> bool:
    """True when Docker args mount host paths or request host privileges."""
    mount_flags = {
        "-v", "--volume", "--mount", "--mount-add", "--volumes-from", "--device",
        "--gpus",
    }
    build_host_io_flags = {
        "--build-context", "--secret", "--ssh", "--output", "-o",
        "--cache-from", "--cache-to",
    }
    host_namespace_flags = {
        "--network", "--net", "--pid", "--ipc", "--uts", "--userns", "--cgroupns",
    }
    host_publication_flags = {
        "-p", "-P", "--publish", "--publish-all", "--publish-add",
        "--publish-rm", "--push", "--set",
    }
    for index, token in enumerate(args):
        lowered = token.casefold()
        flag, separator, value = lowered.partition("=")
        if flag in mount_flags:
            return True
        if flag in build_host_io_flags:
            return True
        if lowered.startswith("-o") and not lowered.startswith("--"):
            return True
        if lowered.startswith("-") and not lowered.startswith("--") and "v" in lowered[1:]:
            return True
        if flag == "--privileged" and (not separator or value not in {"0", "false", "no"}):
            return True
        if flag in {"--device-cgroup-rule", "--security-opt"}:
            return True
        if flag == "--cap-add":
            return True
        if flag == "--allow":
            requested = value if separator else (
                args[index + 1].casefold() if index + 1 < len(args) else ""
            )
            if requested:
                return True
        if flag in host_namespace_flags:
            if value == "host":
                return True
            if not separator and index + 1 < len(args) and args[index + 1].casefold() == "host":
                return True
        if flag in host_publication_flags:
            return True
        if token.startswith("-") and not token.startswith("--") and any(
            option in token[1:] for option in ("p", "P")
        ):
            return True
    return False


def _docker_operation_breaks_isolation(
    command: list[str], subcommand_index: int, subcommand: str
) -> bool:
    args = command[subcommand_index + 1 :]
    if subcommand in {"run", "create", "exec", "build"}:
        return _docker_run_breaks_isolation(args)
    if subcommand == "service":
        mutation_verbs = {"create", "update", "rm", "remove", "rollback", "scale"}
        return _docker_run_breaks_isolation(args) or any(
            token.casefold() in mutation_verbs for token in args
        )
    if subcommand == "stack":
        return any(token.casefold() in {"deploy", "up", "rm", "remove"} for token in args)
    if subcommand == "buildx":
        lowered = [token.casefold() for token in args]
        if "bake" in lowered:
            return True
        if any(
            lowered[index] == "imagetools" and lowered[index + 1] == "create"
            for index in range(len(lowered) - 1)
        ):
            return True
        if any(token in {"build", "b"} for token in lowered):
            return _docker_run_breaks_isolation(args)
    return False


def _docker_has_nested_destructive_verb(command: list[str]) -> bool:
    groups = {
        "container", "image", "volume", "network", "system", "builder", "buildx",
        "service", "stack",
    }
    verbs = {"rm", "remove", "prune"}
    lowered = [token.casefold() for token in command[1:]]
    return any(
        lowered[index] in groups and lowered[index + 1] in verbs
        for index in range(len(lowered) - 1)
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

        # Role convenience may only downgrade commands that were not already
        # intrinsically DANGEROUS.  Host administration/publication remains
        # per-execution approval for every role.
        if role == "devops" and executable in _DEVOPS_DOWNGRADES:
            downgraded = _DEVOPS_DOWNGRADES[executable].get(subcommand)
            if downgraded is not None and base_risk is not RiskLevel.DANGEROUS:
                base_risk = downgraded

        return base_risk

    # For executables without subcommand rules
    if executable in _SUBCOMMAND_RULES:
        # Executable is known, but subcommand is unrecognized
        return RiskLevel.MODERATE

    # Completely unknown command
    return RiskLevel.MODERATE


def _git_read_only_form(command: list[str]) -> bool:
    if (
        len(command) >= 4
        and command[1].casefold() == "diff"
        and "--no-ext-diff" in command[2:]
        and "--no-textconv" in command[2:]
    ):
        return True
    if len(command) == 2 and command[1].casefold() in {"branch", "tag", "remote"}:
        return True
    if len(command) == 3 and command[1].casefold() == "remote" and command[2] in {"-v", "--verbose"}:
        return True
    return (
        len(command) >= 3
        and command[1].casefold() == "stash"
        and command[2].casefold() in {"list", "show"}
    )


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


def _requires_approval(risk: RiskLevel) -> bool:
    """SAFE runs silently; MODERATE and DANGEROUS both go to HITL.

    A MODERATE command can skip the prompt only via an explicit allow-rule
    in the project permission policy (resolved by the terminal layer).
    """
    return risk is not RiskLevel.SAFE
