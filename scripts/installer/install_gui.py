#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the Kvist GUI as a desktop application, on Linux or Windows.

Linux: installs the app under ~/.local/share/kvist-gui, drops the icons into
the hicolor theme, and writes a ~/.local/share/applications/kvist.desktop entry
so Kvist shows up in the application launcher.

Windows: installs the app under %LOCALAPPDATA%\\Kvist\\gui and creates a
"Kvist" shortcut on the Desktop and in the Start Menu.

It reuses an already-built GUI from frontends/gui/electron/dist when possible; pass --build to
produce one first (needs Node.js + npm).

    python scripts/installer/install_gui.py
    python scripts/installer/install_gui.py --build
    python scripts/installer/install_gui.py --uninstall

Building is quiet by default: npm and electron-builder write into an install
log in the temp dir while a single spinner names the current step, and a failed
command prints the tail of that log plus its path. Pass --verbose to watch
those commands directly instead.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from _common import (
    ASSETS_DIR,
    GUI_APP_NAME,
    GUI_LINUX_DESKTOP_ID,
    IS_WINDOWS,
    REPO_ROOT,
    InstallCommandError,
    begin_quiet_mode,
    close_log,
    hold_last_step,
    log,
    probe_command,
    remove_path,
    report_command_failure,
    run_logged,
    show_detected_os,
    show_step,
    spinner,
    user_desktop_dir,
    warn,
)

if TYPE_CHECKING:
    from rich.status import Status

STEPS = {
    "starting": "Starting installation",
    "check_installed": "Checking for an existing installation",
    "installed_found": "Kvist GUI is already installed — rebuilding it",
    "installed_none": "No installed Kvist GUI found",
    "check_npm": "Searching for Node.js and npm",
    "npm_missing": "[red]npm not found — install Node.js[/red]",
    "npm_found": "Found {tools}",
    "check_modules": "Checking the GUI's npm packages",
    "modules_found": "npm packages already installed",
    "install_modules": "Installing npm packages",
    "build_core": "Building the Kvist core binary with PyInstaller",
    "build_app": "Packaging the desktop app with electron-builder",
    "build_done": "GUI built successfully",
    "find_payload": "Looking for the built GUI",
    "payload_found": "Found {payload}",
    "copy_app": "Copying the application into place",
    "activate_app": "Activating the new installation",
    "install_icons": "Installing icons",
    "icons_done": "Installed {count} icons into the hicolor theme",
    "create_entry": "Creating the launcher entry",
    "refresh_caches": "Refreshing the desktop and icon caches",
    "shortcut_desktop": "Creating the Desktop shortcut",
    "shortcut_start_menu": "Creating the Start Menu shortcut",
    "run_setup": "Running the Kvist installer — follow its prompts",
    "done": "Installation complete",
}

GUI_DIR = REPO_ROOT / "frontends" / "gui" / "electron"
GUI_DIST = GUI_DIR / "dist"
LINUX_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
# Truncated per run: one install, one log.
LOG_FILE = Path(tempfile.gettempdir()) / "kvist-gui-install.log"

if IS_WINDOWS:
    INSTALL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Kvist" / "gui"
else:
    INSTALL_ROOT = Path.home() / ".local" / "share" / "kvist-gui"


# --------------------------------------------------------------------------- build

def is_already_installed() -> bool:
    return INSTALL_ROOT.is_dir()


def _toolchain_versions(npm: str) -> str:
    """What the build will run with, e.g. "Node.js v22.3.0 + npm 10.8.1"."""
    npm_version = probe_command([npm, "--version"], timeout=60).stdout.strip() or "?"
    node = shutil.which("node")
    if node is None:
        return f"npm {npm_version}"
    node_version = probe_command([node, "--version"], timeout=60).stdout.strip() or "?"
    return f"Node.js {node_version} + npm {npm_version}"


def _find_npm(status: Status | None) -> str:
    show_step(status, STEPS["check_npm"])
    npm = shutil.which("npm")
    if npm is None:
        show_step(status, STEPS["npm_missing"])
        raise SystemExit("error: npm not found; install Node.js to build the GUI")
    show_step(status, STEPS["npm_found"].format(tools=_toolchain_versions(npm)))
    return npm


