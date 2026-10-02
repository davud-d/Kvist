#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the Kvist Launcher plugin into every JetBrains IDE on this machine.

Works on Linux and Windows, and best-effort on macOS. It finds each installed IDE by its
per-user data directory, then unpacks the plugin ZIP into that IDE's
`plugins/` folder. Restart the IDE afterwards to pick up the plugin.

    python scripts/installer/install_jetbrains.py            # install into all IDEs
    python scripts/installer/install_jetbrains.py --build    # build the ZIP first
    python scripts/installer/install_jetbrains.py --plugin-zip path/to/kvist-intellij.zip
    python scripts/installer/install_jetbrains.py --uninstall

Building the ZIP is quiet by default: Gradle writes into an install log in the
temp dir while a single spinner names the current step, and a failed command
prints the tail of that log plus its path. Pass --verbose to watch it directly.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from _common import (
    IS_LINUX,
    IS_WINDOWS,
    JETBRAINS_PLUGIN_ID,
    REPO_ROOT,
    InstallCommandError,
    begin_quiet_mode,
    close_log,
    discover_jetbrains_ides,
    hold_last_step,
    human_size,
    log,
    remove_path,
    report_command_failure,
    run_logged,
    safe_extract_zip,
    show_detected_os,
    show_step,
    spinner,
    warn,
)

if TYPE_CHECKING:
    from rich.status import Status

STEPS = {
    "starting": "Starting installation",
    "found_ides": "Preparing to install into {count} IDE(s)",
    "check_installed": "Checking for an already installed plugin",
    "installed_found": "Plugin is already installed — rebuilding the ZIP",
    "installed_none": "No installed plugin found",
    "build_plugin": "Building the plugin ZIP (PyInstaller, then Gradle)",
    "build_done": "Plugin ZIP built successfully",
    "find_zip": "Looking for a built plugin ZIP",
    "zip_found": "Found {name} ({size})",
    "read_zip": "Reading the plugin archive",
    "zip_ok": "Archive holds {count} files under {root}/",
    "install_into": "Installing into {label}",
    "installed_into": "Installed into {label}",
    "install_failed": "[red]Could not install into {label}[/red]",
    "done": "Installation complete",
}

# Truncated per run: one install, one log.
LOG_FILE = Path(tempfile.gettempdir()) / "kvist-jetbrains-install.log"


def plugins_dir_for(config_dir: Path) -> Path:
    """Where a JetBrains IDE loads unpacked plugins from.

    Linux uses the data root directly; Windows and macOS nest a `plugins/`
    sub-directory under it. This asymmetry is documented by JetBrains.
    """
    return config_dir if IS_LINUX else config_dir / "plugins"

PLUGIN_DIR = REPO_ROOT / "frontends" / "ide" / "jetbrains"
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


def build_plugin_zip(status: Status | None = None) -> Path:
    script = PLUGIN_DIR / ("build-plugin.bat" if IS_WINDOWS else "build-plugin.sh")
    if not script.is_file():
        raise SystemExit(f"error: plugin build script not found: {script}")
    show_step(status, STEPS["build_plugin"])
    if IS_WINDOWS:
        run_logged(["cmd", "/c", str(script)], cwd=PLUGIN_DIR)
    else:
        run_logged(["bash", str(script)], cwd=PLUGIN_DIR)
    built = find_plugin_zip()
    if built is None:
        raise SystemExit("error: build finished but no plugin ZIP was produced")
    show_step(status, STEPS["build_done"])
    return built


