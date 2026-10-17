#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Give kvide a Kvist commit-message generator.

kvide runs the generators from ``--generators FILE``, or else from the user
file ``<config dir>/kvide/generators.toml``. This installer copies the adapter
(``frontends/kvide/kvist_generator.py``) next to the installed ``kvist-core``
and writes the user file, so a plain ``kvide`` shows the Kvist button:

    python scripts/installer/install_kvide.py
    python scripts/installer/install_kvide.py --uninstall

A user file that Kvist did not write is never overwritten or removed; the
installer then leaves a copy next to the adapter and prints the
``kvide --generators`` command for it. The binary is ``install_cli.py``'s to
install, so run that one first.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from _common import IS_WINDOWS, REPO_ROOT, log, remove_path, warn
from install_cli import CORE_NAME, DEFAULT_LIB_DIR

ADAPTER_SOURCE = REPO_ROOT / "frontends" / "kvide" / "kvist_generator.py"
INSTALL_DIR_NAME = "kvide"
ADAPTER_NAME = ADAPTER_SOURCE.name
CONFIG_NAME = "generators.toml"
# First line of every file this installer writes; it is how a later run tells
# its own file from one the user wrote.
MARKER = "# managed by kvist"
ICON_NAME = "icon.txt"
# Nerd Font "terminal" glyph, the one kvide's own generator example uses.
ICON_GLYPH = ""
# Sky blue, applied by kvide to the icon only.
ICON_COLOR = "#4fc3f7"
# A model call plus the diff upload; kvide's default of 60 s is tight for a large change.
TIMEOUT_SECONDS = 120


def resolve_core(lib_dir: Path) -> Path:
    """Absolute path of the core binary the adapter will run.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :returns: the binary path.
    :raises SystemExit: when the CLI has not been installed there yet.
    """
    core = lib_dir / CORE_NAME
    if not core.is_file():
        raise SystemExit(
            f"error: {core} is missing.\n"
            "Run scripts/installer/install_cli.py first. This installer only wires "
            "kvide to the binary and never builds or copies it."
        )
    return core


def user_config_path() -> Path:
    """The file kvide reads when started without ``--generators``.

    Mirrors ``kvide.cli.default_generators_path``: ``%APPDATA%`` on Windows,
    else an absolute ``$XDG_CONFIG_HOME``, else ``~/.config``.

    :returns: the path, which may not exist yet.
    """
    if IS_WINDOWS:
        value = os.environ.get("APPDATA")
        directory = Path(value) if value else Path.home() / "AppData" / "Roaming"
    else:
        value = os.environ.get("XDG_CONFIG_HOME")
        directory = Path(value) if value and Path(value).is_absolute() else Path.home() / ".config"
    return directory / "kvide" / CONFIG_NAME


def is_ours(path: Path) -> bool:
    """Whether ``path`` is a file this installer wrote."""
    try:
        return path.read_text(encoding="utf-8").startswith(MARKER)
    except (OSError, UnicodeDecodeError):
        return False  # unreadable means we cannot claim it, so it is left alone


def render_config(adapter: Path, core: Path, icon: Path) -> str:
    """The generators file kvide reads.

    TOML basic strings share JSON's escaping, so ``json.dumps`` quotes Windows
    paths correctly.

    :param adapter: installed adapter script.
    :param core: installed ``kvist-core`` binary.
    :param icon: installed icon file; absolute because the config may live elsewhere.
    :returns: the TOML text.
    """
    command = json.dumps([sys.executable, str(adapter), "--core", str(core)])
    return (
        f"{MARKER}\n"
        "[[generators]]\n"
        'name = "Kvist"\n'
        f"command = {command}\n"
        f"icon = {json.dumps(str(icon))}\n"
        f"color = {json.dumps(ICON_COLOR)}\n"
        f"timeout = {TIMEOUT_SECONDS}\n"
    )


def install(lib_dir: Path, user_config: Path) -> tuple[Path, bool]:
    """Write the adapter, icon and generators files.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :param user_config: the file kvide reads by default.
    :returns: the generators file for ``kvide --generators`` and whether the
        user file is Kvist's, so that a plain ``kvide`` picks it up.
    """
    core = resolve_core(lib_dir)
    target = lib_dir / INSTALL_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    adapter = target / ADAPTER_NAME
    shutil.copy2(ADAPTER_SOURCE, adapter)
    icon = target / ICON_NAME
    icon.write_text(ICON_GLYPH + "\n", encoding="utf-8")
    text = render_config(adapter, core, icon)
    local = target / CONFIG_NAME
    local.write_text(text, encoding="utf-8")
    log(f"wrote {local}")
    if user_config.exists() and not is_ours(user_config):
        warn(f"{user_config} is not Kvist's; left untouched")
        return local, False
    user_config.parent.mkdir(parents=True, exist_ok=True)
    user_config.write_text(text, encoding="utf-8")
    log(f"wrote {user_config}")
    return user_config, True


def uninstall(lib_dir: Path, user_config: Path) -> bool:
    """Remove the files this installer created.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :param user_config: the file kvide reads by default; removed only if Kvist wrote it.
    :returns: True when something was removed.
    """
    removed = remove_path(lib_dir / INSTALL_DIR_NAME)
    if is_ours(user_config):
        remove_path(user_config)
        removed = True
    log("removed the kvide generator" if removed else "no kvide generator installed; nothing to do")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true", help="Remove the generator files instead of writing them.")
    parser.add_argument(
        "--lib-dir",
        type=Path,
        default=DEFAULT_LIB_DIR,
        help=f"Where install_cli.py put the binary (default: {DEFAULT_LIB_DIR}).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="The user generators file kvide reads by default (default: kvide's own location).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Accepted for install.py; this installer spawns nothing.")
    args = parser.parse_args()

    user_config = args.config or user_config_path()
    if args.uninstall:
        uninstall(args.lib_dir, user_config)
        print("\nDone. The kvide generator is removed.")
        return
    config, is_default = install(args.lib_dir, user_config)
    if is_default:
        print("\nDone. A plain `kvide` now shows the Kvist button.")
    else:
        print(f"\nDone. {user_config} is yours, so start kvide with: kvide --generators {config}")


if __name__ == "__main__":
    main()
