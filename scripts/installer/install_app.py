#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the pixel desktop app (frontends/gui/tauri) from source and put it into the application menu.

This is the developer path, like ``install_cli.py``: it compiles the app with cargo (``install_rust.py``
brings Rust, so this installer waits for it) and registers the result where the system looks for
applications. People who install a release get the prebuilt binary from ``install.sh`` /
``install.ps1`` instead.

- Linux: ``~/.local/share/kvist/app/kvist-app``, its icon in the hicolor theme and a
  ``kvist-app.desktop`` entry in ``~/.local/share/applications``. Needs the WebKitGTK and GTK
  development packages (checked first, with the command to install them).
- macOS: ``~/Applications/Kvist Pixel.app`` (Launchpad and Spotlight find it). The app is not
  signed or notarized; the quarantine mark is removed so it opens.
- Windows: ``%LOCALAPPDATA%\\Kvist\\app\\kvist-app.exe`` with a "Kvist Pixel" shortcut in the
  Start menu and on the Desktop.

The app finds the agent (``kvist-core``) one folder above itself or where the CLI installer
puts it, and falls back to this checkout's ``.venv``.

    python scripts/installer/install_app.py
    python scripts/installer/install_app.py --uninstall
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from _common import (
    REPO_ROOT,
    InstallCommandError,
    log,
    probe_command,
    remove_path,
    report_command_failure,
    run_logged,
    user_desktop_dir,
)
from install_rust import find_cargo

APP_NAME = "Kvist Pixel"
DESKTOP_ID = "kvist-app"
TAURI_DIR = REPO_ROOT / "frontends" / "gui" / "tauri" / "src-tauri"
BUILT_DIR = TAURI_DIR / "target" / "release"

# What the Linux web view is built against, by package manager.
_LINUX_PACKAGES = (
    ("apt-get", "sudo apt-get install -y libwebkit2gtk-4.1-dev libgtk-3-dev librsvg2-dev libssl-dev pkg-config"),
    ("dnf", "sudo dnf install -y webkit2gtk4.1-devel gtk3-devel librsvg2-devel openssl-devel pkgconf-pkg-config"),
    ("pacman", "sudo pacman -S --needed webkit2gtk-4.1 gtk3 librsvg openssl pkgconf"),
)

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleName</key><string>{name}</string>
\t<key>CFBundleDisplayName</key><string>{name}</string>
\t<key>CFBundleIdentifier</key><string>dev.davud.kvist.tauri</string>
\t<key>CFBundleExecutable</key><string>kvist-app</string>
\t<key>CFBundleIconFile</key><string>kvist-app</string>
\t<key>CFBundlePackageType</key><string>APPL</string>
\t<key>LSMinimumSystemVersion</key><string>11.0</string>
\t<key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


@dataclass(slots=True, frozen=True)
class Layout:
    """Where the app goes on this machine; a value so a test can say what the machine is."""

    system: str
    home: Path
    data_home: Path
    local_app_data: Path
    app_data: Path
    desktop: Path

    @property
    def app_dir(self) -> Path:
        if self.system == "Windows":
            return self.local_app_data / "Kvist" / "app"
        return self.data_home / "kvist" / "app"

    @property
    def binary(self) -> Path:
        return self.app_dir / ("kvist-app.exe" if self.system == "Windows" else "kvist-app")

    @property
    def desktop_entry(self) -> Path:
        return self.data_home / "applications" / f"{DESKTOP_ID}.desktop"

    @property
    def icon_dir(self) -> Path:
        return self.data_home / "icons" / "hicolor" / "256x256" / "apps"

    @property
    def mac_bundle(self) -> Path:
        return self.home / "Applications" / f"{APP_NAME}.app"

    @property
    def start_menu(self) -> Path:
        return self.app_data / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def layout_from_environment() -> Layout:
    home = Path.home()
    system = platform.system()
    return Layout(
        system=system,
        home=home,
        data_home=Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share"),
        local_app_data=Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"),
        app_data=Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming"),
        desktop=user_desktop_dir() if system == "Windows" else home / "Desktop",
    )


# ------------------------------------------------------------------- building


def check_linux_libraries() -> None:
    """Refuse before a long build that would fail on a missing web view library."""
    if probe_command(["pkg-config", "--exists", "webkit2gtk-4.1"]).returncode == 0:
        return
    command = next((line for manager, line in _LINUX_PACKAGES if shutil.which(manager)), None)
    hint = f"Run:\n    {command}" if command else "Install the WebKitGTK 4.1 and GTK 3 development packages."
    raise SystemExit(f"error: building the desktop app needs the WebKitGTK 4.1 development files, and they are missing.\n{hint}\nthen run this installer again.")


def build_from_source(system: str) -> Path:
    cargo = find_cargo()
    if cargo is None:
        raise SystemExit("error: building the desktop app needs Rust; tick the Rust toolchain or run install_rust.py first")
    if system == "Linux":
        check_linux_libraries()
    log("building the desktop app with cargo (a few minutes the first time) ...")
    run_logged([cargo, "build", "--release", "--locked"], cwd=TAURI_DIR)
    built = BUILT_DIR / ("kvist-tauri.exe" if system == "Windows" else "kvist-tauri")
    if not built.is_file():
        raise SystemExit(f"error: cargo finished, but {built} was not produced")
    return built


