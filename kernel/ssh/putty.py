# -*- coding: utf-8 -*-
"""PuTTY's ``plink`` as the ssh client, for a Windows computer that has PuTTY and no OpenSSH.

Kvist talks to a machine through whichever client is there: ``KVIST_SSH`` names one; without it, Windows prefers
``plink`` when it is installed and every other system uses ``ssh``. The command lines differ (``-P`` not ``-p``,
``-batch`` not ``BatchMode``, keys in ``.ppk``) and plink has neither ``ssh-keyscan`` nor a known_hosts file, so the
host key the person confirmed is kept in a small file of Kvist's own and handed to plink with ``-hostkey`` each time.
PuTTY's registry is never touched.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from kernel.config.user_config import get_user_config_path
from kernel.session.durable_json import read_json_object, update_json_object

FILENAME = "putty_hosts.json"
# What plink prints about a host key it does not know, e.g. ``ssh-ed25519 255 SHA256:AbC...``.
_FINGERPRINT = re.compile(r"(ssh-ed25519|ssh-rsa|ssh-dss|ecdsa-sha2-\S+)\s+\d+\s+(SHA256:[A-Za-z0-9+/]{43})")


def ssh_program() -> str:
    explicit = os.environ.get("KVIST_SSH")
    if explicit:
        return explicit
    return "plink" if sys.platform == "win32" and shutil.which("plink") else "ssh"


def uses_putty() -> bool:
    return re.split(r"[\\/]", ssh_program())[-1].lower().startswith("plink")  # either kind of slash, whatever system this is


def _hosts_path() -> Path:
    return get_user_config_path().parent / "ssh" / FILENAME


def _boundary() -> Path:
    folder = _hosts_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve(strict=True)


def host_id(host: str, port: int | None) -> str:
    return f"{host.rsplit('@', 1)[-1]}:{port or 22}"


def known_host_key(host: str, port: int | None) -> str | None:
    if not _hosts_path().exists():
        return None
    stored = read_json_object(_hosts_path(), boundary=_boundary(), empty_as_missing=True).get(host_id(host, port))
    return stored if isinstance(stored, str) and stored.startswith("SHA256:") else None


def remember_host_key(host: str, port: int | None, fingerprint: str) -> None:
    def change(data: dict) -> dict:
        data[host_id(host, port)] = fingerprint
        return data

    update_json_object(_hosts_path(), change, boundary=_boundary(), empty_as_missing=True)


# A fingerprint no server has. plink does not reliably refuse a host it has not been told to trust (on Linux it let a login
# through with nothing confirmed), so a host the person has not confirmed gets this one and the connection fails.
UNCONFIRMED = "SHA256:" + "A" * 43


def plink_options(host: str, port: int | None, key: str | None) -> list[str]:
    """No terminal and no questions; the host key only if it is the one the person confirmed; only the chosen key."""
    # plink has no option for keep-alives or a connect timeout; the proxy's own pings notice a dead link.
    options = ["-batch", "-ssh", "-T"]
    if port:
        options += ["-P", str(port)]
    if key:
        options += ["-i", key, "-noagent"]
    options += ["-hostkey", known_host_key(host, port) or UNCONFIRMED]
    return options


def plink_prefix(host: str, port: int | None, key: str | None) -> list[str]:
    return [ssh_program(), *plink_options(host, port, key), host]


def fingerprint_in(output: str) -> tuple[str, str] | None:
    """``(key type, SHA256:…)`` out of what plink wrote about a host key, or None."""
    found = _FINGERPRINT.search(output)
    return (found.group(1), found.group(2)) if found else None

