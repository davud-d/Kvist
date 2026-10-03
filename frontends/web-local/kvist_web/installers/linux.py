"""systemd user-service installer."""
from __future__ import annotations

import shlex

from .base import AutostartInstaller, InstallResult


class LinuxInstaller(AutostartInstaller):
    @property
    def path(self):
        return self.home / ".config" / "systemd" / "user" / "kvist-web.service"

    def install(self) -> InstallResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        command = " ".join(shlex.quote(part) for part in self.service_command())
        working_directory = shlex.quote(str(self.home))
        content = (
            "# Installed by Kvist. Managed by kvist-web --uninstall-system.\n"
            "[Unit]\nDescription=Kvist local web controller\nAfter=network.target\n\n"
            "[Service]\nType=simple\n"
            f"ExecStart={command}\n"
            f"WorkingDirectory={working_directory}\n"
            "Restart=on-failure\nRestartSec=3\n"
            "Environment=PYTHONUNBUFFERED=1\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        self.path.write_text(content, encoding="utf-8")
        self.runner(["systemctl", "--user", "daemon-reload"])
        self.runner(["systemctl", "--user", "enable", "--now", "kvist-web.service"])
        return InstallResult(
            "installed",
            str(self.path),
            "Installed a systemd user service. It starts at login; linger was not enabled.",
        )

    def uninstall(self) -> InstallResult:
        if self.path.exists():
            self.runner(["systemctl", "--user", "disable", "--now", "kvist-web.service"])
            self.path.unlink()
            self.runner(["systemctl", "--user", "daemon-reload"])
        return InstallResult("uninstalled", str(self.path), "Removed the Kvist user service.")