# ------------------------------------------------------------------- putting it in place


def _replace_file(source: Path, target: Path, mode: int) -> None:
    """Copy through a temporary name: replacing a running binary in place fails (ETXTBSY)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.new")
    shutil.copyfile(source, staged)
    staged.chmod(mode)
    staged.replace(target)


def install_linux(layout: Layout, binary: Path, png: Path) -> str:
    _replace_file(binary, layout.binary, 0o755)
    layout.icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(png, layout.icon_dir / f"{DESKTOP_ID}.png")
    layout.desktop_entry.parent.mkdir(parents=True, exist_ok=True)
    layout.desktop_entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Kvist agents side by side, in pixel style\n"
        f'Exec="{layout.binary}"\n'
        f"Icon={DESKTOP_ID}\n"
        "Terminal=false\n"
        "Categories=Development;\n"
        "StartupWMClass=kvist-tauri\n"
        "Actions=safe;\n\n"
        "[Desktop Action safe]\n"
        "Name=Safe mode (if the window is empty)\n"
        f'Exec="{layout.binary}" --safe\n',
        encoding="utf-8",
        newline="\n",
    )
    layout.desktop_entry.chmod(0o755)
    _refresh_linux_caches(layout)
    return f"Installed {layout.binary}\nLook for {APP_NAME} in your application launcher."


def _refresh_linux_caches(layout: Layout) -> None:
    for command in (
        ["update-desktop-database", str(layout.desktop_entry.parent)],
        ["gtk-update-icon-cache", "-f", "-t", str(layout.data_home / "icons" / "hicolor")],
    ):
        if shutil.which(command[0]) is None:
            continue
        try:
            run_logged(command)
        except InstallCommandError:
            # A desktop that does not refresh its caches still lists the entry after a re-login.
            log(f"{command[0]} failed; the entry appears after the next login")


def install_macos(layout: Layout, binary: Path, icns: Path) -> str:
    bundle = layout.mac_bundle
    if bundle.exists() or bundle.is_symlink():
        remove_path(bundle)
    contents = bundle / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    _replace_file(binary, contents / "MacOS" / "kvist-app", 0o755)
    shutil.copyfile(icns, contents / "Resources" / "kvist-app.icns")
    (contents / "Info.plist").write_text(_INFO_PLIST.format(name=APP_NAME), encoding="utf-8")
    if shutil.which("xattr"):
        try:
            run_logged(["xattr", "-dr", "com.apple.quarantine", str(bundle)])
        except InstallCommandError:
            log("could not clear the quarantine mark; open the app once from its context menu")
    return f"Installed {bundle}\nLaunchpad and Spotlight find {APP_NAME}."


def _windows_shortcut(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath = '{target}';"
        f"$s.WorkingDirectory = '{target.parent}';"
        f"$s.IconLocation = '{target},0';"
        "$s.Description = 'Kvist agents side by side, in pixel style';"
        "$s.Save()"
    )
    run_logged(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def install_windows(layout: Layout, binary: Path) -> str:
    _replace_windows_binary(binary, layout.binary)
    _windows_shortcut(layout.start_menu / f"{APP_NAME}.lnk", layout.binary)
    _windows_shortcut(layout.desktop / f"{APP_NAME}.lnk", layout.binary)
    return f"Installed {layout.binary}\nIt is in the Start menu and on your Desktop."


def _replace_windows_binary(source: Path, target: Path) -> None:
    """A running exe cannot be overwritten on Windows, but it can be renamed away."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.with_name(f"{target.name}.old")
    old.unlink(missing_ok=True)
    if target.exists():
        target.replace(old)
    shutil.copyfile(source, target)
    old.unlink(missing_ok=True)


def install(layout: Layout, binary: Path) -> str:
    icons = TAURI_DIR / "icons"
    if layout.system == "Darwin":
        return install_macos(layout, binary, icons / "icon.icns")
    if layout.system == "Windows":
        return install_windows(layout, binary)
    return install_linux(layout, binary, icons / "128x128@2x.png")


def uninstall(layout: Layout) -> list[Path]:
    """Remove what install put in place; returns what was removed."""
    targets = {
        "Linux": [layout.app_dir, layout.desktop_entry, layout.icon_dir / f"{DESKTOP_ID}.png"],
        "Darwin": [layout.mac_bundle],
        "Windows": [layout.app_dir, layout.start_menu / f"{APP_NAME}.lnk", layout.desktop / f"{APP_NAME}.lnk"],
    }.get(layout.system, [])
    return [target for target in targets if remove_path(target)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    layout = layout_from_environment()
    if args.uninstall:
        removed = uninstall(layout)
        print("Removed:\n" + "\n".join(f"  {path}" for path in removed) if removed else "Nothing to remove.")
        return
    try:
        print(install(layout, build_from_source(layout.system)))
    except InstallCommandError as error:
        report_command_failure(error)
        sys.exit(1)


if __name__ == "__main__":
    main()
