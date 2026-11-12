# -*- coding: utf-8 -*-
"""Checks that a single-binary release carries everything it promises and runs on its own.

    python scripts/release/binary/smoke.py dist/kvist

The binary must start, serve `--session-op`, hold the Node.js, the terminal UI bundle and the Rust
session program in its archive, and (where a pseudo-terminal exists) open the first-run setup screen
with no Node.js on PATH, which only works if it runs the packed one.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REQUIRED_IN_ARCHIVE = ("node/node", "frontends/tui/ink/dist-ui/kvist-tui.mjs", "kernel/session-rs/target/release/kvist-session")
ALTERNATE_SCREEN = b"\x1b[?1049h"
SETUP_SECONDS = 15


def run(binary: Path, *args: str, timeout: int = 120) -> str:
    done = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=timeout, check=True)
    return done.stdout


def check_version(binary: Path) -> None:
    print("version:", run(binary, "--version").strip())


def check_session_op(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as workspace:
        answer = json.loads(run(binary, "--workspace", workspace, "--session-op", "status"))
    assert answer["ok"] is True, answer
    print("session-op: ok")


def check_archive(binary: Path) -> None:
    listing = subprocess.run(
        [sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer", "-l", str(binary)],
        capture_output=True, text=True, timeout=300, check=True,
    ).stdout
    # Windows names its executables with an extension.
    names = listing.replace("node/node.exe", "node/node").replace("kvist-session.exe", "kvist-session")
    missing = [entry for entry in REQUIRED_IN_ARCHIVE if f"'{entry}'" not in names]
    assert not missing, f"the binary does not carry: {', '.join(missing)}"
    print("archive: carries", ", ".join(REQUIRED_IN_ARCHIVE))


def check_setup_screen_without_node(binary: Path) -> None:
    """A first run with an empty config opens the Ink setup screen although PATH holds no node."""
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios

    with tempfile.TemporaryDirectory() as config, tempfile.TemporaryDirectory() as workspace:
        environment = {"PATH": os.devnull, "HOME": config, "KVIST_CONFIG_DIR": config, "TERM": "xterm-256color", "LANG": "C.UTF-8"}
        pid, descriptor = pty.fork()
        if pid == 0:
            os.execve(str(binary), [str(binary), "--workspace", workspace, "--frontend", "tui"], environment)
        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        seen = b""
        deadline = time.monotonic() + SETUP_SECONDS
        try:
            while time.monotonic() < deadline and ALTERNATE_SCREEN not in seen:
                if not select.select([descriptor], [], [], 0.5)[0]:
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                seen += chunk
        finally:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    assert ALTERNATE_SCREEN in seen, "the setup screen did not open; the output was: " + seen[-400:].decode("utf-8", "replace")
    print("setup screen: opened with no node on PATH")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    binary = Path(sys.argv[1]).resolve(strict=True)
    check_version(binary)
    check_session_op(binary)
    check_archive(binary)
    if os.name == "posix":
        check_setup_screen_without_node(binary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
