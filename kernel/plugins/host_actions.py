# -*- coding: utf-8 -*-
"""Actions on the person's own desktop, run by the core outside every skill sandbox.

The bundled ``desktop`` skill reaches them as core tools; the capability grant is the
person's consent. The set is closed on purpose: open an http(s) address in the default
browser, start an installed application by name, and list, focus or close windows.
There is no way to run an arbitrary command, type or click here. Tabs of a running
browser are not reachable: a browser exposes them only on a debugging port the person
has to start it with.

Applications and windows have no common API, so each system has one backend:
desktop entries and ``gtk-launch`` on Linux; macOS app bundles with ``open -a`` and
System Events scripting; Start Menu shortcuts and PowerShell on Windows. Linux windows
go through the compositor's own command: Hyprland ``hyprctl``, Sway ``swaymsg``, niri
``niri msg``, i3 ``i3-msg``. GNOME on Wayland has no window interface at all, so it needs
the Window Calls extension (D-Bus); KDE Plasma needs ``kdotool``; any other X11 window
manager works through ``wmctrl``. Anything else is reported as unavailable with what to
install. The macOS, Windows, GNOME, KDE and niri backends are covered by unit tests with
faked commands only.
"""
from __future__ import annotations

import ast
import configparser
import json
import os
import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import urlsplit

from kernel.plugins.host import ServiceError

_COMMAND_TIMEOUT_SECONDS = 10
_MAX_AMBIGUOUS_SHOWN = 8


