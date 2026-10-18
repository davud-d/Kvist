#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Install what building Kvist Glass and the voice screen needs: a C compiler and Rust.

Kvist Glass (``frontends/tui/glass``) and the voice screen (``frontends/voice/screen``)
are Rust programs. ``install_cli.py`` builds them into the ``kvist`` binary whenever
``cargo`` is there, so the CLI installer waits for this one when both are chosen.

    python scripts/installer/install_rust.py

- The C compiler is what Rust links with. A missing one is installed with the
  system's package manager (apt, dnf, zypper or pacman) through ``sudo -n``;
  when that would need a password, the exact command is printed instead.
- Rust comes from rustup, its official installer (``https://sh.rustup.rs``),
  with the minimal profile. rustup adds ``~/.cargo/bin`` to the shell's PATH
  the way it always does. A Rust that is already installed is left as it is.

Both draw with the kitty graphics protocol, which Windows terminals do not
have, so on Windows this installer does nothing.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from _common import IS_MACOS, IS_WINDOWS, InstallCommandError, probe_command, report_command_failure, run_logged

RUSTUP_URL = "https://sh.rustup.rs"
# The system package that brings a C compiler, per package manager.
_COMPILER_PACKAGES = (
    ("apt-get", ["apt-get", "install", "-y", "build-essential"]),
    ("dnf", ["dnf", "install", "-y", "gcc"]),
    ("zypper", ["zypper", "--non-interactive", "install", "gcc"]),
    ("pacman", ["pacman", "-S", "--needed", "--noconfirm", "gcc"]),
)


def find_cargo() -> str | None:
    """``cargo`` on PATH, or where rustup puts it (a fresh install is not on PATH yet)."""
    found = shutil.which("cargo")
    if found:
        return found
    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    candidate = cargo_home / "bin" / ("cargo.exe" if IS_WINDOWS else "cargo")
    return str(candidate) if candidate.is_file() and os.access(candidate, os.X_OK) else None


def has_c_compiler() -> bool:
    return any(shutil.which(name) for name in ("cc", "gcc", "clang"))


def compiler_install_commands() -> list[list[str]] | None:
    """The commands that install a C compiler here, or None without a known package manager."""
    for manager, command in _COMPILER_PACKAGES:
        if shutil.which(manager):
            # A stale apt index fails the install of a package it no longer lists.
            return [["apt-get", "update"], command] if manager == "apt-get" else [command]
    return None


def _as_root(commands: list[list[str]]) -> list[list[str]]:
    """The commands with ``sudo -n``; SystemExit with the commands to run when sudo would ask."""
    if os.geteuid() == 0:
        return commands
    if shutil.which("sudo") and probe_command(["sudo", "-n", "true"]).returncode == 0:
        return [["sudo", "-n", *command] for command in commands]
    by_hand = "\n".join("    sudo " + " ".join(command) for command in commands)
    raise SystemExit(
        "error: a C compiler is needed to build Kvist Glass and the voice screen, and installing one needs your password.\n"
        f"Run:\n{by_hand}\nthen run this installer again."
    )


def install_compiler() -> None:
    if has_c_compiler():
        print("C compiler found.")
        return
    if IS_MACOS:
        raise SystemExit("error: no C compiler; run `xcode-select --install`, then this installer again.")
    commands = compiler_install_commands()
    if commands is None:
        raise SystemExit("error: no C compiler and no known package manager; install gcc, then this installer again.")
    print("Installing a C compiler ...")
    for command in _as_root(commands):
        run_logged(command)


def install_rust() -> str:
    cargo = find_cargo()
    if cargo is not None:
        print(f"Rust found: {cargo}")
        return cargo
    print("Installing Rust with rustup ...")
    with tempfile.TemporaryDirectory(prefix="kvist-rustup-") as scratch:
        script = Path(scratch) / "rustup-init.sh"
        with urllib.request.urlopen(RUSTUP_URL, timeout=60) as response:
            script.write_bytes(response.read())
        run_logged(["sh", str(script), "-y", "--profile", "minimal"])
    cargo = find_cargo()
    if cargo is None:
        raise SystemExit("error: rustup finished, but cargo was not found in ~/.cargo/bin")
    return cargo


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    if IS_WINDOWS:
        print("Kvist Glass and the voice screen need kitty graphics, which Windows terminals do not have; nothing to install.")
        return
    try:
        install_compiler()
        cargo = install_rust()
    except InstallCommandError as error:
        report_command_failure(error)
        sys.exit(1)
    print(f"Done. Rust is ready ({cargo}); the CLI installer builds Kvist Glass and the voice screen with it.")


if __name__ == "__main__":
    main()
