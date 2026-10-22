# -*- coding: utf-8 -*-
"""Put the Kvist icon inside the user's status bar (Waybar, Polybar).

A bar cannot be drawn over by another program on Wayland, so the icon is a
module of the bar itself: a script that prints the state and a click command.
Both call this core (``--tray-op bar-stream`` / ``toggle``); the bar's own
config gets one module, placed after the first entry of its left side.

Every edit is idempotent. A config that already names the module is never
touched again, however many cores start, and the first edit of each file keeps
a ``.kvist-backup`` copy. ``uninstall`` removes exactly what ``install`` added.
Bars that are not Waybar or Polybar are left alone; the tray daemon then shows
a system tray icon instead.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from kernel.tray.tray_link import core_command
from kernel.tray.tray_wire import COLORS as _COLORS
from kernel.tray.tray_wire import GLYPH, render_state

MODULE = "custom/kvist"
_WAYBAR_STYLE = f"""
/* kvist: icon state colors */
#custom-kvist {{ padding: 0 10px; color: {_COLORS["idle"]}; }}
#custom-kvist.running {{ color: {_COLORS["running"]}; }}
#custom-kvist.done {{ color: {_COLORS["done"]}; }}
#custom-kvist.hidden {{ padding: 0; margin: 0; }}
"""
BACKUP_SUFFIX = ".kvist-backup"
_LOCK_NAME = "tray_bar.lock"


def bar_command() -> str:
    """The shell prefix the bar's exec and click commands share; ``--tray-op`` is appended by the caller's text."""
    return shlex.join([*core_command(), "--tray-op"])


def _waybar_files(home: Path) -> tuple[Path, Path] | None:
    folder = home / ".config" / "waybar"
    for name in ("config", "config.jsonc"):
        if (folder / name).is_file():
            return folder / name, folder / "style.css"
    return None


def _polybar_file(home: Path) -> Path | None:
    for name in ("config.ini", "config"):
        path = home / ".config" / "polybar" / name
        if path.is_file():
            return path
    return None


def waybar_member(command: str) -> str:
    stream = json.dumps(f"{command} bar-stream --bar waybar")
    toggle = json.dumps(f"{command} toggle")
    return (
        f'"{MODULE}": {{\n'
        f'        "exec": {stream},\n'
        '        "return-type": "json",\n'
        '        "format": "{}",\n'
        f'        "on-click": {toggle},\n'
        '        "tooltip": true\n'
        "    }"
    )


def add_to_waybar(text: str, command: str) -> str | None:
    """Return the config with the module added after the first left entry, or None if there is nothing to do."""
    if f'"{MODULE}"' in text or text.lstrip().startswith("["):
        return None
    opening = re.search(r'"modules-left"\s*:\s*\[', text)
    if opening is None:
        return None
    start = opening.end()
    end = _array_end(text, start)
    body = text[start:end]
    first = re.search(r'"(?:[^"\\]|\\.)*"', body)
    if first is None:
        text = text[:start] + f'"{MODULE}"' + text[end:]
    else:
        indent = re.search(r"\n([ \t]*)\"", body)
        gap = f"\n{indent.group(1)}" if indent else " "
        cut = start + first.end()
        text = text[:cut] + f',{gap}"{MODULE}"' + text[cut:]
    return _append_member(text, waybar_member(command))


def _array_end(text: str, start: int) -> int:
    index = start
    while index < len(text):
        char = text[index]
        if char == '"':
            index = _string_end(text, index)
        elif text.startswith("//", index):
            index = text.find("\n", index)
        elif text.startswith("/*", index):
            index = text.find("*/", index) + 2
        elif char == "]":
            return index
        index += 1
    raise ValueError("Unterminated modules-left array.")


def _string_end(text: str, quote: int) -> int:
    index = quote + 1
    while text[index] != '"':
        index += 2 if text[index] == "\\" else 1
    return index


def _append_member(text: str, member: str) -> str:
    close = text.rstrip().rfind("}")
    before = text[:close].rstrip()
    comma = "" if before.endswith(("{", ",")) else ","
    return f"{before}{comma}\n    {member}\n{text[close:]}"


def remove_from_waybar(text: str) -> str:
    text = re.sub(r',?\s*"custom/kvist"(?=\s*[,\]])', "", text, count=1)
    member = re.search(r',?\s*"custom/kvist"\s*:\s*\{', text)
    if member is None:
        return text
    return text[: member.start()] + text[_object_end(text, member.end()) + 1 :]


def _object_end(text: str, start: int) -> int:
    depth = 1
    index = start
    while depth:
        char = text[index]
        if char == '"':
            index = _string_end(text, index)
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return index - 1


def add_to_polybar(text: str, command: str) -> str | None:
    if "[module/kvist]" in text:
        return None
    lines = text.split("\n")
    for number, line in enumerate(lines):
        match = re.match(r"(\s*modules-left\s*=\s*)(\S.*)$", line)
        if match:
            names = match.group(2).split()
            lines[number] = f"{match.group(1)}{' '.join([names[0], 'kvist', *names[1:]])}"
            break
    else:
        return None
    module = (
        "\n[module/kvist]\ntype = custom/script\n"
        f"exec = {command} bar-stream --bar polybar\ntail = true\nclick-left = {command} toggle\n"
    )
    return "\n".join(lines).rstrip("\n") + "\n" + module


def remove_from_polybar(text: str) -> str:
    text = re.sub(r"^(\s*modules-left\s*=.*?)\s+kvist\b", r"\1", text, count=1, flags=re.M)
    return re.sub(r"\n\[module/kvist\]\n(?:(?!\[).*\n?)*", "\n", text)


def _rewrite(path: Path, change: Callable[[str], str | None]) -> bool:
    """Apply ``change`` to a file once, keeping the first version as a backup; False if nothing changed."""
    original = path.read_text(encoding="utf-8")
    updated = change(original)
    if updated is None or updated == original:
        return False
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    temporary = path.with_name(path.name + ".kvist-tmp")
    temporary.write_text(updated, encoding="utf-8")
    os.replace(temporary, path)
    return True


def _running(name: str) -> bool:
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def _reload(name: str) -> None:
    if not _running(name):
        return
    if name == "waybar":
        subprocess.run(["pkill", "-SIGUSR2", "-x", "waybar"], check=False)
    else:
        subprocess.run(["polybar-msg", "cmd", "restart"], check=False, capture_output=True)


def _locked(home: Path):
    # Imported here because the module is imported by ``main`` on every platform and Windows has no
    # fcntl; the only caller, ``_apply``, returns before this on win32.
    import fcntl

    folder = home / ".kvist"
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / _LOCK_NAME, "w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def install(
    home: Path | None = None,
    *,
    command: str | None = None,
    reload: Callable[[str], None] = _reload,
) -> list[str]:
    """Add the module to every Waybar/Polybar config that lacks it; return the bars changed."""
    return _apply(home, command, reload, adding=True)


def uninstall(
    home: Path | None = None,
    *,
    reload: Callable[[str], None] = _reload,
) -> list[str]:
    """Remove the module from every config that has it; return the bars changed."""
    return _apply(home, "", reload, adding=False)


def _apply(home: Path | None, command: str | None, reload: Callable[[str], None], *, adding: bool) -> list[str]:
    if sys.platform == "win32":
        return []
    home = home or Path.home()
    command = command if command is not None else bar_command()
    changed: list[str] = []
    with _locked(home) as _:
        waybar = _waybar_files(home)
        if waybar is not None:
            config, style = waybar
            edit = (lambda t: add_to_waybar(t, command)) if adding else remove_from_waybar
            if _rewrite(config, edit):
                changed.append("waybar")
                if adding and style.is_file():
                    _rewrite(style, lambda css: None if "#custom-kvist" in css else css.rstrip("\n") + "\n" + _WAYBAR_STYLE)
        polybar = _polybar_file(home)
        if polybar is not None:
            edit = (lambda t: add_to_polybar(t, command)) if adding else remove_from_polybar
            if _rewrite(polybar, edit):
                changed.append("polybar")
    for name in changed:
        reload(name)
    return changed


def running_integrated_bars(home: Path | None = None, *, running: Callable[[str], bool] = _running) -> list[str]:
    """Bars that are running now and carry the module; the tray icon is redundant while this is not empty."""
    home = home or Path.home()
    found = []
    waybar = _waybar_files(home)
    if waybar is not None and f'"{MODULE}"' in waybar[0].read_text(encoding="utf-8") and running("waybar"):
        found.append("waybar")
    polybar = _polybar_file(home)
    if polybar is not None and "[module/kvist]" in polybar.read_text(encoding="utf-8") and running("polybar"):
        found.append("polybar")
    return found
