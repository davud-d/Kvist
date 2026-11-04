# -*- coding: utf-8 -*-
"""``/machines`` and ``/remote`` as commands any frontend can run.

The dialogs are ordinary input requests (choice, text, secret): the core says what it needs to know and each frontend
draws that in its own way. When the person has decided, the answer is an ``effects.relaunch`` for the frontend to act on
(UI_API.md section 8g): end itself so the launcher starts it again where the person chose. The same functions run in
a core on this computer and, for a frontend that talks to a server, in the proxy, because these are about this computer's
ssh and saved machines, not the server's. They do not check the machine: the interface that opens on it does (``Proxy``).
"""
from __future__ import annotations

from dataclasses import dataclass

from kernel.ssh import messages
from kernel.ssh.command import parse_target
from kernel.ssh.machines import Machine
from kernel.ssh.machines_wizard import Choice, run_menu
from kernel.ssh.paths import RemoteError

NAMES = ("machines", "remote")


@dataclass(frozen=True, slots=True)
class Outcome:
    ok: bool
    message: str
    effects: dict


def _line(*parts: str | None) -> str:
    """One line of the launcher's switch file: tab-separated, so nothing in it may contain a tab or a newline."""
    return "\t".join((part or "").replace("\t", " ").replace("\n", " ") for part in parts)


def _relaunch(switch: str, label: str, **fields: object) -> dict:
    return {"relaunch": {"switch": switch, "label": label, **fields}}


def local_outcome(language: str | None = None) -> Outcome:
    language = language or messages.ui_language()
    return Outcome(True, messages.text("opening_local", language), _relaunch("@local", "this computer", machine=None, host=None, folder=None, session=None))


def _open_machine(choice: Choice, language: str) -> Outcome:
    """Ask for the interface to start again on the machine; the interface checks the machine once it is open (``Proxy``)."""
    machine: Machine = choice.machine
    folder = choice.folder or machine.work_folder
    label = f"{machine.name} ({folder})"
    switch = _line("@machine", machine.name, folder, choice.session)
    effects = _relaunch(switch, label, machine=machine.name, host=machine.spec, folder=folder, session=choice.session)
    return Outcome(True, messages.text("opening", language, label=label, name=machine.name), effects)


def machines_command(ui, args: list[str] | None = None) -> Outcome | None:
    """The dialog; None when the person backed out of it."""
    language = messages.ui_language()
    choice = run_menu(ui, language)
    if choice is None:
        return None
    return local_outcome(language) if choice.machine is None else _open_machine(choice, language)


def remote_command(ui, args: list[str]) -> Outcome:
    """``/remote user@host /folder`` for a server that is not saved, ``/remote off`` to come back."""
    language = messages.ui_language()
    if [word.lower() for word in args] == ["off"]:
        return local_outcome(language)
    if len(args) < 2:
        return Outcome(False, messages.text("remote_usage", language), {})
    folder = " ".join(args[1:])
    try:
        target = parse_target(args[0], folder)
    except RemoteError as error:
        return Outcome(False, str(error), {})
    effects = _relaunch(_line(args[0], folder), target.label, machine=None, host=args[0], folder=folder, session=None)
    return Outcome(True, messages.text("opening_remote", language, label=f"{target.label} {folder}", host=args[0], folder=folder), effects)