def open_url(url: str) -> None:
    """Open ``url`` in a new tab of the person's default browser."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ServiceError("bad_request", "not a valid address") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ServiceError("bad_request", "only http and https addresses with a host can be opened")
    if parts.username or parts.password:
        raise ServiceError("bad_request", "an address with a login inside it is not opened")
    if not webbrowser.open(url, new=2):
        raise ServiceError("unavailable", "no default browser could be started")


# --- applications ------------------------------------------------------------


def installed_apps() -> dict[str, str]:
    """Application id -> display name for every application the person could start from a menu."""
    if sys.platform == "darwin":
        return _mac_apps()
    if sys.platform == "win32":
        return _windows_apps()
    return _linux_apps()


def resolve_app(query: str, apps: dict[str, str]) -> tuple[str, str]:
    """(application id, name) for the one application ``query`` names, or ServiceError.

    ``id:<id>`` names an application exactly, which is how two entries with one name are told apart.
    """
    if query.startswith("id:"):
        app_id = query[3:].strip()
        if app_id not in apps:
            raise ServiceError("not_found", f"no installed application has the id {app_id!r}")
        return app_id, apps[app_id]
    needle = query.strip().lower()
    exact = [item for item in apps.items() if needle in (item[0].lower(), item[1].lower())]
    found = exact or [item for item in apps.items() if needle in item[0].lower() or needle in item[1].lower()]
    if len(found) == 1:
        return found[0]
    if found:
        shown = ", ".join(f"{name} [{app_id}]" for app_id, name in sorted(found)[:_MAX_AMBIGUOUS_SHOWN])
        raise ServiceError("ambiguous", f"{query!r} matches several applications: {shown}. "
                           "Call again with name \"id:<the id in brackets>\".")
    raise ServiceError("not_found", f"no installed application matches {query!r}")


def open_app(query: str) -> str:
    """Start the installed application ``query`` names; returns its display name."""
    app_id, name = resolve_app(query, installed_apps())
    if sys.platform == "darwin":
        _run(["open", "-a", app_id])
    elif sys.platform == "win32":
        _windows_start(app_id)
    else:
        launcher = shutil.which("gtk-launch")
        if launcher is None:
            raise ServiceError("unavailable", "starting applications needs gtk-launch")
        # gtk-launch appends ".desktop" only when it is missing, and "org.telegram.desktop" already ends with it.
        _run([launcher, f"{app_id}.desktop"])
    return name


def _linux_apps() -> dict[str, str]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    data_dirs = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    apps: dict[str, str] = {}
    for root in [data_home, *(Path(item) for item in data_dirs if item)]:
        base = root / "applications"
        for path in sorted(base.rglob("*.desktop")):
            app_id = path.relative_to(base).with_suffix("").as_posix().replace("/", "-")
            name = _visible_name(path)
            if name and app_id not in apps:
                apps[app_id] = name
    return apps


def _visible_name(path: Path) -> str:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, configparser.Error):
        # An unreadable or malformed entry is not an application the person can start from a menu either.
        return ""
    entry = parser["Desktop Entry"] if parser.has_section("Desktop Entry") else {}
    hidden = entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true"
    if entry.get("Type") != "Application" or hidden:
        return ""
    return entry.get("Name", "").strip()


def _mac_apps() -> dict[str, str]:
    roots = [Path("/Applications"), Path("/Applications/Utilities"), Path("/System/Applications"),
             Path("/System/Applications/Utilities"), Path.home() / "Applications"]
    return {path.stem: path.stem for root in roots if root.is_dir() for path in sorted(root.glob("*.app"))}


def _start_menu_roots() -> list[Path]:
    return [Path(os.environ[name]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            for name in ("PROGRAMDATA", "APPDATA") if os.environ.get(name)]


def _windows_apps() -> dict[str, str]:
    apps: dict[str, str] = {}
    for root in _start_menu_roots():
        for path in sorted(root.rglob("*.lnk")):
            apps.setdefault(path.relative_to(root).with_suffix("").as_posix(), path.stem)
    return apps


def _windows_start(app_id: str) -> None:
    for root in _start_menu_roots():
        shortcut = root / f"{app_id}.lnk"
        if shortcut.is_file():
            os.startfile(shortcut)  # type: ignore[attr-defined]  # exists on Windows only
            return
    raise ServiceError("gone", f"the Start Menu shortcut {app_id!r} is no longer there")


# --- windows -----------------------------------------------------------------


class _Backend(NamedTuple):
    windows: Callable[[], list[dict[str, object]]]
    focus: Callable[[str], None]
    close: Callable[[str], None]


def windows() -> list[dict[str, object]]:
    """The open windows: {id, app, title, workspace, focused}."""
    return _backend().windows()


def window(window_id: str) -> dict[str, object]:
    """The open window ``window_id`` names, or ServiceError. Only ids from this list reach a command."""
    for item in windows():
        if item["id"] == window_id:
            return item
    raise ServiceError("gone", f"window {window_id} is no longer open; list the windows again")


def focus_window(window_id: str) -> None:
    window(window_id)
    _backend().focus(window_id)


def close_window(window_id: str) -> None:
    window(window_id)
    _backend().close(window_id)


def _backend() -> _Backend:
    if sys.platform == "darwin":
        return _Backend(_mac_windows, _mac_focus, _mac_close)
    if sys.platform == "win32":
        return _Backend(_windows_windows, _windows_focus, _windows_close)
    environment = os.environ
    desktop = environment.get("XDG_CURRENT_DESKTOP", "").lower()
    candidates = (
        (environment.get("HYPRLAND_INSTANCE_SIGNATURE"), "hyprctl", _Backend(_hypr_windows, _hypr_focus, _hypr_close)),
        (environment.get("SWAYSOCK"), "swaymsg", _Backend(_sway_windows, _sway_focus, _sway_close)),
        (environment.get("NIRI_SOCKET"), "niri", _Backend(_niri_windows, _niri_focus, _niri_close)),
        (environment.get("I3SOCK") or "i3" in desktop, "i3-msg", _Backend(_i3_windows, _i3_focus, _i3_close)),
        ("gnome" in desktop, "gdbus", _Backend(_gnome_windows, _gnome_focus, _gnome_close)),
        ("kde" in desktop, "kdotool", _Backend(_kde_windows, _kde_focus, _kde_close)),
        (environment.get("DISPLAY") and environment.get("XDG_SESSION_TYPE") != "wayland", "wmctrl",
         _Backend(_wmctrl_windows, _wmctrl_focus, _wmctrl_close)),
    )
    for present, program, backend in candidates:
        if present and shutil.which(program):
            return backend
    raise ServiceError("unavailable", _no_backend_hint(desktop, environment.get("XDG_SESSION_TYPE", "")))


def _no_backend_hint(desktop: str, session: str) -> str:
    if "kde" in desktop:
        return "KDE Plasma has no window command of its own: install kdotool to let Kvist see and close windows"
    if "gnome" in desktop:
        return "GNOME has no window interface of its own: gdbus is missing, and the Window Calls extension is needed"
    if session == "wayland":
        return ("this Wayland compositor has no known window interface (Hyprland, Sway, niri, GNOME with the "
                "Window Calls extension and KDE with kdotool are supported)")
    return "window control on this system needs wmctrl (X11), or one of: i3, Sway, Hyprland, niri"


def _hypr_windows() -> list[dict[str, object]]:
    clients = json.loads(_run(["hyprctl", "clients", "-j"]))
    return [{"id": item["address"], "app": item["class"], "title": item["title"],
             "workspace": item["workspace"]["name"], "focused": item.get("focusHistoryID") == 0}
            for item in clients if item.get("mapped", True)]


def _hypr_focus(window_id: str) -> None:
    _hypr_dispatch("focuswindow", window_id)


def _hypr_close(window_id: str) -> None:
    _hypr_dispatch("closewindow", window_id)


def _hypr_dispatch(command: str, window_id: str) -> None:
    answer = _run(["hyprctl", "dispatch", command, f"address:{window_id}"]).strip()
    if answer != "ok":
        raise ServiceError("error", f"the window manager answered: {answer[:200]}")


def _tree_windows(tree: dict, *, sway: bool) -> list[dict[str, object]]:
    """Windows of an i3 or Sway container tree; Sway views carry a pid, i3 windows an X11 window id."""
    found: list[dict[str, object]] = []

    def walk(node: dict, workspace: str) -> None:
        if node.get("type") == "workspace":
            workspace = str(node.get("name", ""))
        if (node.get("pid") if sway else node.get("window")) is not None:
            properties = node.get("window_properties") or {}
            app = (node.get("app_id") or properties.get("class", "")) if sway else properties.get("class", "")
            found.append({"id": str(node["id"] if sway else node["window"]), "app": app,
                          "title": node.get("name") or "", "workspace": workspace,
                          "focused": bool(node.get("focused"))})
        for child in [*node.get("nodes", []), *node.get("floating_nodes", [])]:
            walk(child, workspace)

    walk(tree, "")
    return found


def _i3_windows() -> list[dict[str, object]]:
    return _tree_windows(json.loads(_run(["i3-msg", "-t", "get_tree"])), sway=False)


def _i3_focus(window_id: str) -> None:
    _i3_command(f"[id={window_id}] focus")


def _i3_close(window_id: str) -> None:
    # "kill" asks the window to close (WM_DELETE_WINDOW); "kill client" would cut its connection.
    _i3_command(f"[id={window_id}] kill")


def _i3_command(command: str) -> None:
    _tree_command("i3-msg", command)


def _tree_command(program: str, command: str) -> None:
    replies = json.loads(_run([program, command]))
    failed = [item for item in replies if not item.get("success")]
    if failed:
        raise ServiceError("error", f"{program} answered: {str(failed[0].get('error', 'failed'))[:200]}")


def _sway_windows() -> list[dict[str, object]]:
    return _tree_windows(json.loads(_run(["swaymsg", "-t", "get_tree"])), sway=True)


def _sway_focus(window_id: str) -> None:
    _tree_command("swaymsg", f"[con_id={window_id}] focus")


def _sway_close(window_id: str) -> None:
    _tree_command("swaymsg", f"[con_id={window_id}] kill")


def _niri_windows() -> list[dict[str, object]]:
    return [{"id": str(item["id"]), "app": item.get("app_id") or "", "title": item.get("title") or "",
             "workspace": str(item.get("workspace_id", "")), "focused": bool(item.get("is_focused"))}
            for item in json.loads(_run(["niri", "msg", "--json", "windows"]))]


def _niri_focus(window_id: str) -> None:
    _run(["niri", "msg", "action", "focus-window", "--id", window_id])


def _niri_close(window_id: str) -> None:
    _run(["niri", "msg", "action", "close-window", "--id", window_id])


_GNOME_WINDOWS = ["--session", "--dest", "org.gnome.Shell", "--object-path", "/org/gnome/Shell/Extensions/Windows"]
_GNOME_MAX_WINDOWS = 40


def _gnome_call(method: str, *arguments: str) -> object:
    """One Window Calls method; gdbus prints the reply as a GVariant tuple such as ('text',)."""
    try:
        answer = _run(["gdbus", "call", *_GNOME_WINDOWS, "--method", f"org.gnome.Shell.Extensions.Windows.{method}",
                       *arguments])
    except ServiceError as error:
        if any(word in error.message for word in ("UnknownMethod", "UnknownObject", "UnknownInterface")):
            raise ServiceError("unavailable", "GNOME has no window interface of its own: install the Window Calls "
                               "extension (extensions.gnome.org/extension/4724/window-calls/) and enable it") from None
        raise
    try:
        reply = ast.literal_eval(answer.strip())
        return reply[0] if reply else None
    except (ValueError, SyntaxError, TypeError):
        raise ServiceError("error", f"unreadable answer from the Window Calls extension: {answer[:100]!r}") from None


def _gnome_windows() -> list[dict[str, object]]:
    found = []
    for item in json.loads(str(_gnome_call("List")))[:_GNOME_MAX_WINDOWS]:
        window_id = str(item["id"])
        details = json.loads(str(_gnome_call("Details", window_id)))
        found.append({"id": window_id, "app": item.get("wm_class", ""), "title": str(_gnome_call("GetTitle", window_id)),
                      "workspace": str(item.get("workspace", "")), "focused": bool(details.get("focus"))})
    return found


def _gnome_focus(window_id: str) -> None:
    _gnome_call("Activate", window_id)


def _gnome_close(window_id: str) -> None:
    _gnome_call("Close", window_id)


_KDE_ID = re.compile(r"\{[0-9a-fA-F-]{36}\}")


def _kde_windows() -> list[dict[str, object]]:
    ids = _KDE_ID.findall(_run(["kdotool", "search", "--name", "."]))
    active = _run(["kdotool", "getactivewindow"]).strip()
    return [{"id": window_id, "app": _run(["kdotool", "getwindowclassname", window_id]).strip(),
             "title": _run(["kdotool", "getwindowname", window_id]).strip(), "workspace": "",
             "focused": window_id == active} for window_id in ids]


def _kde_focus(window_id: str) -> None:
    _run(["kdotool", "windowactivate", window_id])


def _kde_close(window_id: str) -> None:
    _run(["kdotool", "windowclose", window_id])


def _wmctrl_windows() -> list[dict[str, object]]:
    active = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"]).split()[-1] if shutil.which("xprop") else ""
    found = []
    for line in _run(["wmctrl", "-lx"]).splitlines():
        window_id, desktop, window_class, _host, title = (line.split(None, 4) + [""])[:5]
        found.append({"id": window_id, "app": window_class.split(".")[-1], "title": title, "workspace": desktop,
                      "focused": active.startswith("0x") and int(window_id, 16) == int(active, 16)})
    return found


def _wmctrl_focus(window_id: str) -> None:
    _run(["wmctrl", "-i", "-a", window_id])


def _wmctrl_close(window_id: str) -> None:
    _run(["wmctrl", "-i", "-c", window_id])


_MAC_LIST = """
const events = Application("System Events");
JSON.stringify(events.processes.whose({backgroundOnly: false})().flatMap(process =>
  process.windows().map((item, index) => ({id: process.unixId() + ":" + index, app: process.name(),
    title: item.name() || "", workspace: "", focused: process.frontmost()}))));
