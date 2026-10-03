"""Select the one native autostart backend for this platform."""
from __future__ import annotations

import platform
import subprocess

from .linux import LinuxInstaller
from .macos import MacOSInstaller
from .windows import WindowsInstaller


def run_checked(command: list[str]) -> None:
    subprocess.run(command, check=True)


def installer_for(command: list[str], *, home=None, runner=run_checked):
    system = platform.system()
    if system == "Linux":
        return LinuxInstaller(command, home=home, runner=runner)
    if system == "Darwin":
        return MacOSInstaller(command, home=home, runner=runner)
    if system == "Windows":
        return WindowsInstaller(command, home=home, runner=runner)
    raise RuntimeError(f"Automatic startup is not supported on {system}.")

