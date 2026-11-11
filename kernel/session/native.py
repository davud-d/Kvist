# -*- coding: utf-8 -*-
"""The native session program (``kernel/session-rs``): where it is and how a request reaches it.

``kvist-session`` answers ``--session-op`` exactly as ``session_store.run_session_op``
does, on the same files and under the same locks, on Linux, macOS, the BSDs and Windows.
It is optional: built by ``cargo build --release`` in ``kernel/session-rs`` (the installer
does that when Rust is there), and whenever it is missing the Python implementation
serves the request.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SESSION_BIN_ENV = "KVIST_SESSION_BIN"
# What the launcher tells an interface it starts: the command for one-shot session
# operations, a JSON list like KVIST_CORE_CMD. Set only for a core on this machine.
SESSION_CMD_ENV = "KVIST_SESSION_CMD"
# PyInstaller unpacks the program at its checkout path (kvist.spec), so one path serves both.
_APP_ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent.parent)
PROGRAM_NAME = "kvist-session.exe" if os.name == "nt" else "kvist-session"
_BUILD = _APP_ROOT / "kernel" / "session-rs" / "target" / "release" / PROGRAM_NAME
# The operations and options the program reads; main.py's parser and session_store name
# the same ones (kernel/tests/test_session_native.py compares them).
NATIVE_OPS = frozenset(
    {
        "status", "list", "history", "create", "switch", "delete", "reset", "new", "launch",
        "input-history", "input-history-add",
    }
)
_TEXT_OPTIONS = frozenset({"--workspace", "--session-op", "--session-name"})
_NUMBER_OPTIONS = frozenset({"--history-limit", "--history-before", "--history-after"})


def find_session_binary() -> str | None:
    """The native session program, or None when Python has to serve session operations.

    ``KVIST_SESSION_BIN`` is the only place looked at when it is set, so naming a file
    that is not there switches the native program off; otherwise ``kvist-session`` on
    PATH, then a checkout's build.
    """
    explicit = os.environ.get(SESSION_BIN_ENV)
    candidates = (explicit,) if explicit else (shutil.which("kvist-session"), str(_BUILD))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def native_command(
    binary: str,
    workspace: str | Path,
    op: str,
    name: str | None = None,
    *,
    limit: int | None = None,
    before: int | None = None,
    after: int | None = None,
) -> list[str]:
    """The command line that runs one session operation in the native program."""
    # The program expands a bare ``~`` itself; ``~user`` needs the account database, which Python reads.
    command = [binary, "--workspace", os.path.expanduser(str(workspace)), "--session-op", op]
    options = (
        ("--session-name", name),
        ("--history-limit", limit),
        ("--history-before", before),
        ("--history-after", after),
    )
    for flag, value in options:
        if value is not None:
            command += [flag, str(value)]
    return command


def _is_plain_request(arguments: list[str]) -> bool:
    """Whether ``arguments`` are a session operation the program reads exactly as main.py's parser does."""
    if len(arguments) % 2:
        return False
    given = dict(zip(arguments[0::2], arguments[1::2]))
    for flag, value in given.items():
        if flag in _NUMBER_OPTIONS:
            digits = value.removeprefix("-")
            if not (digits.isascii() and digits.isdigit()):
                return False
        elif flag not in _TEXT_OPTIONS or value.startswith("-"):
            return False
    # ``~`` is left to the parser's side: ``~user`` needs the account database.
    workspace = given.get("--workspace", "")
    return given.get("--session-op") in NATIVE_OPS and bool(workspace) and not workspace.startswith("~")


def hand_over_plain_request(arguments: list[str]) -> None:
    """Become the native program for a plain ``--session-op`` request; return when Python has to serve it.

    Called before the core is imported: an interface asks for a session operation on every
    start and every switch, and most of what such a request cost was starting the core
    only to hand the request on. Anything but the program's own options (``--remote``, an
    ``=`` form, a mistake) returns here and goes through the parser as before.
    """
    if not _is_plain_request(arguments):
        return
    binary = find_session_binary()
    if binary is None:
        return
    try:
        if os.name == "nt":
            # execv on Windows starts a new process and lets this one exit at once, which
            # hands the caller back its prompt before the answer; wait for the program instead.
            import subprocess

            raise SystemExit(subprocess.call([binary, *arguments]))
        os.execv(binary, [binary, *arguments])
    except OSError:
        # The file is there but cannot run on this machine (another architecture,
        # a broken build); nothing has been done yet, so Python serves the request.
        return
