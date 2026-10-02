#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install the Kvist VS Code extension into every VS Code-compatible editor found.

Works on Linux and Windows, and best-effort on macOS. It finds command-line launchers for VS Code,
VS Code Insiders and VSCodium, then installs the Kvist `.vsix` with the
editor's own `--install-extension` command. Restart or reload the editor
afterwards if it was already open.

    python scripts/installer/install_vscode.py             # install into all found editors
    python scripts/installer/install_vscode.py --build     # build the VSIX first
    python scripts/installer/install_vscode.py --vsix path/to/kvist.vsix
    python scripts/installer/install_vscode.py --uninstall

Building the VSIX is quiet by default: PyInstaller and npm write into an
install log in the temp dir while a single spinner names the current step, and
a failed command prints the tail of that log plus its path. Pass --verbose to
watch it directly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from _common import (
    IS_MACOS,
    IS_WINDOWS,
    REPO_ROOT,
    InstallCommandError,
    begin_quiet_mode,
    close_log,
    hold_last_step,
    human_size,
    log,
    probe_command,
    report_command_failure,
    run_logged,
    show_detected_os,
    show_step,
    spinner,
    warn,
)

if TYPE_CHECKING:
    from rich.status import Status

VSCODE_EXTENSION_ID = "kvist.kvist"
EXTENSION_DIR = REPO_ROOT / "frontends" / "ide" / "vscode"
LOG_FILE = Path(tempfile.gettempdir()) / "kvist-vscode-install.log"

STEPS = {
    "starting": "Starting installation",
    "find_editors": "Searching for VS Code-compatible editors",
    "found_editors": "Preparing to install into {count} editor(s)",
    "check_installed": "Checking for an already installed extension",
    "installed_found": "Extension is already installed — rebuilding the VSIX",
    "installed_none": "No installed extension found",
    "build_extension": "Building the VS Code extension VSIX (PyInstaller, then vsce)",
    "build_done": "VSIX built successfully",
    "find_vsix": "Looking for a built VSIX",
    "vsix_found": "Found {name} ({size})",
    "read_vsix": "Reading the VSIX archive",
    "vsix_ok": "Archive holds {count} files for {extension_id}",
    "install_into": "Installing into {label}",
    "installed_into": "Installed into {label}",
    "install_failed": "[red]Could not install into {label}[/red]",
    "uninstall_from": "Removing from {label}",
    "uninstalled_from": "Removed from {label}",
    "done": "Installation complete",
}


def _macos_app_commands() -> list[tuple[str, str]]:
    """The command-line launchers inside the app bundles.

    `code` is on PATH only after the user ran "Install 'code' command in PATH"
    from the editor, so the bundles are looked at directly as well.
    """
    bundles = [
        ("VS Code", "Visual Studio Code", "code"),
        ("VS Code Insiders", "Visual Studio Code - Insiders", "code-insiders"),
        ("VSCodium", "VSCodium", "codium"),
    ]
    roots = [Path("/Applications"), Path.home() / "Applications"]
    return [
        (label, str(root / f"{app}.app" / "Contents" / "Resources" / "app" / "bin" / cli))
        for root in roots
        for label, app, cli in bundles
    ]


def _candidate_commands() -> list[tuple[str, str]]:
    names = [
        ("VS Code", "code.cmd" if IS_WINDOWS else "code"),
        ("VS Code Insiders", "code-insiders.cmd" if IS_WINDOWS else "code-insiders"),
        ("VSCodium", "codium.cmd" if IS_WINDOWS else "codium"),
    ]
    if IS_MACOS:
        return [*names, *_macos_app_commands()]
    if not IS_WINDOWS:
        return names

    local = Path.home() / "AppData" / "Local" / "Programs"
    roots = [local]
    roots.extend(Path(value) for key in ("ProgramFiles", "ProgramFiles(x86)") if (value := os.environ.get(key)))
    explicit: list[tuple[str, str]] = []
    for root in roots:
        explicit.extend(
            [
                ("VS Code", str(root / "Microsoft VS Code" / "bin" / "code.cmd")),
                ("VS Code Insiders", str(root / "Microsoft VS Code Insiders" / "bin" / "code-insiders.cmd")),
                ("VSCodium", str(root / "VSCodium" / "bin" / "codium.cmd")),
            ]
        )
    return [*names, *explicit]


