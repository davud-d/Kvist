# -*- coding: utf-8 -*-
"""sudo in the system folder: the command's form, and the password's one-way trip to sudo.

Only ``sudo <program> <arguments>`` is accepted: sudo's own options could keep the environment
(-E), open a root shell (-s, -i) or pick another user, and a pipe would put other processes next
to the password's channel. The program and its arguments pass the same hard validation as any
command, so what is forbidden without sudo stays forbidden with it.

The password lives in the executor's memory for the session. sudo gets it through ``-A``: its
askpass helper reads a FIFO in a private directory made for this one run, served once. It is in no
environment, no argv and no file, and the model never sees it.
"""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from kernel.sandbox.terminal_types import HardDenyError

SUDO_REASON = (
    "runs as administrator (root) through sudo, outside the sandbox / "
    "запуск от имени администратора (root) через sudo, вне песочницы"
)
PASSWORD_TITLE = "Password for sudo / Пароль для sudo"
PASSWORD_HELP = (
    "Kvist keeps it in memory until this session ends and hands it only to sudo; the model never sees it. / "
    "Kvist держит его в памяти до конца сессии и передаёт только sudo; модель его не видит."
)
_ASKPASS_SCRIPT = '#!/bin/sh\nexec cat "$KVIST_SUDO_FIFO"\n'
_SERVE_POLL_SECONDS = 0.02
_VERIFY_SECONDS = 30


def sudo_inner_command(command: list[str]) -> list[str]:
    """The program and arguments sudo is asked to run; HardDenyError for any other form."""
    if "|" in command:
        raise HardDenyError("Hard security denial: a sudo command cannot be part of a pipe")
    inner = command[1:]
    if not inner or inner[0].startswith("-"):
        raise HardDenyError(
            "Hard security denial: sudo takes no options here, only: sudo <program> <arguments>"
        )
    return inner


def sudo_argv(materialized: list[str]) -> list[str]:
    """``[sudo, program, ...]`` as it is spawned: password from the askpass helper, never from a cached ticket."""
    return [materialized[0], "-A", "-k", "--", *materialized[1:]]


class AskpassFeed:
    """A private directory with sudo's askpass helper and the FIFO it reads, for one run."""

    def __init__(self) -> None:
        # mkdtemp makes the directory 0700: nobody else can open the FIFO or replace the helper.
        self.directory = Path(tempfile.mkdtemp(prefix="kvist-sudo-"))
        self.fifo = self.directory / "password"
        self.helper = self.directory / "askpass"
        os.mkfifo(self.fifo, 0o600)
        self.helper.write_text(_ASKPASS_SCRIPT, encoding="utf-8")
        self.helper.chmod(0o700)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def serving(self) -> bool:
        return self._thread is not None

    def env(self) -> dict[str, str]:
        return {"SUDO_ASKPASS": str(self.helper), "KVIST_SUDO_FIFO": str(self.fifo)}

    def serve(self, password: str) -> None:
        self._thread = threading.Thread(target=self._serve, args=(password,), name="kvist-sudo-askpass", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        shutil.rmtree(self.directory)

    def _serve(self, password: str) -> None:
        while not self._stop.is_set():
            try:
                handle = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
            except OSError as error:
                # ENXIO: the helper has not opened the FIFO yet. Anything else: it is gone.
                if error.errno != errno.ENXIO:
                    return
                self._stop.wait(_SERVE_POLL_SECONDS)
                continue
            try:
                os.set_blocking(handle, True)
                os.write(handle, (password + "\n").encode("utf-8"))
            finally:
                os.close(handle)
            # sudo asks again only after a wrong password: the helper then finds no FIFO and
            # fails at once, instead of waiting for a second answer that never comes.
            self.fifo.unlink()
            return


def sudo_accepts(sudo_path: str, password: str) -> bool:
    """Whether sudo takes ``password``, checked with ``sudo -v`` before any approved command runs."""
    feed = AskpassFeed()
    try:
        feed.serve(password)
        done = subprocess.run(
            [sudo_path, "-A", "-k", "-v"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **feed.env()},
            timeout=_VERIFY_SECONDS,
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    finally:
        feed.close()
    return done.returncode == 0
