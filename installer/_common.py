# -*- coding: utf-8 -*-
"""Shared helpers for the Kvist installers (CLI, JetBrains plugin, GUI)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

INSTALLER_DIR = Path(__file__).resolve().parent
REPO_ROOT = INSTALLER_DIR.parent
ASSETS_DIR = INSTALLER_DIR / "assets"

# The plugin id, kept in sync with jetbrains-plugin/src/main/resources/META-INF/plugin.xml.
JETBRAINS_PLUGIN_ID = "com.kvist.intellij"
# electron-builder productName; the unpacked executable is named after `name`.
GUI_APP_NAME = "Kvist"
GUI_LINUX_DESKTOP_ID = "kvist"


def log(message: str) -> None:
    print(f"==> {message}")


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    log(" ".join(str(part) for part in cmd))
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        raise SystemExit(f"error: command failed ({result.returncode}): {' '.join(map(str, cmd))}")
    return result.returncode


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract every member of `zip_path` under `dest_dir`, refusing escapes.

    `ZipFile.extractall` follows `../` components and absolute paths in member
    names and will happily recreate symlink entries, so a hostile archive can
    write anywhere the process can. This rejects any member that would land
    outside `dest_dir` and any symlink member.
    """
    dest_dir = dest_dir.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"error: invalid ZIP archive: {zip_path}") from exc
    with archive:
        for info in archive.infolist():
            # Unix mode is stored in the top 16 bits of external_attr; 0xA000 == S_IFLNK.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit(f"error: refusing symlink entry in archive: {info.filename}")
            target = (dest_dir / info.filename).resolve()
            if target != dest_dir and dest_dir not in target.parents:
                raise SystemExit(f"error: refusing path-traversal entry in archive: {info.filename}")
        try:
            archive.extractall(dest_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            raise SystemExit(f"error: could not extract ZIP archive: {zip_path}") from exc


def remove_path(target: Path) -> bool:
    """Delete a file, directory, or symlink without following it. No-op if absent.

    A naive ``if target.exists(): shutil.rmtree(target)`` is wrong twice: it
    misses broken symlinks (``exists()`` is False for them) and it crashes on a
    symlinked directory (``rmtree`` refuses one, and a dev checkout often
    symlinks the installed plugin folder back to the repo). Returns whether
    anything was removed.
    """
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        target.unlink()
        return True
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return False


def jetbrains_config_roots() -> list[Path]:
    """Directories that hold one sub-directory per installed JetBrains IDE.

    Since 2020.1 every IDE keeps its per-user data under a shared vendor root
    (`.../JetBrains/<Product><version>`), regardless of whether it was installed
    standalone, from a distro package, or via Toolbox.
    """
    home = Path.home()
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return [base / "JetBrains"]
    if IS_MACOS:
        return [home / "Library" / "Application Support" / "JetBrains"]
    roots = [home / ".local" / "share" / "JetBrains"]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.insert(0, Path(xdg) / "JetBrains")
    return roots


# Product-directory prefixes -> human label. A directory is "IntelliJIdea2025.2",
# "PyCharm2024.3", "GoLand2025.1" and so on; the trailing version is stripped.
JETBRAINS_PRODUCT_PREFIXES = {
    "IntelliJIdea": "IntelliJ IDEA",
    "IdeaIC": "IntelliJ IDEA Community",
    "PyCharm": "PyCharm",
    "PyCharmCE": "PyCharm Community",
    "WebStorm": "WebStorm",
    "PhpStorm": "PhpStorm",
    "GoLand": "GoLand",
    "CLion": "CLion",
    "Rider": "Rider",
    "RubyMine": "RubyMine",
    "DataGrip": "DataGrip",
    "DataSpell": "DataSpell",
    "RustRover": "RustRover",
    "AndroidStudio": "Android Studio",
    "Aqua": "Aqua",
}


def _match_product(dir_name: str) -> str | None:
    for prefix, label in sorted(JETBRAINS_PRODUCT_PREFIXES.items(), key=lambda kv: -len(kv[0])):
        rest = dir_name[len(prefix):]
        if dir_name.startswith(prefix) and rest[:1].isdigit():
            return f"{label} {rest}"
    return None


def discover_jetbrains_ides() -> list[tuple[str, Path]]:
    """Return (label, config_dir) for every installed JetBrains IDE found."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for root in jetbrains_config_roots():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry in seen:
                continue
            label = _match_product(entry.name)
            if label is None:
                continue
            seen.add(entry)
            found.append((label, entry))
    return found


def user_desktop_dir() -> Path:
    home = Path.home()
    if IS_WINDOWS:
        # Fall back gracefully when the shell folder is redirected (OneDrive etc.).
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                expanded = Path(os.path.expandvars(value))
                if expanded.is_dir():
                    return expanded
        except OSError:
            pass
    return home / "Desktop"
