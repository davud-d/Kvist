# -*- coding: utf-8 -*-
"""What is checked and asked before a frontend opens on a server, whether the question is put on a terminal or in the frontend's own dialog."""
from __future__ import annotations

from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh import wire
from kernel.ssh.command import Target
from kernel.ssh.paths import RemoteError
from kernel.ssh.prepare import DialogIO, prepare

TARGET = Target("me@box", None, "/srv/app")
HEALTHY = {
    "ok": True,
    "platform": "linux",
    "protocol_version": PROTOCOL_VERSION,
    "remote_revision": wire.REVISION,
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


class ScriptIO:
    """Says into a list and answers menus from a script (None: the person backs out)."""

    def __init__(self, *answers, interactive: bool = True) -> None:
        self.answers = list(answers)
        self.said: list[str] = []
        self.menus: list[tuple[str, list[str]]] = []
        self.interactive = interactive

    def say(self, text: str) -> None:
        self.said.append(text)

    note = say

    def menu(self, title, options):
        self.menus.append((title, [value for value, _ in options]))
        return self.answers.pop(0) if self.answers else options[0][0]


def run(server: Server, io: ScriptIO) -> bool:
    return prepare(TARGET, io=io, call=server, language="en")


def test_a_running_daemon_needs_nothing_more():
    server, io = Server(probe={**HEALTHY, "daemon": {"pid": 1, "revision": wire.REVISION}}), ScriptIO()

    assert run(server, io) is True
    assert server.asked == ["probe"] and io.menus == []


def test_without_a_daemon_the_default_answer_installs_a_service():
    server = Server(probe=HEALTHY, **{"install-service": {"ok": True, "notes": ["lingering is off"]}})
    io = ScriptIO()

    assert run(server, io) is True
    assert server.asked == ["probe", "install-service"]
    assert io.menus[0][1] == ["install-service", "start", "cancel"]
    assert io.said == ["lingering is off"]


def test_the_person_may_start_it_once_or_walk_away():
    once = Server(probe=HEALTHY, start={"ok": True})
    assert run(once, ScriptIO("start")) is True
    assert once.asked == ["probe", "start"]

    cancelled = Server(probe=HEALTHY)
    assert run(cancelled, ScriptIO("cancel")) is False
    assert run(cancelled, ScriptIO(None)) is False
    assert cancelled.asked == ["probe", "probe"]


def test_nothing_is_installed_without_anyone_to_ask():
    server, io = Server(probe=HEALTHY), ScriptIO(interactive=False)

    assert run(server, io) is False
    assert server.asked == ["probe"]
    assert "install-service" in io.said[0]


def test_a_server_that_cannot_work_is_named_before_anything_opens():
    for field, value, words in (
        ("provider_ready", False, "no provider"),
        ("workspace_exists", False, "not a folder"),
        ("protocol_version", PROTOCOL_VERSION + 1, "protocol"),
        ("platform", "win32", "does not support"),
    ):
        server, io = Server(probe={**HEALTHY, field: value}), ScriptIO("no")

        assert run(server, io) is False
        assert words in io.said[-1]
        assert server.asked == ["probe"]


def test_a_server_that_does_not_answer_says_what_ssh_said():
    server, io = Server(stderr="ssh: connect to host box port 22: Connection refused"), ScriptIO()

    assert run(server, io) is False
    assert "Connection refused" in io.said[-1]


def test_a_refused_installation_is_reported_and_stops_the_launch():
    server = Server(probe=HEALTHY, **{"install-service": {"ok": False, "error": "systemctl failed"}})
    io = ScriptIO()

    assert run(server, io) is False
    assert "systemctl failed" in io.said[-1]


def test_a_folder_that_is_not_there_can_be_made():
    server = Server(probe={**HEALTHY, "workspace_exists": False}, mkdir={"ok": True}, **{"install-service": {"ok": True, "notes": []}})

    assert run(server, ScriptIO("yes")) is True
    assert server.asked == ["probe", "mkdir", "install-service"]


def test_declining_to_make_the_folder_stops_the_launch():
    server, io = Server(probe={**HEALTHY, "workspace_exists": False}), ScriptIO("no")

    assert run(server, io) is False
    assert server.asked == ["probe"]
    assert "not a folder" in io.said[-1]


def test_what_the_server_says_is_wrong_is_shown_rather_than_guessed_from_ssh():
    server, io = Server(probe={"ok": False, "error": "The socket path is too long"}), ScriptIO()

    assert run(server, io) is False
    assert "too long" in io.said[-1]


# --- the two ways of asking ----------------------------------------------------------------------------


class FakeDialogs:
    """What the core hands a command: the frontend draws these."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, list[dict], str]] = []
        self.errors: list[str] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.asked.append((title, options, current_value))
        return self.answers.pop(0)

    def print_error(self, message):
        self.errors.append(message)


def test_in_a_frontends_own_dialog_the_question_is_a_choice_and_the_default_is_the_first():
    ui = FakeDialogs("start")
    server = Server(probe=HEALTHY, start={"ok": True})

    assert prepare(TARGET, io=DialogIO(ui), call=server, language="en") is True

    title, options, current = ui.asked[0]
    assert "not running" in title and current == "install-service"
    assert [option["value"] for option in options] == ["install-service", "start", "cancel"]


def test_in_a_frontends_own_dialog_a_problem_is_an_error_event():
    ui = FakeDialogs()

    prepare(TARGET, io=DialogIO(ui), call=Server(probe={**HEALTHY, "provider_ready": False}), language="en")

    assert "no provider" in ui.errors[0]


# --- a machine whose Kvist, or whose running agent, is older than this one -------------------------------------

OLD_DAEMON = {"pid": 1, "attached": 0}  # an older daemon says nothing about its revision


def test_a_kvist_without_the_current_revision_is_offered_the_copy_and_asked_again_afterwards():
    installed: list[str] = []

    class Updating(Server):
        def __call__(self, target, operation):
            if operation == "probe" and installed:
                self.asked.append(operation)
                return 0, {**HEALTHY, "daemon": {"pid": 1, "revision": wire.REVISION}}, ""
            return super().__call__(target, operation)

    without = {key: value for key, value in HEALTHY.items() if key != "remote_revision"}
    server = Updating(probe={**without, "daemon": None})
    io = ScriptIO("install")

    assert prepare(TARGET, io=io, call=server, language="en", install=lambda target: installed.append("x") or "0.1") is True
    assert installed == ["x"] and server.asked == ["probe", "probe"]
    assert "too old" in io.menus[0][0]


def test_an_old_kvist_that_is_not_updated_stops_the_launch_instead_of_connecting():
    without = {key: value for key, value in HEALTHY.items() if key != "remote_revision"}

    for io in (ScriptIO("cancel"), ScriptIO(interactive=False)):
        server = Server(probe={**without, "daemon": {"pid": 1, "revision": wire.REVISION}})

        assert prepare(TARGET, io=io, call=server, language="en", install=lambda target: "never") is False
        assert "too old" in io.said[-1]


def test_an_old_agent_still_running_is_restarted_when_the_person_agrees():
    server = Server(probe={**HEALTHY, "daemon": OLD_DAEMON}, stop={"ok": True}, start={"ok": True})
    io = ScriptIO("restart")

    assert run(server, io) is True
    assert server.asked == ["probe", "stop", "start"]
    assert "older Kvist" in io.menus[0][0]


def test_an_old_agent_that_others_are_connected_to_says_so_before_it_is_restarted():
    server = Server(probe={**HEALTHY, "daemon": {**OLD_DAEMON, "attached": 2}}, stop={"ok": True}, start={"ok": True})
    io = ScriptIO("restart")

    run(server, io)

    assert io.menus[0][0].startswith("2 other interface")


def test_an_old_agent_is_left_alone_when_the_person_says_no_or_nobody_can_be_asked_and_the_launch_stops():
    declined = Server(probe={**HEALTHY, "daemon": OLD_DAEMON})
    assert run(declined, ScriptIO("cancel")) is False
    assert declined.asked == ["probe"]

    silent, io = Server(probe={**HEALTHY, "daemon": OLD_DAEMON}), ScriptIO(interactive=False)
    assert run(silent, io) is False
    assert "older than this Kvist" in io.said[-1] and "--remote-op stop" in io.said[-1]


def test_an_agent_one_revision_behind_is_old_too_because_it_does_not_know_the_newest_exchange():
    server = Server(probe={**HEALTHY, "daemon": {"pid": 1, "attached": 0, "revision": wire.REVISION - 1}}, stop={"ok": True}, start={"ok": True})

    assert run(server, ScriptIO("restart")) is True
    assert server.asked == ["probe", "stop", "start"]


def test_what_the_probe_saw_of_the_daemons_is_dropped_once_one_was_started_or_restarted():
    started, seen = Server(probe={**HEALTHY, "lanes": [{"lane": ""}]}, start={"ok": True}), {}
    assert prepare(TARGET, io=ScriptIO("start"), call=started, language="en", probed=seen) is True
    assert seen["lanes"] == []

    restarted, seen = Server(probe={**HEALTHY, "daemon": OLD_DAEMON, "lanes": [OLD_DAEMON]}, stop={"ok": True}, start={"ok": True}), {}
    assert prepare(TARGET, io=ScriptIO("restart"), call=restarted, language="en", probed=seen) is True
    assert seen["lanes"] == []
