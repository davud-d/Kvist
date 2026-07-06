# -*- coding: utf-8 -*-
"""Cross-platform terminal module for safe process execution."""
from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
})

_RESTRICTED_EXTRA_FORBIDDEN: frozenset[str] = frozenset({
    "python", "python3", "py",
    "pytest", "pip", "pip3", "uv",
    "node", "npm", "npx", "pnpm", "yarn",
    "cmake", "ctest", "make",
    "cargo", "go", "java", "javac", "dotnet",
    "powershell", "pwsh", "cmd", "cmd.exe",
    "bash", "sh", "zsh", "fish",
})

# ---------------------------------------------------------------------------
# Shell operators forbidden as standalone arguments
# ---------------------------------------------------------------------------
_FORBIDDEN_OPERATORS: frozenset[str] = frozenset({
    ";", "&&", "||",
    ">", ">>",
    "&",
})

# ---------------------------------------------------------------------------
# Regex for detecting command substitution and dangerous constructs
# inside arguments ($(…), `…`, ${…})
# ---------------------------------------------------------------------------
_SUBST_RE = re.compile(r"\$\(|`|\$\{")

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
    ) -> None:
        self.workspace = workspace.resolve()
        self.timeout = max(1.0, min(timeout, 30.0))
        self.max_output_chars = max(1000, min(max_output_chars, 200_000))
        self._is_windows = os.name == "nt"
        normalized_profile = (profile or "dev").strip().lower()
        if normalized_profile not in _TERMINAL_PROFILES:
            normalized_profile = "dev"
        self.profile = normalized_profile

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
    ) -> TerminalResult:
        """Execute a command safely within workspace."""
        try:
            self._validate(command)
        except TerminalError as validation_error:
            if not self._request_user_approval(command, str(validation_error)):
                return TerminalResult(
                    command=command,
                    returncode=-1,
                    stdout="",
                    stderr=(
                        "Error: Command execution denied by user for security reasons. "
                        "Find an alternative way to solve the task without this command."
                    ),
                    denied=True,
                )
            # User approved — proceed with relaxed validation (skip allowlist check)
            return self._run_unvalidated(command, cwd, env_overrides, timeout)

        return self._run_unvalidated(command, cwd, env_overrides, timeout)

    def _run_unvalidated(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TerminalResult:
        """Execute an already validated (or user-approved) command."""
        work_dir = self._resolve_cwd(cwd)
        effective_timeout = self._clamp_timeout(timeout)
        env = self._build_env(env_overrides)
        actual_command = self._wrap_for_platform(command)

        # Auto-detect pipe usage: if command contains "|", run via sh -c
        if "|" in actual_command:
            actual_command = ["sh", "-c", " ".join(actual_command)]

        kwargs: dict[str, Any] = {
            "cwd": work_dir,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": effective_timeout,
            "shell": False,
            "env": env,
        }

        if self._is_windows:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _unix_resource_limits

        try:
            proc = subprocess.run(actual_command, **kwargs)
        except UnicodeDecodeError:
            # Rare: output contains broken UTF-8 even with errors="replace"
            kwargs["encoding"] = "utf-8"
            kwargs["errors"] = "surrogateescape"
            proc = subprocess.run(actual_command, **kwargs)
            # Decode safely: replace surrogates with replacement chars
            stdout_raw = proc.stdout or ""
            stderr_raw = proc.stderr or ""
            if isinstance(stdout_raw, str):
                stdout_raw = stdout_raw.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            if isinstance(stderr_raw, str):
                stderr_raw = stderr_raw.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
            return TerminalResult(
                command=command,
                returncode=proc.returncode,
                stdout=self._truncate(stdout_raw),
                stderr=self._truncate(stderr_raw),
                timed_out=False,
            )
        except subprocess.TimeoutExpired:
            return TerminalResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"Timeout: command did not complete within {effective_timeout} sec",
                timed_out=True,
            )
        except FileNotFoundError:
            raise TerminalError(f"Executable not found: {command[0]}")
        except OSError as exc:
            raise TerminalError(f"Process launch error: {exc}")

        return TerminalResult(
            command=command,
            returncode=proc.returncode,
            stdout=self._truncate(proc.stdout or ""),
            stderr=self._truncate(proc.stderr or ""),
            timed_out=False,
        )

    @classmethod
    def _request_user_approval(cls, command: list[str], error_message: str) -> bool:
        """Interactive approval prompt (Human-in-the-Loop).

        Displays a warning panel via rich, then prompts for
        y/N via standard input(). After receiving the answer, fully
        clears the panel and input line from the terminal via ANSI escapes.

        Before input:
        - Pauses background Live TUI (via suspend callback)
        - Sets global HITL flag (blocks update_live)

        After input:
        - Clears panel + input line (ANSI \\033[F\\033[K × N)
        - Clears HITL flag
        - Resumes Live TUI (via resume callback)

        If stdin is not a TTY (tests, CI, pipes) — raises the original
        validation error.
        """
        import sys

        if not sys.stdin.isatty():
            raise TerminalError(error_message)

        from rich.console import Console
        from rich.panel import Panel

        # Pause background TUI rendering
        if cls._hitl_suspend_fn is not None:
            cls._hitl_suspend_fn()

        _enter_hitl()
        try:
            console = Console()
            command_str = " ".join(command)

            panel_content = (
                f"[bold red]⚠️  DANGEROUS COMMAND DETECTED[/bold red]\n\n"
                f"[bold]Command:[/bold] [yellow]{command_str}[/yellow]\n"
                f"[bold]Reason:[/bold] {error_message}\n\n"
                f"[dim]This command does not match the security allowlist.\n"
                f"The agent is attempting to run it. Proceed only if you trust this action.[/dim]"
            )
            panel = Panel(
                panel_content,
                border_style="red",
                title="[bold]Security Check — Human-in-the-Loop[/bold]",
                title_align="left",
                padding=(1, 2),
            )
            console.print(panel)

            try:
                choice = input("Allow execution? [y/N]: ").strip().lower()
            except EOFError:
                raise TerminalError(error_message)

            # Clear panel + input line from terminal
            # Panel ~12 lines + 1 input line = 13; 20 is a safe margin
            _HITL_CLEAR_LINES = 20
            for _ in range(_HITL_CLEAR_LINES):
                sys.stdout.write("\033[F\033[K")
            sys.stdout.flush()

            return choice in ("y", "yes")
        finally:
            _exit_hitl()
            # Resume background TUI rendering
            if cls._hitl_resume_fn is not None:
                cls._hitl_resume_fn()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, command: list[str]) -> None:
        if not command:
            raise TerminalError("Empty command")

        executable = command[0].lower()

        if executable in self._forbidden:
            raise TerminalError(f"Command '{command[0]}' is forbidden")

        if executable not in self._allowed:
            raise TerminalError(
                f"Command '{command[0]}' is not allowed. "
                f"Allowed: {', '.join(sorted(self._allowed))}"
            )

        if executable in {"bash", "sh", "powershell", "pwsh", "cmd", "cmd.exe"}:
            self._validate_shell_command(command, executable)
        else:
            self._validate_standard_command(command, executable)

        # Total length limit
        if sum(len(t) for t in command) > 2000:
            raise TerminalError("Command too long")

    def _validate_standard_command(self, command: list[str], executable: str) -> None:
        # Subcommand validation (for git and similar)
        allowed_sub = self._allowed.get(executable)
        if allowed_sub is not None and len(command) > 1:
            sub = command[1].lstrip("-")
            if sub and sub not in allowed_sub:
                raise TerminalError(
                    f"Subcommand '{command[1]}' is not allowed for '{command[0]}'"
                )

        self._validate_common_tokens(command[1:], executable)
        self._validate_interpreter_inline_modes(command, executable)

    def _validate_shell_command(self, command: list[str], executable: str) -> None:
        if executable in {"bash", "sh"}:
            if len(command) != 2:
                raise TerminalError(f"For '{command[0]}' only script file execution within workspace is allowed")
            self._validate_script_argument(command[1], executable)
            return

        if executable in {"powershell", "pwsh"}:
            if len(command) < 3:
                raise TerminalError(f"For '{command[0]}' only -File <script.ps1> mode is allowed")

            script_index: int | None = None
            index = 1
            while index < len(command):
                token = command[index]
                lowered = token.lower()
                if lowered in _POWERSHELL_COMMAND_FLAGS:
                    raise TerminalError(f"Inline mode '{token}' is forbidden for '{command[0]}'")
                if lowered in _POWERSHELL_FILE_FLAGS:
                    script_index = index + 1
                    break
                if lowered in _POWERSHELL_SAFE_FLAGS:
                    index += 1
                    continue
                if lowered in _POWERSHELL_SAFE_FLAGS_WITH_VALUE:
                    if index + 1 >= len(command):
                        raise TerminalError(f"Flag '{token}' requires a safe value before -File")
                    self._validate_common_tokens([command[index + 1]])
                    index += 2
                    continue
                raise TerminalError(f"For '{command[0]}' only -File <script.ps1> mode is allowed")

            if script_index is None or script_index >= len(command):
                raise TerminalError(f"No script file specified after -File for '{command[0]}'")

            if len(command) != script_index + 1:
                raise TerminalError(f"Only one script file is allowed after -File for '{command[0]}'")

            script = command[script_index]
            self._validate_common_tokens(command[1:script_index])
            self._validate_script_argument(script, executable)
            return

        self._validate_windows_cmd_command(command)

    def _validate_windows_cmd_command(self, command: list[str]) -> None:
        if len(command) < 3 or command[1].lower() != "/c":
            raise TerminalError(f"For '{command[0]}' only /c <builtin|script> mode is allowed")

        if len(command) != 3:
            raise TerminalError(f"Only one safe argument is allowed after /c for '{command[0]}'")

        payload = command[2].strip()
        if not payload:
            raise TerminalError("Empty string after /c is forbidden")
        if _CMD_DANGEROUS_INLINE_RE.search(payload):
            raise TerminalError("Dangerous shell operators in cmd /c are forbidden")
        if _SUBST_RE.search(payload):
            raise TerminalError("Command substitution in cmd /c is forbidden")

        if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS["cmd"]):
            self._validate_script_argument(payload, "cmd")
            return

        builtin, separator, remainder = payload.partition(" ")
        if builtin.lower() not in _WINDOWS_CMD_BUILTINS:
            raise TerminalError(f"For '{command[0]}' only CMD builtins or .bat/.cmd from workspace are allowed")
        if not separator:
            return

        remainder_tokens = [token for token in remainder.split() if token]
        if not remainder_tokens:
            return

        if not all(_WINDOWS_CMD_BUILTIN_RE.fullmatch(token) for token in remainder_tokens):
            raise TerminalError("CMD builtin arguments contain invalid characters")

        self._validate_common_tokens(remainder_tokens)

    def _validate_common_tokens(self, tokens: list[str], executable: str = "") -> None:
        for token in tokens:
            # Exact match only with shell operator (not substring!)
            if token in _FORBIDDEN_OPERATORS:
                raise TerminalError(f"Shell operator '{token}' is forbidden")

            # Detect command substitution inside arguments: $(…), `…`, ${…}
            if _SUBST_RE.search(token):
                raise TerminalError(
                    "Command substitution ($(), ``, ${}) in arguments is forbidden"
                )

            if self._is_absolute_path(token) and executable.casefold() not in _ABSOLUTE_PATH_SAFE_COMMANDS:
                raise TerminalError("Absolute paths are forbidden in arguments")

            # UNC paths on Windows (\\server\share)
            if token.startswith("\\\\"):
                raise TerminalError("UNC paths are forbidden in arguments")

            # Path traversal
            segments = [s for s in token.replace("\\", "/").split("/") if s]
            if ".." in segments:
                raise TerminalError("Path traversal (..) is forbidden")

    def _validate_interpreter_inline_modes(self, command: list[str], executable: str) -> None:
        if executable in {"python", "python3", "py"} and any(arg == "-c" for arg in command[1:]):
            raise TerminalError(f"Inline mode '-c' is forbidden for '{command[0]}'")
        if executable == "node" and any(arg in {"-e", "--eval"} for arg in command[1:]):
            raise TerminalError("Inline mode '-e' is forbidden for 'node'")
        if executable == "sed" and any(
            arg == "-i" or arg.startswith("-i") or arg == "--in-place" for arg in command[1:]
        ):
            raise TerminalError("In-place mode 'sed -i' is forbidden: only sed -n (read-only) is allowed")

    def _validate_script_argument(self, script: str, executable: str) -> None:
        self._validate_common_tokens([script])
        if not self._looks_like_relative_script(script, _SCRIPT_EXTENSIONS[executable]):
            raise TerminalError(f"For '{executable}' only relative script file within workspace is allowed")

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
            return self.workspace

        if self._is_absolute_path(cwd) or cwd.startswith("\\\\"):
            raise TerminalError("Absolute paths are forbidden for cwd")

        candidate = (self.workspace / cwd).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise TerminalError("Working directory is outside workspace")

        if not candidate.is_dir():
            raise TerminalError(f"Working directory not found: {cwd}")

        return candidate

    def _clamp_timeout(self, timeout: float | None) -> float:
        if timeout is None:
            return self.timeout
        return max(1.0, min(timeout, 30.0))

    def _build_env(self, overrides: dict[str, str] | None) -> dict[str, str] | None:
        if not overrides:
            return None
        env = dict(os.environ)
        env.update(overrides)
        return env

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

    def _wrap_for_platform(self, command: list[str]) -> list[str]:
        """On Windows wraps CMD built-in commands through cmd.exe /c."""
        if self._is_windows and command[0].lower() in _WINDOWS_CMD_BUILTINS:
            # Escape arguments to prevent shell injection via & | && etc.
            safe_args = [self._escape_cmd_arg(a) for a in command]
            return ["cmd.exe", "/c"] + safe_args
        return command

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[: self.max_output_chars]
            + f"\n[TRUNCATED: showing first {self.max_output_chars} of {len(text)} characters]"
        )


def _unix_resource_limits() -> None:
    """Resource limits for child processes on Unix.

    Called as preexec_fn — runs in the child process before exec.
    """
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (256 * 1024 * 1024, 256 * 1024 * 1024),
        )
        resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except ImportError:
        pass
    except Exception:
        import sys
        print(
            "[TerminalExecutor] Failed to set resource limits",
            file=sys.stderr,
        )
