#!/usr/bin/env python3
"""Install the ``kvist-web`` and ``kvist-web-bridge`` launchers beside Kvist CLI."""
from __future__ import annotations

import argparse
from pathlib import Path

import install_cli


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    core = install_cli.DEFAULT_LIB_DIR / install_cli.CORE_NAME
    if not core.is_file():
        raise SystemExit("error: install the Kvist CLI before the web frontend")
    web, bridge = install_cli.install_web_launchers(core, install_cli.DEFAULT_BIN_DIR)
    print(f"Installed {web}")
    print(f"Installed {bridge}")


if __name__ == "__main__":
    main()