def _zip_layout(zip_path: Path) -> tuple[str, int]:
    """The archive's single top-level folder, and how many files it holds."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"error: invalid plugin ZIP: {zip_path}") from exc
    with archive:
        entries = [info for info in archive.infolist() if info.filename.strip("/")]
    roots = {info.filename.split("/", 1)[0] for info in entries}
    if len(roots) != 1:
        raise SystemExit(f"error: unexpected plugin ZIP layout (roots: {sorted(roots)})")
    root = roots.pop()
    if root in {".", ".."} or not root or "/" in root or "\\" in root:
        raise SystemExit(f"error: unsafe plugin ZIP root: {root!r}")
    return root, sum(not info.is_dir() for info in entries)


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


def resolve_plugin_zip(
    args: argparse.Namespace, ides: list[tuple[str, Path]], status: Status | None = None
) -> Path:
    """Pick the ZIP to install, building a fresh one when that is the point."""
    if args.plugin_zip is not None:
        return args.plugin_zip
    if args.build:
        return build_plugin_zip(status)

    show_step(status, STEPS["check_installed"])
    installed = plugin_already_installed(ides)
    show_step(status, STEPS["installed_found" if installed else "installed_none"])
    # Re-running over an existing install means "update", so a stale
    # distributions/ ZIP must be rebuilt rather than reused.
    if installed:
        log("plugin is already installed; rebuilding the ZIP")
        return build_plugin_zip(status)

    show_step(status, STEPS["find_zip"])
    found = find_plugin_zip()
    if found is None:
        raise SystemExit(
            "error: no plugin ZIP found. Re-run with --build, or build it via "
            "frontends/ide/jetbrains/build-plugin.sh (.bat on Windows)."
        )
    return found


def install_into_all(
    ides: list[tuple[str, Path]],
    zip_path: Path,
    zip_root: str,
    status: Status | None = None,
) -> list[str]:
    """Unpack the plugin into every IDE; return one line per IDE that failed.

    A single unwritable IDE directory must not abort the others, and the
    failures are returned rather than printed: a spinner may still be live.
    """
    failures: list[str] = []
    for label, config_dir in ides:
        show_step(status, STEPS["install_into"].format(label=label))
        try:
            install_into(config_dir, zip_path, zip_root)
        except OSError as exc:
            show_step(status, STEPS["install_failed"].format(label=label))
            warn(f"could not install into {label}: {exc}")
            failures.append(f"{label}: {exc}")
            continue
        show_step(status, STEPS["installed_into"].format(label=label))
    return failures


def _install_plugin(
    args: argparse.Namespace, ides: list[tuple[str, Path]], status: Status | None = None
) -> list[str]:
    show_detected_os(status)
    show_step(status, STEPS["found_ides"].format(count=len(ides)))
    zip_path = resolve_plugin_zip(args, ides, status)
    show_step(status, STEPS["zip_found"].format(name=zip_path.name, size=human_size(zip_path)))
    show_step(status, STEPS["read_zip"])
    log(f"plugin ZIP: {zip_path}")
    zip_root, entry_count = _zip_layout(zip_path)
    show_step(status, STEPS["zip_ok"].format(count=entry_count, root=zip_root))
    failures = install_into_all(ides, zip_path, zip_root, status)
    show_step(status, STEPS["done"])
    hold_last_step()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Build the plugin ZIP before installing.")
    parser.add_argument("--plugin-zip", type=Path, help="Use this plugin ZIP instead of searching for one.")
    parser.add_argument("--uninstall", action="store_true", help="Remove the plugin from every IDE instead.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the full output of the plugin build instead of a spinner.",
    )
    args = parser.parse_args()

    if args.plugin_zip is not None and not args.plugin_zip.is_file():
        raise SystemExit(f"error: plugin ZIP not found: {args.plugin_zip}")

    ides = discover_jetbrains_ides()
    if not ides:
        raise SystemExit(
            "error: no JetBrains IDE found. Install one (or run it once so it "
            "creates its user directory), then re-run."
        )

    # Printed before the spinner takes the terminal: this is what the user checks.
    log(f"found {len(ides)} JetBrains IDE(s):")
    for label, config_dir in ides:
        print(f"    - {label}  ({config_dir})")

    if args.uninstall:
        # Removal spawns nothing, so it needs neither a log nor a spinner.
        touched = sum(uninstall_from(config_dir) for _, config_dir in ides)
        print(f"\nDone. Plugin removed from {touched} IDE(s). Restart them to apply.")
        return

    if not args.verbose:
        begin_quiet_mode(LOG_FILE)
    try:
        with spinner(STEPS["starting"]) as status:
            failures = _install_plugin(args, ides, status)
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

    if failures:
        raise SystemExit(
            "error: plugin installation failed for:\n  " + "\n  ".join(failures)
        )
    print(
        "\nDone. Restart your JetBrains IDE(s), then use Tools > Run Kvist "
        "(or the toolbar button)."
    )


if __name__ == "__main__":
    main()
