# -*- coding: utf-8 -*-
"""LAN mode of ``kvist-web``: who this computer is, the discovery beacon and the host check for private addresses.

The phone app runs no agent. It finds a computer on the same router, asks to be trusted (the computer
asks its owner once) and then drives that computer's web interface. Everything here stays inside the
local network: the beacon answers only private addresses, and the host check accepts only IP literals of
private ranges, so a DNS-rebinding page cannot reach the controller by a name.
"""
from __future__ import annotations

import getpass
import ipaddress
import json
import logging
import os
import platform
import socket
import threading
import uuid
from pathlib import Path

from .instance import controller_home

_LOG = logging.getLogger("kvist.web.lan")

BEACON_PORT = 9672
PROBE = b"KVIST-DISCOVER/1"
BEACON_VERSION = 2


def computer_id_path() -> Path:
    return controller_home() / "computer-id"


def computer_id(path: Path | None = None) -> str:
    """A stable name for this computer, so a phone knows it again after the router gives it a new address."""
    path = path or computer_id_path()
    try:
        saved = path.read_text(encoding="utf-8").strip()
    except OSError:
        saved = ""
    if len(saved) == 32:
        return saved
    fresh = uuid.uuid4().hex
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fresh, encoding="utf-8")
    return fresh


def os_info() -> tuple[str, str]:
    """('linux' | 'macos' | 'windows', a name for a person: 'Ubuntu 24.04', 'macOS 15.1', 'Windows 11')."""
    system = platform.system()
    if system == "Windows":
        return "windows", f"Windows {platform.release()}".strip()
    if system == "Darwin":
        return "macos", f"macOS {platform.mac_ver()[0]}".strip()
    try:
        pretty = platform.freedesktop_os_release().get("PRETTY_NAME", "")
    except OSError:
        pretty = ""
    return "linux", pretty or "Linux"


def user_name() -> str:
    try:
        return getpass.getuser()
    except (OSError, KeyError, ImportError):
        # No passwd entry (a container, a service account): the environment may still know.
        return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def identity(http_port: int, *, host: str | None = None) -> dict[str, object]:
    """What the beacon tells a phone: enough to recognise the computer, nothing to act on."""
    kind, label = os_info()
    return {
        "service": "kvist-web", "version": BEACON_VERSION, "id": computer_id(),
        "name": host or socket.gethostname(), "os": kind, "os_name": label, "user": user_name(), "port": http_port,
    }


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


def banner(who: dict[str, object], address: str | None, russian: bool) -> str:
    where = f"http://{address}:{who['port']}" if address else f"http://<IP>:{who['port']}"
    label = f"{who['name']} ({who['os_name']}, {who['user']})"
    if russian:
        return (
            f"Kvist виден телефонам в локальной сети как {label}.\n"
            "  Новый телефон спросит разрешения здесь, на компьютере, один раз.\n"
            f"  Адрес для ручного ввода: {where}\n"
            "Доверенный телефон управляет агентом на этом компьютере. Забыть все телефоны: kvist-web --forget-phones"
        )
    return (
        f"Kvist is visible to phones on the local network as {label}.\n"
        "  A new phone asks for permission here, on this computer, once.\n"
        f"  Address for manual entry: {where}\n"
        "A trusted phone drives the agent on this computer. Forget every phone: kvist-web --forget-phones"
    )


class Beacon:
    """Answers a discovery probe with who this computer is; reveals nothing else."""

    def __init__(self, http_port: int, port: int = BEACON_PORT, who: dict[str, object] | None = None) -> None:
        self._reply = json.dumps(who or identity(http_port), separators=(",", ":")).encode("utf-8")
        self._port = port
        self._stop = threading.Event()
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Bind the beacon port; raises ``OSError`` when it is taken (discovery is then off, pairing by address still works)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Two users on one computer each run a beacon: both must hear the broadcast (macOS needs REUSEPORT for that).
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
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
