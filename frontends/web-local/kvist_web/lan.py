# -*- coding: utf-8 -*-
"""LAN mode of ``kvist-web``: a pairing code, a discovery beacon and the host check for private addresses.

The phone app runs no agent. It finds a computer on the same router, pairs with the code printed
there and then shows that computer's web interface. Everything here stays inside the local network:
the beacon answers only private addresses, and the host check accepts only IP literals of private
ranges, so a DNS-rebinding page cannot reach the controller by a name.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
from pathlib import Path

from .instance import controller_home

_LOG = logging.getLogger("kvist.web.lan")

BEACON_PORT = 9672
PROBE = b"KVIST-DISCOVER/1"
# No 0/O, 1/I/L: the code is read off a screen and typed on a phone.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12


def code_path() -> Path:
    return controller_home() / "lan-code"


def normalize_code(text: str) -> str:
    return "".join(char for char in text.upper() if char.isalnum())


def format_code(code: str) -> str:
    return "-".join(code[start:start + 4] for start in range(0, len(code), 4))


def load_code(path: Path | None = None, *, rotate: bool = False) -> str:
    """Return the saved pairing code, creating it on first use or when ``rotate`` asks for a new one."""
    path = path or code_path()
    if not rotate:
        try:
            saved = normalize_code(path.read_text(encoding="utf-8"))
        except OSError:
            saved = ""
        if len(saved) == CODE_LENGTH:
            return saved
    code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(code)
    return code


def is_private_host(name: str | None) -> bool:
    """True for an IP literal of a private, loopback or link-local range; a name is never accepted."""
    try:
        address = ipaddress.ip_address((name or "").strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def local_address() -> str | None:
    """The address this computer uses to reach the local network (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def banner(code: str, port: int, address: str | None, russian: bool) -> str:
    where = f"http://{address}:{port}" if address else f"http://<IP>:{port}"
    if russian:
        return (
            "Kvist открыт в локальной сети.\n"
            "  Телефон: открой приложение Kvist, выбери этот компьютер и введи код.\n"
            f"  Код:   {format_code(code)}\n"
            f"  Адрес: {where}  (для ручного ввода)\n"
            "Любой, кто в этой сети знает код, управляет агентом на этом компьютере.\n"
            "Новый код: kvist-web --lan --new-code"
        )
    return (
        "Kvist is open to your local network.\n"
        "  Phone:   open the Kvist app, pick this computer and enter the code.\n"
        f"  Code:    {format_code(code)}\n"
        f"  Address: {where}  (for manual entry)\n"
        "Anyone on this network who knows the code drives the agent on this computer.\n"
        "New code: kvist-web --lan --new-code"
    )


class Beacon:
    """Answers a discovery probe with the controller's name and port; reveals nothing else."""

    def __init__(self, http_port: int, name: str | None = None, port: int = BEACON_PORT) -> None:
        self._reply = json.dumps(
            {"service": "kvist-web", "version": 1, "name": name or socket.gethostname(), "port": http_port},
            separators=(",", ":"),
        ).encode("utf-8")
        self._port = port
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind the beacon port; raises ``OSError`` when it is taken (discovery is then off, pairing by address still works)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self._port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(0.4)
        self._socket = sock
        self._thread = threading.Thread(target=self._serve, name="kvist-beacon", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._socket.getsockname()[1] if self._socket else self._port

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._socket:
            self._socket.close()

    def _serve(self) -> None:
        assert self._socket is not None
        while not self._stop.is_set():
            try:
                data, source = self._socket.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if data.strip() != PROBE or not is_private_host(source[0]):
                continue
            try:
                self._socket.sendto(self._reply, source)
            except OSError as error:
                _LOG.warning("beacon reply failed: %s", error, extra={"component": "lan"})
