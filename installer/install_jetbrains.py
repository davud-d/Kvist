#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the Kvist Launcher plugin into every JetBrains IDE on this machine.

Works on Linux and Windows (and macOS). It finds each installed IDE by its
per-user data directory, then unpacks the plugin ZIP into that IDE's
`plugins/` folder. Restart the IDE afterwards to pick up the plugin.

    python installer/install_jetbrains.py            # install into all IDEs
    python installer/install_jetbrains.py --build    # build the ZIP first
    python installer/install_jetbrains.py --plugin-zip path/to/kvist-intellij.zip
    python installer/install_jetbrains.py --uninstall
"""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path

from _common import (
    IS_LINUX,
    IS_WINDOWS,
    JETBRAINS_PLUGIN_ID,
    REPO_ROOT,
    discover_jetbrains_ides,
    log,
    remove_path,
    run,
    safe_extract_zip,
    warn,
)


def plugins_dir_for(config_dir: Path) -> Path:
    """Where a JetBrains IDE loads unpacked plugins from.

    Linux uses the data root directly; Windows and macOS nest a `plugins/`
    sub-directory under it. This asymmetry is documented by JetBrains.
    """
    return config_dir if IS_LINUX else config_dir / "plugins"

PLUGIN_DIR = REPO_ROOT / "jetbrains-plugin"
# The unpacked plugin's own top-level folder name (matches the ZIP root entry).
PLUGIN_FOLDER_NAME = "kvist-intellij"


def find_plugin_zip() -> Path | None:
    candidates = [
        *sorted((PLUGIN_DIR / "build" / "distributions").glob("*.zip")),
        REPO_ROOT / "dist" / "jetbrains-plugin.zip",
        *sorted((REPO_ROOT / "dist").glob("kvist-intellij*.zip")),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def plugin_already_installed(ides: list[tuple[str, Path]]) -> bool:
    return any(
        (plugins_dir_for(config_dir) / name).is_dir()
        for _, config_dir in ides
        for name in (PLUGIN_FOLDER_NAME, JETBRAINS_PLUGIN_ID)
    )


def build_plugin_zip() -> Path:
    script = PLUGIN_DIR / ("build-plugin.bat" if IS_WINDOWS else "build-plugin.sh")
    if not script.is_file():
        raise SystemExit(f"error: plugin build script not found: {script}")
    if IS_WINDOWS:
        run(["cmd", "/c", str(script)], cwd=PLUGIN_DIR)
    else:
        run(["bash", str(script)], cwd=PLUGIN_DIR)
    built = find_plugin_zip()
    if built is None:
        raise SystemExit("error: build finished but no plugin ZIP was produced")
    return built


def _zip_root(zip_path: Path) -> str:
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"error: invalid plugin ZIP: {zip_path}") from exc
    with archive:
        roots = {name.split("/", 1)[0] for name in archive.namelist() if name.strip("/")}
    if len(roots) != 1:
        raise SystemExit(f"error: unexpected plugin ZIP layout (roots: {sorted(roots)})")
    root = roots.pop()
    if root in {".", ".."} or not root or "/" in root or "\\" in root:
        raise SystemExit(f"error: unsafe plugin ZIP root: {root!r}")
    return root


def install_into(config_dir: Path, zip_path: Path, zip_root: str) -> None:
    plugins_dir = plugins_dir_for(config_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    if zip_root in {".", ".."} or not zip_root or "/" in zip_root or "\\" in zip_root:
        raise SystemExit(f"error: unsafe plugin ZIP root: {zip_root!r}")
    target = plugins_dir / zip_root
    staging = Path(tempfile.mkdtemp(prefix=f".{zip_root}.", dir=plugins_dir))
    try:
        safe_extract_zip(zip_path, staging)
        staged_target = staging / zip_root
        if not staged_target.is_dir():
            raise SystemExit(f"error: plugin ZIP root not found: {zip_root}")
        backup = plugins_dir / f".{zip_root}.old"
        if backup.exists() or backup.is_symlink():
            remove_path(backup)
        if target.exists() or target.is_symlink():
            target.replace(backup)
        try:
            staged_target.replace(target)
        except BaseException:
            if target.exists() or target.is_symlink():
                remove_path(target)
            if backup.exists() or backup.is_symlink():
                backup.replace(target)
            raise
        if backup.exists() or backup.is_symlink():
            remove_path(backup)
    finally:
        if staging.exists():
            remove_path(staging)
    log(f"installed -> {target}")


def uninstall_from(config_dir: Path) -> bool:
    removed = False
    for name in (PLUGIN_FOLDER_NAME, JETBRAINS_PLUGIN_ID):
        target = plugins_dir_for(config_dir) / name
        if remove_path(target):
            log(f"removed -> {target}")
            removed = True
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Build the plugin ZIP before installing.")
    parser.add_argument("--plugin-zip", type=Path, help="Use this plugin ZIP instead of searching for one.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the plugin from every IDE instead.")
    args = parser.parse_args()

    ides = discover_jetbrains_ides()
    if not ides:
        raise SystemExit(
            "error: no JetBrains IDE found. Install one (or run it once so it "
            "creates its user directory), then re-run."
        )
    log(f"found {len(ides)} JetBrains IDE(s):")
    for label, config_dir in ides:
        print(f"    - {label}  ({config_dir})")

    if args.uninstall:
        touched = sum(uninstall_from(config_dir) for _, config_dir in ides)
        print(f"\nDone. Plugin removed from {touched} IDE(s). Restart them to apply.")
        return

    zip_path = args.plugin_zip
    if zip_path is not None and not zip_path.is_file():
        raise SystemExit(f"error: plugin ZIP not found: {zip_path}")
    if zip_path is None:
        if args.build:
            zip_path = build_plugin_zip()
        elif plugin_already_installed(ides):
            # Re-running over an existing install means "update", so a stale
            # distributions/ ZIP must be rebuilt rather than reused.
            log("plugin is already installed; rebuilding the ZIP ...")
            zip_path = build_plugin_zip()
        else:
            zip_path = find_plugin_zip()
    if zip_path is None:
        raise SystemExit(
            "error: no plugin ZIP found. Re-run with --build, or build it via "
            "jetbrains-plugin/build-plugin.sh (.bat on Windows)."
        )
    log(f"plugin ZIP: {zip_path}")
    zip_root = _zip_root(zip_path)

    failures = []
    for label, config_dir in ides:
        try:
            install_into(config_dir, zip_path, zip_root)
        except OSError as exc:
            warn(f"could not install into {label}: {exc}")
            failures.append(label)

    if failures:
        raise SystemExit(f"error: plugin installation failed for: {', '.join(failures)}")

    print(
        f"\nDone. Restart your JetBrains IDE(s), then use Tools > Run Kvist "
        "(or the toolbar button)."
    )


if __name__ == "__main__":
    main()
