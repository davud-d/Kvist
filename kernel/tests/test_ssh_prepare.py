# -*- coding: utf-8 -*-
"""What the launcher checks and asks before it opens a frontend on a server."""
from __future__ import annotations

from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh.command import Target
from kernel.ssh.prepare import prepare

TARGET = Target("me@box", None, "/srv/app")
HEALTHY = {
    "ok": True,
    "platform": "linux",
    "protocol_version": PROTOCOL_VERSION,
    "workspace_exists": True,
    "provider_ready": True,
    "daemon": None,
}


class Server:
    """A server that answers each operation from a table and remembers what it was asked."""

    def __init__(self, **answers) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def __call__(self, target, operation):
        self.asked.append(operation)
        answer = self.answers.get(operation)
        return (0 if answer else 255), answer, "" if answer else self.answers.get("stderr", "")


def run(server: Server, *, choice: str = "", interactive: bool = True) -> bool:
    return prepare(TARGET, call=server, ask=lambda prompt: choice, interactive=interactive, language="en")


def test_a_running_daemon_needs_nothing_more(capsys):
    server = Server(probe={**HEALTHY, "daemon": {"pid": 1}})

    assert run(server) is True
    assert server.asked == ["probe"]


def test_without_a_daemon_the_default_answer_installs_a_service():
    server = Server(probe=HEALTHY, **{"install-service": {"ok": True, "notes": []}})

    assert run(server) is True
    assert server.asked == ["probe", "install-service"]


def test_the_person_may_start_it_once_or_walk_away():
    once = Server(probe=HEALTHY, start={"ok": True})
    assert run(once, choice="2") is True
    assert once.asked == ["probe", "start"]

    cancelled = Server(probe=HEALTHY)
    assert run(cancelled, choice="3") is False
    assert cancelled.asked == ["probe"]


def test_nothing_is_installed_without_anyone_to_ask(capsys):
    server = Server(probe=HEALTHY)

    assert run(server, interactive=False) is False
    assert server.asked == ["probe"]
    assert "install-service" in capsys.readouterr().err


def test_a_server_that_cannot_work_is_named_before_anything_opens(capsys):
    for field, value, words in (
        ("provider_ready", False, "no provider"),
        ("workspace_exists", False, "not a folder"),
        ("protocol_version", PROTOCOL_VERSION + 1, "protocol"),
        ("platform", "win32", "does not support"),
    ):
        server = Server(probe={**HEALTHY, field: value})
        assert run(server) is False
        assert words in capsys.readouterr().err
        assert server.asked == ["probe"]


def test_a_server_that_does_not_answer_says_what_ssh_said(capsys):
    server = Server(stderr="ssh: connect to host box port 22: Connection refused")

    assert run(server) is False
    assert "Connection refused" in capsys.readouterr().err


def test_a_refused_installation_is_reported_and_stops_the_launch(capsys):
    server = Server(probe=HEALTHY, **{"install-service": {"ok": False, "error": "systemctl failed"}})

    assert run(server) is False
    assert "systemctl failed" in capsys.readouterr().err
