# -*- coding: utf-8 -*-
"""Which session a frontend is in on a server, learned from the events that pass through the proxy.

A session lives on one machine. The proxy notes each one that had a turn in the saved machines file, so the
dialog can list sessions from every machine and open one where it lives.
"""
from __future__ import annotations

import json
from collections.abc import Callable

from kernel.session.durable_json import JsonStorageError
from kernel.ssh import machines


class SessionTracker:
    def __init__(
        self,
        machine: str | None,
        folder: str,
        *,
        record: Callable[..., None] = machines.record_session,
        report: Callable[[str], None] = lambda text: None,
    ) -> None:
        self._machine = machine
        self._folder = folder
        self._record = record
        self._report = report
        self.session = ""
        self.title = ""

    def see(self, kind: str, raw: bytes) -> None:
        """Take in one event line the daemon sent."""
        if kind == "turn_end":
            return self._remember()
        if kind not in ("hello", "session", "session_title"):
            return
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return
        session = event.get("session")
        if kind == "session_title" and self.session and session != self.session:
            return  # a title that landed after the person moved to another session
        if isinstance(session, str) and session:
            if session != self.session:
                self.title = ""
            self.session = session
        if kind != "hello" and isinstance(event.get("title"), str):
            self.title = event["title"]
        if kind == "session_title":
            self._remember()

    def _remember(self) -> None:
        if not self._machine or not self.session:
            return
        try:
            self._record(self._machine, self._folder, self.session, self.title)
        except (OSError, JsonStorageError) as error:
            self._report(f"could not note the session in the machines file: {error}")
