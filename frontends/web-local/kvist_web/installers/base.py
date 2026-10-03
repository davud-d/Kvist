"""Platform autostart contract for ``kvist-web --install-system``."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


Runner = Callable[[list[str]], None]


@dataclass(slots=True)
class InstallResult:
    action: str
    target: str
    message: str


class AutostartInstaller(ABC):
    def __init__(self, command: list[str], *, home: Path | None = None, runner: Runner) -> None:
        self.command = command
        self.home = home or Path.home()
        self.runner = runner

    def service_command(self) -> list[str]:
        """Stable, non-interactive invocation used by a native launcher.

        Native launchers do not inherit the terminal's current directory. Pin
        both the process working directory (where supported) and Kvist's first
        workspace to the account home rather than accidentally persisting `/`,
        `C:\\Windows\\System32`, or a transient installer directory.
        """
        return [*self.command, "--no-open", "--workspace", str(self.home)]

    @abstractmethod
    def install(self) -> InstallResult: ...

    @abstractmethod
    def uninstall(self) -> InstallResult: ...
