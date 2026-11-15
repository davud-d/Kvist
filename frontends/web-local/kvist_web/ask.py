# -*- coding: utf-8 -*-
"""Ask the owner a yes/no question in a window of the operating system.

The web controller may run with no terminal and no frontend of its own (the core starts it in the
background), so the question goes to the desktop: zenity, yad or kdialog on Linux, a dialog from
AppleScript on macOS, a message box from PowerShell on Windows.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

_LOG = logging.getLogger("kvist.web.ask")


def _linux_command(title: str, message: str, timeout: int, which: Callable[[str], str | None], env: dict[str, str]) -> list[str] | None:
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return None
    if which("zenity"):
        return ["zenity", "--question", "--no-markup", "--title", title, "--text", message,
                "--ok-label", "Allow", "--cancel-label", "Deny", "--timeout", str(timeout), "--width", "460"]
    if which("yad"):
        return ["yad", "--no-markup", "--center", "--on-top", "--title", title, "--text", message,
                "--button=Deny:1", "--button=Allow:0", "--timeout", str(timeout)]
    if which("kdialog"):
        return ["kdialog", "--title", title, "--warningyesno", message, "--yes-label", "Allow", "--no-label", "Deny"]
    return None


def _applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _macos_command(title: str, message: str, timeout: int) -> list[str]:
    script = (
        f'display dialog "{_applescript(message)}" with title "{_applescript(title)}" '
        f'buttons {{"Deny", "Allow"}} default button "Deny" cancel button "Deny" with icon caution giving up after {timeout}'
    )
    return ["osascript", "-e", script]


# The text goes through the environment, never into the script: no quoting to get wrong.
_WINDOWS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true}; "
    "[System.Windows.Forms.MessageBox]::Show($owner, $env:KVIST_ASK_MESSAGE, $env:KVIST_ASK_TITLE, 'YesNo', 'Warning', 'Button2')"
)


def ask(
    title: str,
    message: str,
    *,
    timeout: int = 60,
    platform_name: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    env: dict[str, str] | None = None,
) -> bool | None:
    """True if the owner allowed, False if they denied or did not answer in time, None if there is no way to ask."""
    env = dict(os.environ if env is None else env)
    extra: dict[str, object] = {}
    if platform_name == "darwin":
        argv = _macos_command(title, message, timeout)
    elif platform_name == "win32":
        env["KVIST_ASK_TITLE"], env["KVIST_ASK_MESSAGE"] = title, message
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_SCRIPT]
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        argv = _linux_command(title, message, timeout, which, env)
    if argv is None:
        return None
    try:
        done = run(argv, env=env, capture_output=True, text=True, timeout=timeout + 5, **extra)
    except subprocess.TimeoutExpired:
        return False
    except OSError as error:
        _LOG.warning("cannot show the question: %s", error, extra={"component": "ask"})
        return None
    return _allowed(platform_name, done)


def _allowed(platform_name: str, done: subprocess.CompletedProcess) -> bool:
    output = done.stdout or ""
    if platform_name == "darwin":
        return done.returncode == 0 and "button returned:Allow" in output and "gave up:true" not in output
    if platform_name == "win32":
        return output.strip() == "Yes"
    return done.returncode == 0