def discover_vscode_editors(status: Status | None = None) -> list[tuple[str, str]]:
    if status is not None:
        show_step(status, STEPS["find_editors"])
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, command in _candidate_commands():
        resolved = shutil.which(command) or (command if Path(command).is_file() else None)
        if resolved is None:
            continue
        canonical = str(Path(resolved).resolve()) if Path(resolved).exists() else resolved.lower()
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append((label, resolved))
    return found


def find_extension_vsix() -> Path | None:
    candidates = [
        *sorted((EXTENSION_DIR / ".build" / "vsix").glob("*.vsix")),
        *sorted(EXTENSION_DIR.glob("*.vsix")),
        *sorted((REPO_ROOT / "dist").glob("*.vsix")),
    ]
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def build_extension_vsix(status: Status | None = None) -> Path:
    script = EXTENSION_DIR / ("build-plugin.bat" if IS_WINDOWS else "build-plugin.sh")
    if not script.is_file():
        raise SystemExit(f"error: VS Code extension build script not found: {script}")
    show_step(status, STEPS["build_extension"])
    if IS_WINDOWS:
        run_logged(["cmd", "/c", str(script)], cwd=EXTENSION_DIR)
    else:
        run_logged(["bash", str(script)], cwd=EXTENSION_DIR)
    built = find_extension_vsix()
    if built is None:
        raise SystemExit("error: build finished but no VSIX was produced")
    show_step(status, STEPS["build_done"])
    return built