def build_gui(status: Status | None = None) -> None:
    npm = _find_npm(status)
    show_step(status, STEPS["check_modules"])
    if (GUI_DIR / "node_modules").is_dir():
        show_step(status, STEPS["modules_found"])
    else:
        show_step(status, STEPS["install_modules"])
        run_logged([npm, "install"], cwd=GUI_DIR)
    # `npm run dist` chains these two; running them separately is what lets the
    # step line name which of the two long phases the build is in.
    show_step(status, STEPS["build_core"])
    run_logged([npm, "run", "build:core"], cwd=GUI_DIR)
    show_step(status, STEPS["build_app"])
    run_logged([npm, "run", "dist:app"], cwd=GUI_DIR)
    show_step(status, STEPS["build_done"])


# ----------------------------------------------------------------------------- linux

def _find_linux_payload() -> tuple[str, Path]:
    """Return ('appimage'|'dir', path) for the best available built GUI."""
    appimages = sorted(GUI_DIST.glob("*.AppImage"))
    if appimages:
        return "appimage", appimages[-1]
    unpacked = GUI_DIST / "linux-unpacked"
    if unpacked.is_dir():
        return "dir", unpacked
    raise SystemExit(
        "error: no built Linux GUI found in frontends/gui/electron/dist (expected an .AppImage or "
        "linux-unpacked/). Re-run with --build."
    )


def _linux_unpacked_exe(unpacked: Path) -> Path:
    for name in ("kvist-gui", "kvist", GUI_APP_NAME):
        candidate = unpacked / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"error: could not find the GUI executable inside {unpacked}")


def install_linux(status: Status | None = None) -> str:
    """Install the built GUI; return the line main() prints once done."""
    show_step(status, STEPS["find_payload"])
    kind, payload = _find_linux_payload()
    show_step(status, STEPS["payload_found"].format(payload=payload.name))
    INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staged_root = Path(tempfile.mkdtemp(prefix=".kvist-gui.", dir=INSTALL_ROOT.parent))
    committed_root = False
    backup_root = INSTALL_ROOT.with_name(f".{INSTALL_ROOT.name}.old")

    try:
        show_step(status, STEPS["copy_app"])
        if kind == "appimage":
            staged_exec = staged_root / "Kvist.AppImage"
            shutil.copy2(payload, staged_exec)
            staged_exec.chmod(0o755)
        else:
            staged_dest = staged_root / "linux-unpacked"
            shutil.copytree(payload, staged_dest)
            staged_exec = _linux_unpacked_exe(staged_dest)
            staged_exec.chmod(0o755)

        show_step(status, STEPS["activate_app"])
        if backup_root.exists() or backup_root.is_symlink():
            remove_path(backup_root)
        if INSTALL_ROOT.exists() or INSTALL_ROOT.is_symlink():
            INSTALL_ROOT.replace(backup_root)
        staged_root.replace(INSTALL_ROOT)
        committed_root = True
        exec_path = INSTALL_ROOT / staged_exec.relative_to(staged_root)
        log(f"installed app -> {exec_path}")

        show_step(status, STEPS["install_icons"])
        icon_root = Path.home() / ".local" / "share" / "icons" / "hicolor"
        installed_icons = 0
        for size in LINUX_ICON_SIZES:
            src = ASSETS_DIR / f"icon-{size}.png"
            if not src.is_file():
                continue
            dst_dir = icon_root / f"{size}x{size}" / "apps"
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / f"{GUI_LINUX_DESKTOP_ID}.png")
            installed_icons += 1
        scalable = icon_root / "scalable" / "apps"
        scalable.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ASSETS_DIR / "icon.svg", scalable / f"{GUI_LINUX_DESKTOP_ID}.svg")
        installed_icons += 1
        log(f"installed icons -> {icon_root}")
        show_step(status, STEPS["icons_done"].format(count=installed_icons))

        show_step(status, STEPS["create_entry"])
        apps_dir = Path.home() / ".local" / "share" / "applications"
        apps_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = apps_dir / f"{GUI_LINUX_DESKTOP_ID}.desktop"
        desktop_file.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={GUI_APP_NAME}\n"
            "GenericName=Coding Agent\n"
            "Comment=Local terminal-based coding agent\n"
            f'Exec="{exec_path}" %U\n'
            f"Icon={GUI_LINUX_DESKTOP_ID}\n"
            "Terminal=false\n"
            "Categories=Development;Utility;\n"
            f"StartupWMClass={GUI_APP_NAME}\n",
            newline="\n",
        )
        desktop_file.chmod(0o755)
        log(f"created launcher entry -> {desktop_file}")

        show_step(status, STEPS["refresh_caches"])
        _best_effort(["update-desktop-database", str(apps_dir)])
        _best_effort(["gtk-update-icon-cache", "-f", "-t", str(Path.home() / ".local" / "share" / "icons" / "hicolor")])
        return f"Installed {GUI_APP_NAME} to {exec_path}\nLook for it in your application launcher."
    except BaseException:
        if committed_root:
            remove_path(INSTALL_ROOT)
            if backup_root.exists() or backup_root.is_symlink():
                backup_root.replace(INSTALL_ROOT)
        raise
    finally:
        if staged_root.exists():
            remove_path(staged_root)
        if backup_root.exists() or backup_root.is_symlink():
            remove_path(backup_root)


