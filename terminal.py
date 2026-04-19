# -*- coding: utf-8 -*-
"""Кроссплатформенный терминальный модуль для безопасного запуска процессов."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TerminalResult:
    """Результат выполнения команды в терминале."""

    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class TerminalError(Exception):
    """Ошибка валидации или запуска команды в терминале."""


# ---------------------------------------------------------------------------
# Исполняемые файлы, запрещённые безусловно
# ---------------------------------------------------------------------------
_FORBIDDEN_EXECUTABLES: frozenset[str] = frozenset({
    # Удаление
    "rm", "rmdir", "del",
    # Привилегии
    "sudo", "su", "runas",
    # Права / владение
    "chmod", "chown", "icacls", "takeown",
    # Разрушительные системные
    "mkfs", "format", "dd",
    "shutdown", "reboot", "poweroff",
    # Сетевые утилиты (могут утекать данные)
    "curl", "wget",
    # Удалённый доступ
    "nc", "ncat", "netcat",
    "ssh", "scp", "sftp", "telnet", "ftp",
    # Реестр Windows
    "reg", "regedit",
    # Управление службами
    "net", "sc",
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
# Shell-операторы, запрещённые как отдельные аргументы
# ---------------------------------------------------------------------------
_FORBIDDEN_OPERATORS: frozenset[str] = frozenset({
    ";", "&&", "||", "|",
    ">", ">>", "<", "<<",
    "&",
})

# ---------------------------------------------------------------------------
# Regex для обнаружения command substitution и опасных конструкций
# внутри аргументов ($(…), `…`, ${…})
# ---------------------------------------------------------------------------
_SUBST_RE = re.compile(r"\$\(|`|\$\{")

# ---------------------------------------------------------------------------
# Разрешённые команды: общие и платформенные
# Значение — набор допустимых подкоманд; None = любые аргументы.
# ---------------------------------------------------------------------------
_COMMON_ALLOWED: dict[str, set[str] | None] = {
    "git": {
        "status", "diff", "rev-parse", "branch", "log",
        "show", "tag", "remote", "stash", "ls-files",
    },
}

_DEV_COMMON_ALLOWED: dict[str, set[str] | None] = {
    "git": None,
    "python": None,
    "python3": None,
    "py": None,
    "pytest": None,
    "pip": None,
    "pip3": None,
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

_DEV_UNIX_ALLOWED: dict[str, set[str] | None] = {
    "bash": None,
    "sh": None,
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

# CMD-встроенные команды (нет отдельного .exe, нужна обёртка cmd /c)
_WINDOWS_CMD_BUILTINS: frozenset[str] = frozenset({
    "dir", "cd", "type", "echo", "set", "ver",
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
    """Кроссплатформенный безопасный запуск процессов внутри workspace.

    Возможности:
    - allowlist команд с платформенными профилями
    - валидация аргументов без ложных срабатываний
    - запрет опасных команд и shell-операторов
    - рабочая директория строго внутри workspace
    - env overrides для дочерних процессов
    - настраиваемый timeout и лимит stdout/stderr
    - ресурсные лимиты на Unix (CPU, память, файлы)
    - CREATE_NO_WINDOW на Windows
    """

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

        # Собираем allowlist: общие + платформенные + пользовательские
        self._allowed = self._build_allowed_commands()
        if extra_allowed:
            self._allowed.update(extra_allowed)

        # Собираем forbidden
        self._forbidden: set[str] = set(_FORBIDDEN_EXECUTABLES)
        if self.profile == "restricted":
            self._forbidden.update(_RESTRICTED_EXTRA_FORBIDDEN)
        if extra_forbidden:
            self._forbidden.update(extra_forbidden)

    @property
    def allowed_commands(self) -> list[str]:
        """Отсортированный список разрешённых команд."""
        return sorted(self._allowed)

    def run(
        self,
        command: list[str],
        cwd: str | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TerminalResult:
        """Выполнить команду безопасно в рамках workspace."""
        self._validate(command)
        work_dir = self._resolve_cwd(cwd)
        effective_timeout = self._clamp_timeout(timeout)
        env = self._build_env(env_overrides)
        actual_command = self._wrap_for_platform(command)

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
        except subprocess.TimeoutExpired:
            return TerminalResult(
                command=command,
                returncode=-1,
                stdout="",
                stderr=f"Таймаут: команда не завершилась за {effective_timeout} сек",
                timed_out=True,
            )
        except FileNotFoundError:
            raise TerminalError(f"Исполняемый файл не найден: {command[0]}")
        except OSError as exc:
            raise TerminalError(f"Ошибка запуска процесса: {exc}")

        return TerminalResult(
            command=command,
            returncode=proc.returncode,
            stdout=self._truncate(proc.stdout or ""),
            stderr=self._truncate(proc.stderr or ""),
            timed_out=False,
        )

    # ------------------------------------------------------------------
    # Валидация
    # ------------------------------------------------------------------

    def _validate(self, command: list[str]) -> None:
        if not command:
            raise TerminalError("Пустая команда")

        executable = command[0].lower()

        if executable in self._forbidden:
            raise TerminalError(f"Команда '{command[0]}' запрещена")

        if executable not in self._allowed:
            raise TerminalError(
                f"Команда '{command[0]}' не разрешена. "
                f"Допустимые: {', '.join(sorted(self._allowed))}"
            )

        if executable in {"bash", "sh", "powershell", "pwsh", "cmd", "cmd.exe"}:
            self._validate_shell_command(command, executable)
        else:
            self._validate_standard_command(command, executable)

        # Лимит суммарной длины
        if sum(len(t) for t in command) > 2000:
            raise TerminalError("Команда слишком длинная")

    def _validate_standard_command(self, command: list[str], executable: str) -> None:
        # Проверка подкоманд (для git и подобных)
        allowed_sub = self._allowed.get(executable)
        if allowed_sub is not None and len(command) > 1:
            sub = command[1].lstrip("-")
            if sub and sub not in allowed_sub:
                raise TerminalError(
                    f"Подкоманда '{command[1]}' не разрешена для '{command[0]}'"
                )

        self._validate_common_tokens(command[1:])
        self._validate_interpreter_inline_modes(command, executable)

    def _validate_shell_command(self, command: list[str], executable: str) -> None:
        if executable in {"bash", "sh"}:
            if len(command) != 2:
                raise TerminalError(f"Для '{command[0]}' разрешён только запуск script file внутри workspace")
            self._validate_script_argument(command[1], executable)
            return

        if executable in {"powershell", "pwsh"}:
            if len(command) < 3:
                raise TerminalError(f"Для '{command[0]}' разрешён только режим -File <script.ps1>")

            script_index: int | None = None
            index = 1
            while index < len(command):
                token = command[index]
                lowered = token.lower()
                if lowered in _POWERSHELL_COMMAND_FLAGS:
                    raise TerminalError(f"Inline-режим '{token}' запрещён для '{command[0]}'")
                if lowered in _POWERSHELL_FILE_FLAGS:
                    script_index = index + 1
                    break
                if lowered in _POWERSHELL_SAFE_FLAGS:
                    index += 1
                    continue
                if lowered in _POWERSHELL_SAFE_FLAGS_WITH_VALUE:
                    if index + 1 >= len(command):
                        raise TerminalError(f"Для '{token}' требуется безопасное значение перед -File")
                    self._validate_common_tokens([command[index + 1]])
                    index += 2
                    continue
                raise TerminalError(f"Для '{command[0]}' разрешён только режим -File <script.ps1>")

            if script_index is None or script_index >= len(command):
                raise TerminalError(f"Для '{command[0]}' не указан script file после -File")

            script = command[script_index]
            self._validate_common_tokens(command[1:script_index])
            self._validate_script_argument(script, executable)
            self._validate_common_tokens(command[script_index + 1 :])
            return

        self._validate_windows_cmd_command(command)

    def _validate_windows_cmd_command(self, command: list[str]) -> None:
        if len(command) < 3 or command[1].lower() != "/c":
            raise TerminalError(f"Для '{command[0]}' разрешён только режим /c <builtin|script>")

        if len(command) != 3:
            raise TerminalError(f"Для '{command[0]}' разрешён только один безопасный аргумент после /c")

        payload = command[2].strip()
        if not payload:
            raise TerminalError("Пустая строка после /c запрещена")
        if _CMD_DANGEROUS_INLINE_RE.search(payload):
            raise TerminalError("Опасные shell-операторы в cmd /c запрещены")
        if _SUBST_RE.search(payload):
            raise TerminalError("Command substitution в cmd /c запрещена")

        if self._looks_like_relative_script(payload, _SCRIPT_EXTENSIONS["cmd"]):
            self._validate_script_argument(payload, "cmd")
            return

        builtin, separator, remainder = payload.partition(" ")
        if builtin.lower() not in _WINDOWS_CMD_BUILTINS:
            raise TerminalError(f"Для '{command[0]}' разрешены только builtin-команды CMD или .bat/.cmd из workspace")
        if not separator:
            return

        if not _WINDOWS_CMD_BUILTIN_RE.fullmatch(remainder.strip()):
            raise TerminalError("Аргументы builtin-команд CMD содержат недопустимые символы")

    def _validate_common_tokens(self, tokens: list[str]) -> None:
        for token in tokens:
            # Только точное совпадение с shell-оператором (не подстрока!)
            if token in _FORBIDDEN_OPERATORS:
                raise TerminalError(f"Shell-оператор '{token}' запрещён")

            # Обнаружение command substitution внутри аргументов: $(…), `…`, ${…}
            if _SUBST_RE.search(token):
                raise TerminalError(
                    "Command substitution ($(), ``, ${}) в аргументах запрещена"
                )

            if self._is_absolute_path(token):
                raise TerminalError("Абсолютные пути запрещены в аргументах")

            # UNC-пути на Windows (\\server\share)
            if token.startswith("\\\\"):
                raise TerminalError("UNC-пути запрещены в аргументах")

            # Path traversal
            segments = [s for s in token.replace("\\", "/").split("/") if s]
            if ".." in segments:
                raise TerminalError("Path traversal (..) запрещён")

    def _validate_interpreter_inline_modes(self, command: list[str], executable: str) -> None:
        if executable in {"python", "python3", "py"} and any(arg == "-c" for arg in command[1:]):
            raise TerminalError(f"Inline-режим '-c' запрещён для '{command[0]}'")
        if executable == "node" and any(arg in {"-e", "--eval"} for arg in command[1:]):
            raise TerminalError("Inline-режим '-e' запрещён для 'node'")

    def _validate_script_argument(self, script: str, executable: str) -> None:
        self._validate_common_tokens([script])
        if not self._looks_like_relative_script(script, _SCRIPT_EXTENSIONS[executable]):
            raise TerminalError(f"Для '{executable}' разрешён только относительный script file внутри workspace")

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
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _is_absolute_path(self, token: str) -> bool:
        """Абсолютный путь: Unix (/..., ~/...) или Windows (C:\\...)."""
        if token.startswith("/") or token.startswith("~"):
            return True
        if len(token) >= 3 and token[1] == ":" and token[2] in ("/", "\\"):
            return True
        return False

    def _resolve_cwd(self, cwd: str | None) -> Path:
        """Определяет рабочую директорию строго внутри workspace."""
        if not cwd:
            return self.workspace

        if self._is_absolute_path(cwd) or cwd.startswith("\\\\"):
            raise TerminalError("Абсолютные пути запрещены для cwd")

        candidate = (self.workspace / cwd).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise TerminalError("Рабочая директория выходит за пределы workspace")

        if not candidate.is_dir():
            raise TerminalError(f"Рабочая директория не найдена: {cwd}")

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
        """Экранирует спецсимволы CMD для безопасной передачи через cmd.exe /c."""
        # Символы, которые CMD интерпретирует как операторы
        _CMD_META = set('()%!^"<>&|')
        if not any(ch in _CMD_META for ch in arg):
            return arg
        # Экранируем каждый метасимвол CMD с помощью ^
        escaped = []
        for ch in arg:
            if ch in _CMD_META:
                escaped.append("^")
            escaped.append(ch)
        return "".join(escaped)

    def _wrap_for_platform(self, command: list[str]) -> list[str]:
        """На Windows оборачивает CMD-встроенные команды через cmd.exe /c."""
        if self._is_windows and command[0].lower() in _WINDOWS_CMD_BUILTINS:
            # Экранируем аргументы, чтобы предотвратить shell-инъекцию через & | && и т.д.
            safe_args = [self._escape_cmd_arg(a) for a in command]
            return ["cmd.exe", "/c"] + safe_args
        return command

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return (
            text[: self.max_output_chars]
            + f"\n[TRUNCATED: показаны первые {self.max_output_chars} из {len(text)} символов]"
        )


def _unix_resource_limits() -> None:
    """Ограничения ресурсов для дочерних процессов на Unix.

    Вызывается как preexec_fn — выполняется в дочернем процессе до exec.
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
    except Exception:
        pass