def _vsix_layout(vsix_path: Path) -> tuple[str, int]:
    try:
        archive = zipfile.ZipFile(vsix_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"error: invalid VSIX archive: {vsix_path}") from exc
    with archive:
        names = {info.filename for info in archive.infolist() if info.filename.strip("/")}
        try:
            manifest = json.loads(archive.read("extension/package.json").decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemExit(f"error: VSIX does not contain a readable extension/package.json: {vsix_path}") from exc
    name = manifest.get("name")
    publisher = manifest.get("publisher")
    extension_id = f"{publisher}.{name}" if publisher and name else ""
    if extension_id != VSCODE_EXTENSION_ID:
        raise SystemExit(f"error: VSIX is {extension_id or 'unknown extension'}, expected {VSCODE_EXTENSION_ID}")
    main = str(manifest.get("main") or "out/extension.js")
    if not main.endswith((".js", ".cjs")):
        main += ".js"
    normalized_main = main.replace("\\", "/")
    main_entry = f"extension/{normalized_main}"
    if main_entry not in names:
        raise SystemExit(f"error: VSIX entry point missing: {main_entry}")
    return extension_id, len(names)


def _extension_installed(command: str) -> bool:
    result = probe_command(_editor_command(command, "--list-extensions"), timeout=30)
    if result.returncode != 0:
        return False
    return VSCODE_EXTENSION_ID.lower() in {line.strip().lower() for line in result.stdout.splitlines()}


def _editor_command(command: str, *args: str) -> list[str]:
    if IS_WINDOWS and command.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", command, *args]
    return [command, *args]


def extension_already_installed(editors: list[tuple[str, str]]) -> bool:
    return any(_extension_installed(command) for _, command in editors)


def resolve_extension_vsix(
    args: argparse.Namespace, editors: list[tuple[str, str]], status: Status | None = None
) -> Path:
    if args.extension_vsix is not None:
        return args.extension_vsix
    if args.build:
        return build_extension_vsix(status)

    show_step(status, STEPS["check_installed"])
    installed = extension_already_installed(editors)
    show_step(status, STEPS["installed_found" if installed else "installed_none"])
    if installed:
        log("extension is already installed; rebuilding the VSIX")
        return build_extension_vsix(status)

    show_step(status, STEPS["find_vsix"])
    found = find_extension_vsix()
    if found is None:
        raise SystemExit(
            "error: no VSIX found. Re-run with --build, or build it via "
            "frontends/ide/vscode/build-plugin.sh (.bat on Windows)."
        )
    return found


def install_into_all(editors: list[tuple[str, str]], vsix_path: Path, status: Status | None = None) -> list[str]:
    failures: list[str] = []
    for label, command in editors:
        show_step(status, STEPS["install_into"].format(label=label))
        try:
            run_logged(_editor_command(command, "--install-extension", str(vsix_path), "--force"))
        except InstallCommandError as exc:
            # Retry without --force for older VS Code versions (pre-1.40) that don't support it
            try:
                run_logged(_editor_command(command, "--install-extension", str(vsix_path)))
            except InstallCommandError as retry_exc:
                show_step(status, STEPS["install_failed"].format(label=label))
                warn(f"could not install into {label}: {retry_exc}")
                failures.append(f"{label}: {retry_exc}")
                continue
        show_step(status, STEPS["installed_into"].format(label=label))
    return failures


def uninstall_from_all(editors: list[tuple[str, str]], status: Status | None = None) -> list[str]:
    failures: list[str] = []
    for label, command in editors:
        show_step(status, STEPS["uninstall_from"].format(label=label))
        if not _extension_installed(command):
            show_step(status, STEPS["uninstalled_from"].format(label=label))
            continue
        try:
            run_logged(_editor_command(command, "--uninstall-extension", VSCODE_EXTENSION_ID))
        except InstallCommandError as exc:
            warn(f"could not uninstall from {label}: {exc}")
            failures.append(f"{label}: {exc}")
            continue
        show_step(status, STEPS["uninstalled_from"].format(label=label))
    return failures


def _install_extension(
    args: argparse.Namespace, editors: list[tuple[str, str]], status: Status | None = None
) -> list[str]:
    show_detected_os(status)
    show_step(status, STEPS["found_editors"].format(count=len(editors)))
    if args.uninstall:
        failures = uninstall_from_all(editors, status)
        show_step(status, STEPS["done"])
        hold_last_step()
        return failures

    vsix_path = resolve_extension_vsix(args, editors, status)
    show_step(status, STEPS["vsix_found"].format(name=vsix_path.name, size=human_size(vsix_path)))
    show_step(status, STEPS["read_vsix"])
    log(f"VSIX: {vsix_path}")
    extension_id, entry_count = _vsix_layout(vsix_path)
    show_step(status, STEPS["vsix_ok"].format(count=entry_count, extension_id=extension_id))
    failures = install_into_all(editors, vsix_path, status)
    show_step(status, STEPS["done"])
    hold_last_step()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true", help="Build the VSIX before installing.")
    parser.add_argument(
        "--extension-vsix",
        "--vsix",
        dest="extension_vsix",
        type=Path,
        help="Use this VSIX instead of searching for one.",
    )
    parser.add_argument("--uninstall", action="store_true", help="Remove the extension from every editor instead.")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show the full output of the extension build/install instead of a spinner.",
    )
    args = parser.parse_args()

    if args.extension_vsix is not None and not args.extension_vsix.is_file():
        raise SystemExit(f"error: VSIX not found: {args.extension_vsix}")

    editors = discover_vscode_editors()
    if not editors:
        raise SystemExit(
            "error: no VS Code-compatible editor command found on PATH. Install VS Code "
            "or add its `code` command to PATH, then re-run."
        )

    log(f"found {len(editors)} VS Code-compatible editor(s):")
    for label, command in editors:
        print(f"    - {label}  ({command})")

    if not args.verbose:
        begin_quiet_mode(LOG_FILE)
    try:
        with spinner(STEPS["starting"]) as status:
            failures = _install_extension(args, editors, status)
    except InstallCommandError as exc:
        report_command_failure(exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Installation cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
    finally:
        close_log()

    if failures:
        raise SystemExit(
            "error: VS Code extension installation failed for:\n  " + "\n  ".join(failures)
        )
    action = "removed from" if args.uninstall else "installed into"
    print(f"\nDone. Extension {action} {len(editors)} editor(s). Reload VS Code if it was open.")


if __name__ == "__main__":
    main()
