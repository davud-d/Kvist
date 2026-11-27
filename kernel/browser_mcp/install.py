# -*- coding: utf-8 -*-
"""``kvist --browser-bridge install|status``: put the bridge where a Firefox-family browser loads it.

The browser loads the bridge from ``~/.kvist/browser-bridge`` through its own autoconfig
(firefox/kvist-autoconfig.cfg). Kvist can write that folder; the loader itself belongs in the
browser's install folder, which needs the administrator once, so this prints the commands.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from kernel.browser_mcp.endpoint import bridge_folder, bridge_sockets

SOURCE = Path(__file__).parent / "firefox"
BRIDGE_FILES = ("bridge.js", "bridge-frame.js")
LOADER_FILES = ("kvist-autoconfig.cfg", "autoconfig.js")
BROWSER_FOLDERS = ("/opt/waterfox", "/usr/lib/waterfox", "/usr/lib/firefox", "/opt/firefox", "/usr/lib/librewolf",
                   "/opt/librewolf", "/usr/lib/firefox-developer-edition")


def browser_folders() -> list[Path]:
    return [Path(folder) for folder in BROWSER_FOLDERS if (Path(folder) / "defaults" / "pref").is_dir()]


def loader_installed(folder: Path) -> bool:
    cfg = folder / "kvist-autoconfig.cfg"
    return cfg.is_file() and (folder / "defaults" / "pref" / "autoconfig.js").is_file() \
        and "browser-bridge" in cfg.read_text(encoding="utf-8", errors="replace")


def install() -> int:
    target = bridge_folder()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in (*BRIDGE_FILES, *LOADER_FILES):
        shutil.copyfile(SOURCE / name, target / name)
    print(f"Kvist browser bridge copied to {target}.")
    folders = browser_folders()
    missing = [folder for folder in folders if not loader_installed(folder)]
    if folders and not missing:
        print("The browser already loads it; restart the browser to pick up this version.")
        return 0
    print("Once, the browser needs its loader (as administrator), then a full restart:")
    for folder in missing or [Path("<browser install folder>")]:
        print(f"  sudo cp {target / 'kvist-autoconfig.cfg'} {folder}/")
        print(f"  sudo cp {target / 'autoconfig.js'} {folder}/defaults/pref/")
    print("To remove it later, delete those two files from the browser's folder.")
    return 0


def status() -> int:
    folder = bridge_folder()
    print(f"bridge files: {'installed' if all((folder / name).is_file() for name in BRIDGE_FILES) else 'missing'} ({folder})")
    for browser in browser_folders():
        print(f"loader in {browser}: {'yes' if loader_installed(browser) else 'no'}")
    live = [path for path in bridge_sockets() if path.exists()]
    print(f"running browser with the bridge: {live[0] if live else 'none (restart the browser after installing)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kvist --browser-bridge",
                                     description="The Kvist bridge for Firefox-family browsers.")
    parser.add_argument("action", choices=("install", "status"))
    args, _unknown = parser.parse_known_args(argv)
    return install() if args.action == "install" else status()


if __name__ == "__main__":
    sys.exit(main())