"""
_MAC_ACT = """
function run(argv) {
  const [pid, index, act] = [Number(argv[0]), Number(argv[1]), argv[2]];
  const process = Application("System Events").processes.whose({unixId: pid})()[0];
  const target = process.windows()[index];
  if (act === "focus") { process.frontmost = true; target.actions.byName("AXRaise").perform(); }
  else { target.buttons.whose({subrole: "AXCloseButton"})()[0].click(); }
}
"""


def _mac_windows() -> list[dict[str, object]]:
    return json.loads(_run(["osascript", "-l", "JavaScript", "-e", _MAC_LIST]))


def _mac_focus(window_id: str) -> None:
    _mac_act(window_id, "focus")


def _mac_close(window_id: str) -> None:
    _mac_act(window_id, "close")


def _mac_act(window_id: str, act: str) -> None:
    pid, index = window_id.split(":")
    _run(["osascript", "-l", "JavaScript", "-e", _MAC_ACT, pid, index, act])


_WIN_LIST = ("Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } | "
             "Select-Object Id, ProcessName, MainWindowTitle | ConvertTo-Json -Compress")
_WIN_FOREGROUND = ("Add-Type 'using System;using System.Runtime.InteropServices;public class W{"
                   "[DllImport(\"user32.dll\")]public static extern IntPtr GetForegroundWindow();"
                   "[DllImport(\"user32.dll\")]public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);}';"
                   "$p=0;[void][W]::GetWindowThreadProcessId([W]::GetForegroundWindow(),[ref]$p);$p")


def _powershell(script: str) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def _windows_windows() -> list[dict[str, object]]:
    raw = _powershell(_WIN_LIST).strip()
    rows = json.loads(raw) if raw else []
    answer = _powershell(_WIN_FOREGROUND).strip()
    foreground = int(answer) if answer.isdigit() else 0
    return [{"id": str(item["Id"]), "app": item["ProcessName"], "title": item["MainWindowTitle"], "workspace": "",
             "focused": item["Id"] == foreground} for item in (rows if isinstance(rows, list) else [rows])]


def _windows_focus(window_id: str) -> None:
    _powershell(f"(New-Object -ComObject WScript.Shell).AppActivate({int(window_id)}) | Out-Null")


def _windows_close(window_id: str) -> None:
    # CloseMainWindow asks the program to close, so it can still offer to save; Stop-Process would not.
    _powershell(f"[void](Get-Process -Id {int(window_id)}).CloseMainWindow()")


def _run(command: list[str]) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=_COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise ServiceError("timeout", f"{Path(command[0]).name} did not answer in time") from None
    except OSError as error:
        raise ServiceError("unavailable", f"{Path(command[0]).name} cannot be started: {error}") from None
    if done.returncode != 0:
        raise ServiceError("error", f"{Path(command[0]).name} failed: {(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout
