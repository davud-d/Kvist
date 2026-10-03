"""launchd LaunchAgent installer."""
from __future__ import annotations

import plistlib

from .base import AutostartInstaller, InstallResult


class MacOSInstaller(AutostartInstaller):
    label = "ai.kvist.web"

    @property
    def path(self):
        return self.home / "Library" / "LaunchAgents" / f"{self.label}.plist"

    def install(self) -> InstallResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        logs = self.home / ".kvist" / "web" / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": self.label,
            "ProgramArguments": self.service_command(),
            "WorkingDirectory": str(self.home),
            "RunAtLoad": True,
            "KeepAlive": {"Crashed": True},
            "ProcessType": "Background",
            "StandardOutPath": str(logs / "launchd.log"),
            "StandardErrorPath": str(logs / "launchd-error.log"),
        }
        self.path.write_bytes(plistlib.dumps(payload, sort_keys=True))
        # Idempotent: bootout may fail when nothing is loaded, so the CLI runner
        # used in production marks it optional.
        try:
            self.runner(["launchctl", "bootout", f"gui/{__import__('os').getuid()}", str(self.path)])
        except Exception:
            pass
        self.runner(["launchctl", "bootstrap", f"gui/{__import__('os').getuid()}", str(self.path)])
        return InstallResult("installed", str(self.path), "Installed the Kvist LaunchAgent.")

    def uninstall(self) -> InstallResult:
        if self.path.exists():
            try:
                self.runner(["launchctl", "bootout", f"gui/{__import__('os').getuid()}", str(self.path)])
            except Exception:
                pass
            self.path.unlink()
        return InstallResult("uninstalled", str(self.path), "Removed the Kvist LaunchAgent.")
