# -*- coding: utf-8 -*-
"""A real ``sshd`` run by the test's own user on a free port, so tests can use genuine ssh with no root and no network.

It accepts one client key. ``SetEnv`` gives the sessions it starts the test's HOME, PATH and config folder, so what
runs "on the server" (the attach shim, the daemon's files) lands in the test's temporary folder, not in the user's real one.
"""
from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

CORE_ENTRY = Path(__file__).resolve().parents[1] / "__main__.py"


def sshd_program() -> str | None:
    for candidate in (shutil.which("sshd"), "/usr/bin/sshd", "/usr/sbin/sshd"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return None


class LocalSshd:
    def __init__(self, root: Path, home: Path, config_dir: Path, extra_env: dict[str, str] | None = None) -> None:
        self.root = root
        self.home = home
        self.user = os.environ.get("USER") or Path(home).owner()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.host_key = root / "host_key"
        self.client_key = root / "client_key"
        for key in (self.host_key, self.client_key):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        (root / "authorized_keys").write_text(Path(f"{self.client_key}.pub").read_text(), encoding="utf-8")
        (root / "authorized_keys").chmod(0o600)
        self.install_core_shim(home)
        config = root / "sshd_config"
        config.write_text(
            "\n".join(
                [
                    f"Port {self.port}",
                    "ListenAddress 127.0.0.1",
                    f"HostKey {self.host_key}",
                    f"AuthorizedKeysFile {root / 'authorized_keys'}",
                    "PasswordAuthentication no",
                    "KbdInteractiveAuthentication no",
                    "StrictModes no",
                    *(["UsePAM no"] if not sys.platform.startswith("openbsd") else []),  # OpenBSD's sshd has no PAM and rejects the option
                    "PidFile none",
                    "SetEnv " + " ".join(f'{name}="{value}"' for name, value in {"HOME": home, "PATH": self._server_path(), "KVIST_CONFIG_DIR": config_dir, **(extra_env or {})}.items()),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.log = root / "sshd.log"
        self._process = subprocess.Popen(
            [sshd_program(), "-D", "-e", "-f", str(config)],
            stdout=open(self.log, "w"),
            stderr=subprocess.STDOUT,
        )
        self._wait_until_listening()

    @staticmethod
    def _server_path() -> str:
        """The interpreter running the tests comes first: a bare sshd session has the system PATH, whose ``python3`` may be older than the core needs."""
        return os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", os.defpath)])

    @staticmethod
    def install_core_shim(home: Path) -> None:
        """Where the server end looks when ``kvist-core`` is not on PATH."""
        shim = home / ".local" / "share" / "kvist" / "kvist-core"
        shim.parent.mkdir(parents=True, exist_ok=True)
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CORE_ENTRY}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

    def _wait_until_listening(self) -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"sshd exited: {self.log.read_text()}")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                    return
            time.sleep(0.05)
        raise RuntimeError("sshd did not start listening")

    @property
    def address(self) -> str:
        return f"{self.user}@127.0.0.1"

    def ssh_wrapper(self, directory: Path, known_hosts: Path) -> Path:
        """A stand-in for the ``ssh`` program: the real one, told to ignore the user's own config and known hosts."""
        wrapper = directory / "ssh"
        wrapper.write_text(
            f'#!/bin/sh\nexec ssh -F /dev/null -o UserKnownHostsFile="{known_hosts}" -o StrictHostKeyChecking=yes "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
        return wrapper

    def stop(self) -> None:
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
