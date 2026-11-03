# -*- coding: utf-8 -*-
"""Keep the daemon alive across the server's reboots: a user service of whatever the system has.

The renderers are pure; ``install`` and ``uninstall`` do the writing and call the manager.
Linux with systemd gets a user unit, macOS a LaunchAgent, anything else a crontab ``@reboot`` line.
"""
from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from kernel.ssh.paths import RemoteError, daemon_id

SYSTEMD = "systemd"
LAUNCHD = "launchd"
CRON = "cron"

_ENVIRONMENT_NAMES = ("KVIST_CONFIG_DIR",)
_COMMAND_SECONDS = 30


@dataclass(slots=True)
class InstallResult:
    manager: str
    path: str
    notes: list[str] = field(default_factory=list)


def daemon_argv(workspace: str) -> list[str]:
    """The command that runs the daemon: this very program, so the service follows an updated binary."""
    if getattr(sys, "frozen", False):
        base = [sys.executable]
    else:
        base = [sys.executable, str(Path(__file__).resolve().parents[1] / "__main__.py")]
    return [*base, "--remote-serve", "--workspace", str(Path(workspace).expanduser().resolve())]


def _environment() -> dict[str, str]:
    return {name: os.environ[name] for name in _ENVIRONMENT_NAMES if os.environ.get(name)}


def _systemd_quote(argument: str) -> str:
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def render_systemd_unit(argv: list[str], workspace: str, environment: dict[str, str]) -> str:
    lines = [
        "[Unit]",
        f"Description=Kvist remote core for {workspace}",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + " ".join(_systemd_quote(item) for item in argv),
        "Restart=on-failure",
        "RestartSec=5",
        # Exit code 2 is a configuration problem (no provider yet); starting again cannot fix it.
        "RestartPreventExitStatus=2",
    ]
    lines += [f"Environment={_systemd_quote(f'{name}={value}')}" for name, value in environment.items()]
    lines += ["", "[Install]", "WantedBy=default.target", ""]
    return "\n".join(lines)


def render_launchd_plist(label: str, argv: list[str], environment: dict[str, str]) -> bytes:
    document: dict = {
        "Label": label,
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
    }
    if environment:
        document["EnvironmentVariables"] = environment
    return plistlib.dumps(document)


def cron_marker(workspace: str) -> str:
    return f"# kvist-remote-{daemon_id(workspace)}"


def render_cron_line(argv: list[str], workspace: str) -> str:
    environment = "".join(f"{name}={shlex.quote(value)} " for name, value in _environment().items())
    return f"@reboot {environment}{shlex.join(argv)} {cron_marker(workspace)}"


def detect_manager() -> str:
    if sys.platform == "darwin":
        return LAUNCHD
    if shutil.which("systemctl") and _run(["systemctl", "--user", "show-environment"]).returncode == 0:
        return SYSTEMD
    if shutil.which("crontab"):
        return CRON
    raise RemoteError("Neither systemd, launchd nor crontab is available here; start the daemon with --remote-op start.")


def _run(command: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=_COMMAND_SECONDS, check=False
    )


def _must(command: list[str], *, stdin: str | None = None) -> str:
    done = _run(command, stdin=stdin)
    if done.returncode:
        raise RemoteError(f"{shlex.join(command)} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


# --- systemd ---------------------------------------------------------------


def _unit_name(workspace: str) -> str:
    return f"kvist-{daemon_id(workspace)}.service"


def _unit_path(workspace: str) -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / _unit_name(workspace)


def _install_systemd(workspace: str) -> InstallResult:
    path = _unit_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_systemd_unit(daemon_argv(workspace), workspace, _environment()), encoding="utf-8")
    _must(["systemctl", "--user", "daemon-reload"])
    _must(["systemctl", "--user", "enable", "--now", _unit_name(workspace)])
    result = InstallResult(SYSTEMD, str(path))
    if not _ensure_linger():
        result.notes.append(
            "The service starts after a reboot only while you are logged in. "
            "Make it start at boot with: sudo loginctl enable-linger " + _user_name()
        )
    return result


def _user_name() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or str(os.getuid())


def _ensure_linger() -> bool:
    if not shutil.which("loginctl"):
        return False
    done = _run(["loginctl", "show-user", _user_name(), "--property=Linger", "--value"])
    if done.stdout.strip() == "yes":
        return True
    return _run(["loginctl", "enable-linger"]).returncode == 0


# --- launchd ---------------------------------------------------------------


def _label(workspace: str) -> str:
    return f"site.kvist.remote.{daemon_id(workspace)}"


def _plist_path(workspace: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{_label(workspace)}.plist"


def _install_launchd(workspace: str) -> InstallResult:
    path = _plist_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_launchd_plist(_label(workspace), daemon_argv(workspace), _environment()))
    domain = f"gui/{os.getuid()}"
    _run(["launchctl", "bootout", f"{domain}/{_label(workspace)}"])
    _must(["launchctl", "bootstrap", domain, str(path)])
    return InstallResult(
        LAUNCHD,
        str(path),
        ["A LaunchAgent starts when you log in to the Mac, not when it boots."],
    )


# --- cron ------------------------------------------------------------------


def _crontab_lines() -> list[str]:
    done = _run(["crontab", "-l"])
    return done.stdout.splitlines() if done.returncode == 0 else []


def _write_crontab(lines: list[str]) -> None:
    _must(["crontab", "-"], stdin="\n".join(lines) + "\n" if lines else "")


def _without_marker(lines: list[str], workspace: str) -> list[str]:
    marker = cron_marker(workspace)
    return [line for line in lines if not line.endswith(marker)]


def _install_cron(workspace: str) -> InstallResult:
    lines = _without_marker(_crontab_lines(), workspace)
    _write_crontab([*lines, render_cron_line(daemon_argv(workspace), workspace)])
    return InstallResult(
        CRON,
        "crontab",
        ["cron starts the daemon at boot but does not restart it if it crashes."],
    )


# --- the interface ---------------------------------------------------------


def install(workspace: str) -> InstallResult:
    manager = detect_manager()
    return {SYSTEMD: _install_systemd, LAUNCHD: _install_launchd, CRON: _install_cron}[manager](workspace)


def uninstall(workspace: str) -> str:
    manager = detect_manager()
    if manager == SYSTEMD:
        _run(["systemctl", "--user", "disable", "--now", _unit_name(workspace)])
        _unit_path(workspace).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
    elif manager == LAUNCHD:
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{_label(workspace)}"])
        _plist_path(workspace).unlink(missing_ok=True)
    else:
        _write_crontab(_without_marker(_crontab_lines(), workspace))
    return manager


def status(workspace: str) -> dict:
    """What a probe reports: which manager would be used and whether it holds this workspace's service."""
    try:
        manager = detect_manager()
    except RemoteError:
        return {"manager": None, "installed": False}
    if manager == SYSTEMD:
        installed = _unit_path(workspace).exists()
        active = _run(["systemctl", "--user", "is-active", _unit_name(workspace)]).stdout.strip() == "active"
        return {"manager": manager, "installed": installed, "active": active, "linger": _lingers_quietly()}
    if manager == LAUNCHD:
        return {"manager": manager, "installed": _plist_path(workspace).exists()}
    return {"manager": manager, "installed": any(line.endswith(cron_marker(workspace)) for line in _crontab_lines())}


def _lingers_quietly() -> bool:
    """Linger state without trying to change it (a probe must not touch the system)."""
    if not shutil.which("loginctl"):
        return False
    done = _run(["loginctl", "show-user", _user_name(), "--property=Linger", "--value"])
    return done.stdout.strip() == "yes"
