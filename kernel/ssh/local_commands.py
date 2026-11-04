# -*- coding: utf-8 -*-
"""The commands a frontend on a server still answers on this computer.

``/machines`` and ``/remote`` are about this computer's saved machines and ssh, so the proxy, which stands between the
frontend and the server's core, runs them itself with the same dialogs a local core would use: the frontend sees input
requests and a ``command_result``, exactly as for any command, and never learns the difference.
"""
from __future__ import annotations

import threading
from collections.abc import Callable

from kernel.protocol.event_stdio import CommandResultEvent
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel, InputResponse
from kernel.session.durable_json import JsonStorageError
from kernel.ssh.commands import NAMES, Outcome, local_outcome, machines_command, remote_command
from kernel.ssh.paths import RemoteError

_PUMP_SECONDS = 0.1


class LocalCommands:
    def __init__(self, write_event: Callable[[dict], None]) -> None:
        self._write_event = write_event
        self._channel = FrontendChannel()
        self._ui = EventStreamUI(self._channel)
        self._pump_started = threading.Lock()
        self._pumping = False

    def take(self, message: dict) -> bool:
        """True when ``message`` was for this computer: the command itself, or the answer to a dialog it asked."""
        if message.get("type") == "command" and str(message.get("name", "")).lstrip("/").casefold() in NAMES:
            args = message.get("args")
            self._start(str(message["name"]).lstrip("/").casefold(), args if isinstance(args, list) else [])
            return True
        request_id = message.get("request_id")
        if isinstance(request_id, str) and request_id in self._channel.open_request_ids():
            reply = InputResponse.from_dict(message)
            self._channel.respond(reply.request_id, value=reply.value, approved=reply.approved, remember=reply.remember)
            return True
        return False

    def ask(self, work: Callable[[EventStreamUI], object]):
        """Run ``work(ui)`` on this thread: what it asks becomes a dialog in the frontend and the answers come back here."""
        self._pump()
        return work(self._ui)

    def relaunch_here(self) -> None:
        """Ask the frontend to start again on this computer (the same request ``/machines`` ends with)."""
        outcome = local_outcome()
        self._write_event(CommandResultEvent("machines", outcome.ok, outcome.message, outcome.effects).to_dict())

    def _start(self, name: str, args: list[str]) -> None:
        self._pump()
        threading.Thread(target=self._run, args=(name, [str(item) for item in args]), daemon=True, name=f"local-{name}").start()

    def _pump(self) -> None:
        with self._pump_started:
            if self._pumping:
                return
            self._pumping = True
        threading.Thread(target=self._forward_events, daemon=True, name="local-events").start()

    def _forward_events(self) -> None:
        # One reader keeps the dialogs and the result in the order they were produced.
        while True:
            event = self._channel.get(timeout=_PUMP_SECONDS)
            if event is not None:
                self._write_event(event.to_dict())

    def _run(self, name: str, args: list[str]) -> None:
        try:
            outcome = machines_command(self._ui, args) if name == "machines" else remote_command(self._ui, args)
        except (RemoteError, OSError, JsonStorageError) as error:
            outcome = Outcome(False, str(error), {})
        if outcome is None:
            outcome = Outcome(True, "Cancelled.", {"cancelled": True})
        self._channel.emit(CommandResultEvent(name, outcome.ok, outcome.message, outcome.effects))