def uninstall_linux() -> None:
    targets = [
        INSTALL_ROOT,
        Path.home() / ".local" / "share" / "applications" / f"{GUI_LINUX_DESKTOP_ID}.desktop",
    ]
    icon_root = Path.home() / ".local" / "share" / "icons" / "hicolor"
    for size in LINUX_ICON_SIZES:
        targets.append(icon_root / f"{size}x{size}" / "apps" / f"{GUI_LINUX_DESKTOP_ID}.png")
    targets.append(icon_root / "scalable" / "apps" / f"{GUI_LINUX_DESKTOP_ID}.svg")
    _remove_all(targets)


def _best_effort(cmd: list[str]) -> None:
    if shutil.which(cmd[0]) is None:
        return
    try:
        subprocess.run(cmd, check=False, capture_output=True)
    except OSError as exc:
        warn(f"{cmd[0]} failed: {exc}")


# --------------------------------------------------------------------------- windows

def _find_windows_payload() -> tuple[str, Path]:
    setups = sorted(GUI_DIST.glob("*Setup*.exe")) + sorted(GUI_DIST.glob("Kvist*.exe"))
    setups = [p for p in setups if "unpacked" not in str(p)]
    if setups:
        return "setup", setups[-1]
    unpacked = GUI_DIST / "win-unpacked"
    if unpacked.is_dir():
        return "dir", unpacked
    raise SystemExit(
        "error: no built Windows GUI found in frontends/gui/electron/dist (expected a *Setup*.exe or "
        "win-unpacked/). Re-run with --build."
    )


def _windows_shortcut(link_path: Path, target: Path, icon: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        "$s.TargetPath = '{target}';"
        "$s.WorkingDirectory = '{workdir}';"
        "$s.IconLocation = '{icon}';"
        "$s.Description = 'Kvist coding agent';"
        "$s.Save()"
    ).format(link=link_path, target=target, workdir=target.parent, icon=icon)
    run_logged(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])


