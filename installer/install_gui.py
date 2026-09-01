#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the Kvist GUI as a desktop application, on Linux or Windows.

Linux: installs the app under ~/.local/share/kvist-gui, drops the icons into
the hicolor theme, and writes a ~/.local/share/applications/kvist.desktop entry
so Kvist shows up in the application launcher.

Windows: installs the app under %LOCALAPPDATA%\\Kvist\\gui and creates a
"Kvist" shortcut on the Desktop and in the Start Menu.

It reuses an already-built GUI from gui/dist when possible; pass --build to
produce one first (needs Node.js + npm).

    python installer/install_gui.py
    python installer/install_gui.py --build
    python installer/install_gui.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from _common import (
    ASSETS_DIR,
    GUI_APP_NAME,
    GUI_LINUX_DESKTOP_ID,
    IS_WINDOWS,
    REPO_ROOT,
    log,
    remove_path,
    run,
    user_desktop_dir,
    warn,
)

GUI_DIR = REPO_ROOT / "gui"
GUI_DIST = GUI_DIR / "dist"
LINUX_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)

if IS_WINDOWS:
    INSTALL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Kvist" / "gui"
else:
    INSTALL_ROOT = Path.home() / ".local" / "share" / "kvist-gui"


# --------------------------------------------------------------------------- build

def build_gui() -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("error: npm not found; install Node.js to use --build")
    if not (GUI_DIR / "node_modules").is_dir():
        run([npm, "install"], cwd=GUI_DIR)
    run([npm, "run", "dist"], cwd=GUI_DIR)


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
        "error: no built Linux GUI found in gui/dist (expected an .AppImage or "
        "linux-unpacked/). Re-run with --build."
    )


def _linux_unpacked_exe(unpacked: Path) -> Path:
    for name in ("kvist-gui", "kvist", GUI_APP_NAME):
        candidate = unpacked / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"error: could not find the GUI executable inside {unpacked}")


def install_linux() -> None:
    kind, payload = _find_linux_payload()
    INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staged_root = Path(tempfile.mkdtemp(prefix=".kvist-gui.", dir=INSTALL_ROOT.parent))
    committed_root = False
    backup_root = INSTALL_ROOT.with_name(f".{INSTALL_ROOT.name}.old")

    try:
        if kind == "appimage":
            staged_exec = staged_root / "Kvist.AppImage"
            shutil.copy2(payload, staged_exec)
            staged_exec.chmod(0o755)
        else:
            staged_dest = staged_root / "linux-unpacked"
            shutil.copytree(payload, staged_dest)
            staged_exec = _linux_unpacked_exe(staged_dest)
            staged_exec.chmod(0o755)

        if backup_root.exists() or backup_root.is_symlink():
            remove_path(backup_root)
        if INSTALL_ROOT.exists() or INSTALL_ROOT.is_symlink():
            INSTALL_ROOT.replace(backup_root)
        staged_root.replace(INSTALL_ROOT)
        committed_root = True
        exec_path = INSTALL_ROOT / staged_exec.relative_to(staged_root)
        log(f"installed app -> {exec_path}")

        icon_root = Path.home() / ".local" / "share" / "icons" / "hicolor"
        for size in LINUX_ICON_SIZES:
            src = ASSETS_DIR / f"icon-{size}.png"
            if not src.is_file():
                continue
            dst_dir = icon_root / f"{size}x{size}" / "apps"
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / f"{GUI_LINUX_DESKTOP_ID}.png")
        scalable = icon_root / "scalable" / "apps"
        scalable.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ASSETS_DIR / "icon.svg", scalable / f"{GUI_LINUX_DESKTOP_ID}.svg")
        log(f"installed icons -> {icon_root}")

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

        _best_effort(["update-desktop-database", str(apps_dir)])
        _best_effort(["gtk-update-icon-cache", "-f", "-t", str(Path.home() / ".local" / "share" / "icons" / "hicolor")])
        print(f"\nDone. Look for \"{GUI_APP_NAME}\" in your application launcher.")
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
        "error: no built Windows GUI found in gui/dist (expected a *Setup*.exe or "
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
    run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])


def install_windows() -> None:
    kind, payload = _find_windows_payload()
    if kind == "setup":
        log("launching the NSIS installer; follow its prompts ...")
        run([str(payload)])
        print("\nDone. The installer created its own Start Menu / Desktop shortcuts.")
        return

    INSTALL_ROOT.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".kvist-gui.", dir=INSTALL_ROOT.parent))
    backup = INSTALL_ROOT.with_name(f".{INSTALL_ROOT.name}.old")
    committed = False
    try:
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

        if backup.exists() or backup.is_symlink():
            remove_path(backup)
        if INSTALL_ROOT.exists() or INSTALL_ROOT.is_symlink():
            INSTALL_ROOT.replace(backup)
        (staging / "app").replace(INSTALL_ROOT)
        committed = True
        exe = INSTALL_ROOT / exe.relative_to(staging / "app")
        icon = INSTALL_ROOT / icon.relative_to(staging / "app")
        log(f"installed app -> {exe}")

        _windows_shortcut(user_desktop_dir() / f"{GUI_APP_NAME}.lnk", exe, icon)
        start_menu = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / \
            "Microsoft" / "Windows" / "Start Menu" / "Programs"
        _windows_shortcut(start_menu / f"{GUI_APP_NAME}.lnk", exe, icon)
        print(f"\nDone. \"{GUI_APP_NAME}\" is on your Desktop and in the Start Menu.")
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Build the GUI with electron-builder first.")
    parser.add_argument("--uninstall", action="store_true", help="Remove an installed GUI instead.")
    args = parser.parse_args()

    if args.uninstall:
        (uninstall_windows if IS_WINDOWS else uninstall_linux)()
        return

    if args.build:
        build_gui()

    (install_windows if IS_WINDOWS else install_linux)()


if __name__ == "__main__":
    main()
