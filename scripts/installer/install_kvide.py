#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Give kvide a Kvist commit-message generator.

kvide runs only the generators listed in a TOML file passed with
``--generators``. This installer copies the adapter
(``frontends/kvide/kvist_generator.py``) next to the installed ``kvist-core``,
and writes a ``generators.toml`` that points at both:

    python scripts/installer/install_kvide.py
    python scripts/installer/install_kvide.py --uninstall

Then start the editor with ``kvide --generators <printed path>``. Nothing in
kvide's own settings or any existing generators file is touched. The binary is
``install_cli.py``'s to install, so run that one first.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from _common import REPO_ROOT, log, remove_path
from install_cli import CORE_NAME, DEFAULT_LIB_DIR

ADAPTER_SOURCE = REPO_ROOT / "frontends" / "kvide" / "kvist_generator.py"
INSTALL_DIR_NAME = "kvide"
ADAPTER_NAME = ADAPTER_SOURCE.name
CONFIG_NAME = "generators.toml"
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


def render_config(adapter: Path, core: Path) -> str:
    """The generators file kvide reads.

    TOML basic strings share JSON's escaping, so ``json.dumps`` quotes Windows
    paths correctly.

    :param adapter: installed adapter script.
    :param core: installed ``kvist-core`` binary.
    :returns: the TOML text.
    """
    command = json.dumps([sys.executable, str(adapter), "--core", str(core)])
    return (
        "[[generators]]\n"
        'name = "Kvist"\n'
        f"command = {command}\n"
        f"icon = {json.dumps(ICON_NAME)}\n"
        f"color = {json.dumps(ICON_COLOR)}\n"
        f"timeout = {TIMEOUT_SECONDS}\n"
    )


def install(lib_dir: Path) -> Path:
    """Write the adapter, icon and generators file.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :returns: the generators file to pass to ``kvide --generators``.
    """
    core = resolve_core(lib_dir)
    target = lib_dir / INSTALL_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    adapter = target / ADAPTER_NAME
    shutil.copy2(ADAPTER_SOURCE, adapter)
    (target / ICON_NAME).write_text(ICON_GLYPH + "\n", encoding="utf-8")
    config = target / CONFIG_NAME
    config.write_text(render_config(adapter, core), encoding="utf-8")
    log(f"wrote {config}")
    return config


def uninstall(lib_dir: Path) -> bool:
    """Remove the directory this installer created.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :returns: True when something was removed.
    """
    removed = remove_path(lib_dir / INSTALL_DIR_NAME)
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
    parser.add_argument("-v", "--verbose", action="store_true", help="Accepted for install.py; this installer spawns nothing.")
    args = parser.parse_args()

    if args.uninstall:
        uninstall(args.lib_dir)
        print("\nDone. The kvide generator is removed.")
        return
    config = install(args.lib_dir)
    print(f"\nDone. Start kvide with: kvide --generators {config}")


if __name__ == "__main__":
    main()