def install_windows(status: Status | None = None) -> str:
    """Install the built GUI; return the line main() prints once done."""
    show_step(status, STEPS["find_payload"])
    kind, payload = _find_windows_payload()
    show_step(status, STEPS["payload_found"].format(payload=payload.name))
    if kind == "setup":
        # The NSIS installer owns the rest: it lays down its own files and its
        # own Start Menu / Desktop shortcuts, so none of the staging below
        # applies and there is nothing for this installer to undo.
        show_step(status, STEPS["run_setup"])
        run_logged([str(payload)])
        return "The installer created its own Start Menu / Desktop shortcuts."

    INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".kvist-gui.", dir=INSTALL_ROOT.parent))
    backup = INSTALL_ROOT.with_name(f".{INSTALL_ROOT.name}.old")
    committed = False
    try:
        show_step(status, STEPS["copy_app"])
        shutil.copytree(payload, staging / "app")
        dest = staging / "app"
        exe = dest / f"{GUI_APP_NAME}.exe"
        if not exe.is_file():
            exes = [p for p in dest.glob("*.exe") if p.name.lower() != "elevate.exe"]
            if not exes:
                raise SystemExit(f"error: no GUI executable found inside {dest}")
            exe = exes[0]
        icon = dest / "kvist.ico"
        shutil.copy2(ASSETS_DIR / "kvist.ico", icon)

        show_step(status, STEPS["activate_app"])
        if backup.exists() or backup.is_symlink():
            remove_path(backup)
        if INSTALL_ROOT.exists() or INSTALL_ROOT.is_symlink():
            INSTALL_ROOT.replace(backup)
        (staging / "app").replace(INSTALL_ROOT)
        committed = True
        exe = INSTALL_ROOT / exe.relative_to(staging / "app")
        icon = INSTALL_ROOT / icon.relative_to(staging / "app")
        log(f"installed app -> {exe}")

        show_step(status, STEPS["shortcut_desktop"])
        _windows_shortcut(user_desktop_dir() / f"{GUI_APP_NAME}.lnk", exe, icon)
        show_step(status, STEPS["shortcut_start_menu"])
        start_menu = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / \
            "Microsoft" / "Windows" / "Start Menu" / "Programs"
        _windows_shortcut(start_menu / f"{GUI_APP_NAME}.lnk", exe, icon)
        return f"Installed {GUI_APP_NAME} to {exe}\nIt is on your Desktop and in the Start Menu."
    except BaseException:
        if committed:
            remove_path(INSTALL_ROOT)
            if backup.exists() or backup.is_symlink():
                backup.replace(INSTALL_ROOT)
        raise
    finally:
        if staging.exists():
            remove_path(staging)
        if backup.exists() or backup.is_symlink():
            remove_path(backup)


def uninstall_windows() -> None:
    start_menu = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / \
        "Microsoft" / "Windows" / "Start Menu" / "Programs"
    _remove_all([
        INSTALL_ROOT,
        user_desktop_dir() / f"{GUI_APP_NAME}.lnk",
        start_menu / f"{GUI_APP_NAME}.lnk",
    ])


# ------------------------------------------------------------------------------ misc

def _remove_all(targets: list[Path]) -> None:
    for target in targets:
        if remove_path(target):
            log(f"removed -> {target}")
    print("\nDone. Kvist GUI uninstalled.")


def _install_gui(args: argparse.Namespace, status: Status | None = None) -> str:
    show_detected_os(status)
    show_step(status, STEPS["check_installed"])
    installed = is_already_installed()
    show_step(status, STEPS["installed_found" if installed else "installed_none"])
    if installed:
        log(f"Kvist GUI is already installed at {INSTALL_ROOT}")

    # Re-running over an existing install means "update", so a stale dist/
    # build must be rebuilt rather than silently reused.
    if args.build or installed:
        build_gui(status)

    done = (install_windows if IS_WINDOWS else install_linux)(status)
    show_step(status, STEPS["done"])
    hold_last_step()
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Build the GUI with electron-builder first.")
    parser.add_argument("--uninstall", action="store_true", help="Remove an installed GUI instead.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the full output of npm and electron-builder instead of a spinner.",
    )
    args = parser.parse_args()

    if args.uninstall:
        # Removal spawns nothing, so it needs neither a log nor a spinner.
        (uninstall_windows if IS_WINDOWS else uninstall_linux)()
        return

    if not args.verbose:
        begin_quiet_mode(LOG_FILE)
    try:
        with spinner(STEPS["starting"]) as status:
            done = _install_gui(args, status)
    except InstallCommandError as exc:
        # Reported out here on purpose: leaving the `with` above already
        # stopped the spinner and erased its line, so this cannot print over it.
        report_command_failure(exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Installation cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
    finally:
        close_log()

    print(f"\n{done}")


if __name__ == "__main__":
    main()
