# -*- coding: utf-8 -*-
"""The machines Kvist knows: where each one is, how to sign in, which folder to work in.

Stored in ``<config dir>/machines.json`` (private, atomic). A password is never written here: it is used once
to install a key (``access.py``) and forgotten. Only the fields below are ever saved.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from kernel.config.user_config import get_user_config_path
from kernel.session.durable_json import read_json_object, update_json_object
from kernel.ssh.paths import RemoteError

FILENAME = "machines.json"
# Where a machine works when no folder was chosen; the server expands ``~``.
STANDARD_FOLDER = "~/Kvist Workspace"
MAX_FOLDERS = 10
MAX_RECENT_SESSIONS = 50
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


@dataclass(frozen=True, slots=True)
class Machine:
    name: str
    host: str  # as ssh takes it: ``user@name``, an IP, or a name from ~/.ssh/config
    port: int | None = None
    key: str | None = None  # a private key file; None means whatever ssh would use
    folder: str | None = None  # None means STANDARD_FOLDER
    folders: tuple[str, ...] = ()  # folders worked in before, newest first

    @property
    def work_folder(self) -> str:
        return self.folder or STANDARD_FOLDER

    @property
    def spec(self) -> str:
        """The address as ``--remote`` takes it."""
        return f"{self.host}:{self.port}" if self.port else self.host


@dataclass(frozen=True, slots=True)
class SessionRecord:
    machine: str
    folder: str
    session: str
    title: str
    updated: float


def is_valid_name(name: str) -> bool:
    return bool(_NAME.match(name))


def machines_path() -> Path:
    return get_user_config_path().parent / FILENAME


def _boundary() -> Path:
    folder = machines_path().parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve(strict=True)


def _read() -> dict:
    if not machines_path().exists():
        return {}
    return read_json_object(machines_path(), boundary=_boundary(), empty_as_missing=True)


def _update(change) -> dict:
    return update_json_object(machines_path(), change, boundary=_boundary(), empty_as_missing=True)


def _machine_from(name: str, raw: object) -> Machine | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("host"), str) or not raw["host"].strip():
        return None
    port = raw.get("port")
    folders = raw.get("folders")
    return Machine(
        name=name,
        host=raw["host"].strip(),
        port=port if isinstance(port, int) and 0 < port < 65536 else None,
        key=raw["key"] if isinstance(raw.get("key"), str) and raw["key"] else None,
        folder=raw["folder"] if isinstance(raw.get("folder"), str) and raw["folder"] else None,
        folders=tuple(item for item in folders if isinstance(item, str)) if isinstance(folders, list) else (),
    )


def _stored(machine: Machine) -> dict:
    return {
        "host": machine.host,
        "port": machine.port,
        "key": machine.key,
        "folder": machine.folder,
        "folders": list(machine.folders[:MAX_FOLDERS]),
    }


def list_machines() -> list[Machine]:
    raw = _read().get("machines")
    if not isinstance(raw, dict):
        return []
    found = (_machine_from(name, value) for name, value in sorted(raw.items()))
    return [machine for machine in found if machine is not None]


def get_machine(name: str) -> Machine | None:
    return next((machine for machine in list_machines() if machine.name == name), None)


def main_machine() -> Machine | None:
    name = _read().get("default")
    return get_machine(name) if isinstance(name, str) else None


def save_machine(machine: Machine) -> None:
    if not is_valid_name(machine.name):
        raise RemoteError("A machine name is 1-32 lowercase letters, digits, hyphens or underscores, starting with a letter or digit.")

    def change(data: dict) -> dict:
        machines = data.get("machines") if isinstance(data.get("machines"), dict) else {}
        machines[machine.name] = _stored(machine)
        data["machines"] = machines
        return data

    _update(change)


def delete_machine(name: str) -> None:
    def change(data: dict) -> dict:
        machines = data.get("machines") if isinstance(data.get("machines"), dict) else {}
        machines.pop(name, None)
        data["machines"] = machines
        if data.get("default") == name:
            data["default"] = None
        data["sessions"] = [item for item in data.get("sessions", []) if item.get("machine") != name]
        return data

    _update(change)


def set_main(name: str | None) -> None:
    def change(data: dict) -> dict:
        data["default"] = name
        return data

    _update(change)


def remember_folder(name: str, folder: str) -> None:
    """Put ``folder`` first in the machine's folders worked in."""
    machine = get_machine(name)
    if machine is None:
        return
    folders = (folder, *(item for item in machine.folders if item != folder))
    save_machine(Machine(machine.name, machine.host, machine.port, machine.key, machine.folder, folders[:MAX_FOLDERS]))


def record_session(machine: str, folder: str, session: str, title: str = "") -> None:
    """Note that ``session`` lives on ``machine`` in ``folder``; the newest are kept, and a title is not lost to a later note without one."""

    def change(data: dict) -> dict:
        key = (machine, folder, session)
        sessions = data.get("sessions", [])
        same = [item for item in sessions if (item.get("machine"), item.get("folder"), item.get("session")) == key]
        others = [item for item in sessions if item not in same]
        known_title = title or (same[0].get("title", "") if same else "")
        entry = {"machine": machine, "folder": folder, "session": session, "title": known_title, "updated": time.time()}
        data["sessions"] = [entry, *others][:MAX_RECENT_SESSIONS]
        return data

    _update(change)


def recent_sessions() -> list[SessionRecord]:
    records = []
    for item in _read().get("sessions", []):
        try:
            records.append(SessionRecord(item["machine"], item["folder"], item["session"], item.get("title", ""), float(item["updated"])))
        except (KeyError, TypeError, ValueError):
            continue
    return records
