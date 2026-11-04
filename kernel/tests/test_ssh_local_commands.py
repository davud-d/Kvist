# -*- coding: utf-8 -*-
"""The proxy answering ``/machines`` itself, so a frontend on a server sees an ordinary command with ordinary dialogs."""
from __future__ import annotations

import threading
import time

from kernel.ssh import machines
from kernel.ssh.local_commands import LocalCommands
from kernel.ssh.machines import Machine


class Frontend:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._changed = threading.Condition()

    def write(self, event: dict) -> None:
        with self._changed:
            self.events.append(event)
            self._changed.notify_all()

    def wait_for(self, kind: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                found = next((event for event in self.events if event["type"] == kind), None)
                if found:
                    return found
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError([event["type"] for event in self.events])
                self._changed.wait(remaining)


def test_a_command_for_this_computer_is_taken_and_a_command_for_the_server_is_not():
    local = LocalCommands(Frontend().write)

    assert local.take({"type": "command", "name": "models", "args": []}) is False
    assert local.take({"type": "goal", "text": "hello"}) is False
    assert local.take({"request_id": "not-ours", "value": "x"}) is False
    assert local.take({"type": "command", "name": "/Machines", "args": []}) is True


def test_the_dialog_is_asked_as_an_input_request_and_the_answer_ends_in_a_command_result():
    machines.save_machine(Machine("work", "me@box", folder="/srv/work"))
    frontend = Frontend()
    local = LocalCommands(frontend.write)

    local.take({"type": "command", "name": "machines", "args": []})
    request = frontend.wait_for("input_request")
    assert request["kind"] == "choice" and request["title"] == "Machines"
    assert "work" in [option["value"] for option in request["options"]]
    assert local.take({"request_id": request["request_id"], "value": "@local"}) is True
    result = frontend.wait_for("command_result")

    assert result["name"] == "machines" and result["ok"] is True
    assert result["effects"]["relaunch"]["switch"] == "@local"
    types = [event["type"] for event in frontend.events]
    assert types.index("input_request") < types.index("command_result")


def test_closing_the_dialog_ends_the_command_as_cancelled():
    frontend = Frontend()
    local = LocalCommands(frontend.write)

    local.take({"type": "command", "name": "machines", "args": []})
    request = frontend.wait_for("input_request")
    local.take({"request_id": request["request_id"], "value": None})
    result = frontend.wait_for("command_result")

    assert result["effects"] == {"cancelled": True}


def test_a_bad_remote_command_ends_with_its_usage_and_no_request_to_start_again():
    frontend = Frontend()
    local = LocalCommands(frontend.write)

    local.take({"type": "command", "name": "remote", "args": ["me@box"]})
    result = frontend.wait_for("command_result")

    assert result["ok"] is False and "Usage" in result["message"] and result["effects"] == {}
