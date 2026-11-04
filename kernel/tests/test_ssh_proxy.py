# -*- coding: utf-8 -*-
"""The proxy against a real daemon, through a stand-in ``ssh`` that runs the remote command on this machine.

The shim is the real ``--remote-attach`` of this checkout; only the network between the two is faked.
"""
from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from kernel.ssh import wire
from kernel.ssh.command import Target
from kernel.ssh.proxy import Proxy
from kernel.tests.ssh_support import FakeCoreDaemon

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the remote core is POSIX-only")

CORE_ENTRY = Path(__file__).resolve().parents[1] / "__main__.py"


class Frontend:
    """What a frontend is to the proxy: a pipe it writes goals to and a stream of events it reads."""

    def __init__(self) -> None:
        read_end, self._write_end = os.pipe()
        self.stdin = os.fdopen(read_end, "rb")
        self._lines: list[dict] = []
        self._changed = threading.Condition()
        self._partial = b""
        self.stderr = io.StringIO()

    # the proxy's stdout
    def write(self, data: bytes) -> None:
        with self._changed:
            self._partial += data
            *complete, self._partial = self._partial.split(b"\n")
            self._lines.extend(json.loads(item) for item in complete if item)
            self._changed.notify_all()

    def flush(self) -> None:
        pass

    def say(self, text: str) -> None:
        os.write(self._write_end, text.encode() + b"\n")

    def leave(self) -> None:
        os.close(self._write_end)

    def wait_for(self, predicate, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                for line in self._lines:
                    if predicate(line):
                        return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"got {[line.get('type') for line in self._lines]}")
                self._changed.wait(remaining)

    def types(self) -> list[str]:
        with self._changed:
            return [line["type"] for line in self._lines]

    def events(self, kind: str) -> list[dict]:
        with self._changed:
            return [line for line in self._lines if line["type"] == kind]


@pytest.fixture
def server_tools(tmp_path, monkeypatch):
    """``ssh`` runs its last argument locally; ``kvist-core`` is this checkout."""
    tools = tmp_path / "tools"
    tools.mkdir()
    ssh = tools / "ssh"
    ssh.write_text(
        "#!/bin/sh\n"
        'if [ -f "$STUB_SSH_FAIL" ]; then\n'
        '  echo x >> "$STUB_SSH_FAIL.count"\n'
        '  cat "$STUB_SSH_FAIL" >&2\n'
        "  exit 255\n"
        "fi\n"
        'for last; do :; done\n'
        'exec sh -c "$last"\n'
    )
    core = tools / "kvist-core"
    core.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{CORE_ENTRY}" "$@"\n')
    for script in (ssh, core):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("KVIST_SSH", str(ssh))
    monkeypatch.setenv("PATH", f"{tools}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_SSH_FAIL", str(tmp_path / "ssh-fails"))
    return tools


def start_proxy(workspace: str, **options) -> tuple[Proxy, Frontend, threading.Thread, list[int]]:
    frontend = Frontend()
    proxy = Proxy(
        Target("me@box", None, workspace),
        stdin=frontend.stdin,
        stdout=frontend,
        stderr=frontend.stderr,
        language="en",
        **{"give_up_seconds": 20.0, "max_delay": 0.2, "prepare_machine": lambda *a, **k: True, **options},
    )
    exit_code: list[int] = []
    thread = threading.Thread(target=lambda: exit_code.append(proxy.run()), daemon=True)
    thread.start()
    return proxy, frontend, thread, exit_code


@pytest.fixture
def core(tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    yield daemon
    daemon.close()


def test_a_frontend_talks_to_the_daemon_through_ssh_and_leaves_it_running(server_tools, core):
    _, frontend, thread, exit_code = start_proxy(core.workspace)

    frontend.wait_for(lambda line: line["type"] == "control")
    frontend.say("hello server")
    final = frontend.wait_for(lambda line: line["type"] == "final")
    frontend.leave()
    thread.join(timeout=10)

    assert frontend.types()[0] == "hello"
    assert final["message"] == "done hello server"
    assert exit_code == [0]
    assert core.thread.is_alive()
    # What the proxy and the daemon say to each other stays between them; only the link's state reaches the frontend.
    assert not {"remote_hello", "remote_attached", "remote_offset", "remote_ping"} & set(frontend.types())


def test_after_a_broken_connection_it_continues_where_it_was_without_repeating(server_tools, core):
    proxy, frontend, thread, _ = start_proxy(core.workspace)
    frontend.wait_for(lambda line: line["type"] == "control")
    frontend.say("one")
    frontend.wait_for(lambda line: line["type"] == "turn_end")
    time.sleep(0.5)

    proxy._proc.kill()
    states = lambda: [event["state"] for event in frontend.events("remote_state")]  # noqa: E731
    frontend.wait_for(lambda line: states()[-2:] == ["reconnecting", "connected"])
    frontend.say("two")
    frontend.wait_for(lambda line: line["type"] == "final" and line["message"] == "done two")
    frontend.leave()
    thread.join(timeout=10)

    assert [event["message"] for event in frontend.events("final")] == ["done one", "done two"]
    # The proxy's own hello opened the frontend; the daemon's followed once, and a reconnect sent none.
    assert frontend.types().count("hello") == 2
    assert core.agent.goals == ["one", "two"]


def test_a_second_frontend_is_told_it_only_watches(server_tools, core):
    _, driver, driver_thread, _ = start_proxy(core.workspace)
    driver.wait_for(lambda line: line["type"] == "control")
    _, watcher, watcher_thread, _ = start_proxy(core.workspace)

    control = watcher.wait_for(lambda line: line["type"] == "control")
    watcher.say("my own task")
    watcher.wait_for(lambda line: line["type"] == "control" and watcher.types().count("control") >= 2)
    driver.leave()
    watcher.leave()
    driver_thread.join(timeout=10)
    watcher_thread.join(timeout=10)

    assert control["role"] == "observer"
    assert core.agent.goals == []


def test_with_nothing_running_on_the_server_it_gives_up_and_says_why_instead_of_knocking(server_tools, tmp_path):
    workspace = tmp_path / "nobody-home"
    workspace.mkdir()
    _, frontend, thread, exit_code = start_proxy(str(workspace))

    thread.join(timeout=20)

    assert exit_code == [wire.EXIT_GAVE_UP]
    assert "not running on me@box" in frontend.stderr.getvalue()


def test_a_server_that_is_down_is_retried_for_a_while_and_then_given_up_on(server_tools, tmp_path):
    (tmp_path / "ssh-fails").write_text("ssh: connect to host box port 22: Connection refused\n")
    _, frontend, thread, exit_code = start_proxy(str(tmp_path), give_up_seconds=0.6, max_delay=0.1)

    thread.join(timeout=20)

    attempts = (tmp_path / "ssh-fails.count").read_text().count("x")
    assert exit_code == [wire.EXIT_GAVE_UP]
    assert attempts >= 3
    assert "Gave up reconnecting" in frontend.stderr.getvalue()


def test_a_refused_login_is_final_at_once(server_tools, tmp_path):
    (tmp_path / "ssh-fails").write_text("me@box: Permission denied (publickey).\n")
    _, frontend, thread, exit_code = start_proxy(str(tmp_path))

    thread.join(timeout=20)

    assert exit_code == [wire.EXIT_GAVE_UP]
    assert (tmp_path / "ssh-fails.count").read_text().count("x") == 1
    assert "key or ssh-agent" in frontend.stderr.getvalue()


def test_a_message_typed_while_disconnected_is_refused_loudly(server_tools, tmp_path):
    (tmp_path / "ssh-fails").write_text("ssh: connect to host box port 22: Connection refused\n")
    _, frontend, thread, _ = start_proxy(str(tmp_path), give_up_seconds=5, max_delay=0.3)
    time.sleep(0.3)

    frontend.say("anyone there?")
    time.sleep(0.3)
    frontend.leave()
    thread.join(timeout=10)

    assert any("your message was not sent" in event["text"] for event in frontend.events("notice"))
    # The frontend is waiting for the turn to end; it is told it did, or it would wait for ever.
    assert "turn_end" in frontend.types()


def test_a_one_shot_operation_runs_on_the_server_with_the_servers_folder(server_tools, core, capfd):
    from kernel.ssh.proxy import run_forwarded

    code = run_forwarded(
        Target("me@box", None, core.workspace),
        ["--remote", "me@box", "--remote-workspace", core.workspace, "--workspace", "/nowhere", "--remote-op", "status"],
        language="en",
    )

    answer = json.loads(capfd.readouterr().out.splitlines()[-1])
    assert code == 0
    assert answer["ok"] is True and answer["running"] is True
    assert answer["daemon"]["protocol_version"] == 1


def test_probe_reports_what_the_server_has(server_tools, core, capfd):
    result = subprocess.run(
        [sys.executable, str(CORE_ENTRY), "--remote-op", "probe", "--workspace", core.workspace],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=60,
    )

    answer = json.loads(result.stdout.splitlines()[-1])
    assert answer["ok"] is True
    assert answer["workspace_exists"] is True
    assert answer["daemon"]["type"] == "remote_status"
    assert answer["protocol_version"] == 1
    assert "provider_ready" in answer and "service" in answer


class SessionCommands:
    """The one command a test core knows: ``session``, which only notes what it was asked."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    def handlers(self) -> dict:
        from kernel.protocol.event_stdio import CommandResult

        def session(context) -> CommandResult:
            self.asked.append(list(context.args))
            return CommandResult(True, "switched", {})

        return {"session": session}


def test_the_session_the_person_chose_is_opened_once_the_log_has_been_read(server_tools, tmp_path):
    commands = SessionCommands()
    daemon = FakeCoreDaemon(tmp_path, commands=commands.handlers())
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, open_session="chat-from-before")
        frontend.wait_for(lambda line: line["type"] == "command_result")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert commands.asked == [["switch", "chat-from-before"]]


def test_nothing_is_asked_when_the_daemon_is_already_in_that_session(server_tools, tmp_path):
    commands = SessionCommands()
    daemon = FakeCoreDaemon(tmp_path, commands=commands.handlers())
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, open_session="default")
        frontend.wait_for(lambda line: line["type"] == "control")
        time.sleep(0.5)
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert commands.asked == []


def test_an_observer_cannot_switch_the_session_and_is_told_so(server_tools, tmp_path):
    commands = SessionCommands()
    daemon = FakeCoreDaemon(tmp_path, commands=commands.handlers())
    try:
        _, driver, driver_thread, _ = start_proxy(daemon.workspace)
        driver.wait_for(lambda line: line["type"] == "control")
        _, watcher, watcher_thread, _ = start_proxy(daemon.workspace, open_session="chat-from-before")
        told = watcher.wait_for(lambda line: line["type"] == "error")
        driver.leave()
        watcher.leave()
        driver_thread.join(timeout=10)
        watcher_thread.join(timeout=10)
    finally:
        daemon.close()

    assert "chat-from-before" in told["message"]
    assert commands.asked == []


def test_a_session_that_had_a_turn_is_written_down_under_its_machine(server_tools, tmp_path):
    book: list[tuple] = []
    daemon = FakeCoreDaemon(tmp_path)
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, machine="work", record=lambda *record: book.append(record))
        frontend.wait_for(lambda line: line["type"] == "control")
        frontend.say("do it")
        frontend.wait_for(lambda line: line["type"] == "turn_end")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert book and book[-1][:3] == ("work", daemon.workspace, "default")


def test_machines_is_answered_by_the_proxy_with_dialogs_and_never_reaches_the_server(server_tools, tmp_path):
    commands = SessionCommands()
    daemon = FakeCoreDaemon(tmp_path, commands=commands.handlers())
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace)
        frontend.wait_for(lambda line: line["type"] == "control")

        frontend.say(json.dumps({"type": "command", "name": "machines", "args": []}))
        request = frontend.wait_for(lambda line: line["type"] == "input_request")
        frontend.say(json.dumps({"request_id": request["request_id"], "value": "@local"}))
        result = frontend.wait_for(lambda line: line["type"] == "command_result")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert request["title"] == "Machines"
    assert result["effects"]["relaunch"]["switch"] == "@local"
    # The server's core would have answered "unknown command"; it was never asked.
    assert [line["name"] for line in frontend.events("command_result")] == ["machines"]


def test_a_command_for_the_server_still_goes_to_the_server(server_tools, tmp_path):
    commands = NewCommand()
    daemon = FakeCoreDaemon(tmp_path, commands=commands.handlers())
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace)
        # The proxy opens the new session first, and a core takes one command at a time.
        frontend.wait_for(lambda line: line["type"] == "command_result" and line["name"] == "new")

        frontend.say(json.dumps({"type": "command", "name": "session", "args": ["switch", "x"]}))
        frontend.wait_for(lambda line: line["type"] == "command_result" and line["name"] == "session")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert commands.asked == ["new", "session switch x"]


def test_the_frontend_is_told_where_the_core_works_as_machine_then_folder(server_tools, tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    try:
        _, saved, saved_thread, _ = start_proxy(daemon.workspace, machine="work")
        hello_saved = saved.wait_for(lambda line: line["type"] == "hello" and line.get("session") == "default")
        saved.leave()
        saved_thread.join(timeout=10)
        _, by_hand, by_hand_thread, _ = start_proxy(daemon.workspace)
        hello_by_hand = by_hand.wait_for(lambda line: line["type"] == "hello" and line.get("session") == "default")
        by_hand.leave()
        by_hand_thread.join(timeout=10)
    finally:
        daemon.close()

    assert hello_saved["location"] == f"work:{daemon.workspace}"
    assert hello_by_hand["location"] == f"me@box:{daemon.workspace}"
    # Nothing else of the hello is changed.
    assert hello_saved["protocol_version"] == 1 and hello_saved["session"] == "default"


# --- the interface opens first and asks its questions itself ---------------------------------------------


def asking_prepare(*answers_wanted):
    """A check of the machine that puts one question to the person, as the real one does, and reports what was answered."""
    seen: list = []

    def prepare(target, io, language):
        seen.append(io.menu("Kvist is not running here.", [("install-service", "Install it as a service"), ("cancel", "Cancel")]))
        return seen[-1] == "install-service"

    prepare.seen = seen
    return prepare


def test_the_interface_gets_its_hello_at_once_and_the_machine_is_prepared_in_dialogs_it_draws_itself(server_tools, tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    check = asking_prepare()
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, machine="work", prepare_machine=check)
        first = frontend.wait_for(lambda line: True)
        request = frontend.wait_for(lambda line: line["type"] == "input_request")
        frontend.say(json.dumps({"request_id": request["request_id"], "value": "install-service"}))
        frontend.wait_for(lambda line: line["type"] == "control")
        frontend.say("go")
        frontend.wait_for(lambda line: line["type"] == "final")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert first["type"] == "hello" and first["location"] == f"work:{daemon.workspace}"
    assert request["kind"] == "choice" and request["title"] == "Kvist is not running here."
    assert check.seen == ["install-service"]
    # The daemon's own hello followed the proxy's, and the person's goal ran once the machine was ready.
    assert frontend.types().count("hello") == 2
    # "Connecting…" is a notification, not something said in the conversation.
    assert frontend.events("notice")[0]["text"].startswith("Connecting to")
    assert frontend.events("message") == []


def test_when_the_machine_cannot_be_made_ready_the_person_chooses_and_working_here_asks_the_interface_to_start_again(server_tools, tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    try:
        _, frontend, thread, exit_code = start_proxy(daemon.workspace, prepare_machine=lambda target, io, language: False)
        menu = frontend.wait_for(lambda line: line["type"] == "input_request")
        frontend.say(json.dumps({"request_id": menu["request_id"], "value": "local"}))
        result = frontend.wait_for(lambda line: line["type"] == "command_result")
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert "Could not open" in menu["title"]
    assert [option["value"] for option in menu["options"]] == ["retry", "local", "close"]
    assert result["effects"]["relaunch"]["switch"] == "@local"
    assert exit_code == [0]


def test_trying_again_asks_the_machine_again_and_closing_ends_with_the_give_up_code(server_tools, tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    attempts = []

    def prepare(target, io, language):
        attempts.append(1)
        return False

    try:
        _, frontend, thread, exit_code = start_proxy(daemon.workspace, prepare_machine=prepare)
        for number, answer in enumerate(("retry", "close")):
            menu = frontend.wait_for(lambda line, n=number: line["type"] == "input_request" and frontend.events("input_request").index(line) == n)
            frontend.say(json.dumps({"request_id": menu["request_id"], "value": answer}))
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert len(attempts) == 2
    assert exit_code == [wire.EXIT_GAVE_UP]


def test_a_machine_that_is_ready_has_its_folder_remembered(server_tools, tmp_path):
    from kernel.ssh import machines
    from kernel.ssh.machines import Machine

    machines.save_machine(Machine("work", "me@box"))
    daemon = FakeCoreDaemon(tmp_path)
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, machine="work")
        frontend.wait_for(lambda line: line["type"] == "control")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert machines.get_machine("work").folders[0] == daemon.workspace


def test_what_the_frontend_sends_on_its_own_while_the_machine_is_being_made_ready_waits_and_is_not_refused(server_tools, tmp_path):
    daemon = FakeCoreDaemon(tmp_path)
    release = threading.Event()

    def slow_prepare(target, io, language):
        release.wait(20)
        return True

    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, prepare_machine=slow_prepare)
        frontend.wait_for(lambda line: line["type"] == "hello")
        frontend.say("typed while the machine is made ready")
        time.sleep(0.5)
        assert frontend.events("notice") == [{"type": "notice", "text": frontend.events("notice")[0]["text"], "level": "info"}]
        release.set()
        final = frontend.wait_for(lambda line: line["type"] == "final")
        frontend.leave()
        thread.join(timeout=10)
    finally:
        release.set()
        daemon.close()

    assert final["message"] == "done typed while the machine is made ready"
    assert not any(event["level"] == "warn" for event in frontend.events("notice"))


def test_a_command_refused_after_a_break_is_answered_so_the_frontend_does_not_wait_for_ever(server_tools, tmp_path):
    (tmp_path / "ssh-fails").write_text("ssh: connect to host box port 22: Connection refused\n")
    _, frontend, thread, _ = start_proxy(str(tmp_path), give_up_seconds=5, max_delay=0.3)
    frontend.wait_for(lambda line: line["type"] == "hello")
    time.sleep(1.0)

    frontend.say(json.dumps({"type": "command", "name": "new", "args": []}))
    frontend.say(json.dumps({"type": "cancel"}))
    result = frontend.wait_for(lambda line: line["type"] == "command_result")
    frontend.leave()
    thread.join(timeout=10)

    # The link is down and the frontend was told; what it sends meanwhile is answered, not left to wait.
    assert result["name"] == "new" and result["ok"] is False


# --- every launch begins with a new session -------------------------------------------------------------------


class NewCommand:
    """A test core that knows ``new`` and ``session`` and notes what it was asked."""

    def __init__(self, *, ok: bool = True) -> None:
        self.asked: list[str] = []
        self._ok = ok

    def handlers(self) -> dict:
        from kernel.protocol.event_stdio import CommandResult

        def new(context) -> CommandResult:
            self.asked.append("new")
            return CommandResult(self._ok, "A new session" if self._ok else "busy", {"changed": self._ok})

        def session(context) -> CommandResult:
            self.asked.append("session " + " ".join(context.args))
            return CommandResult(True, "switched", {})

        return {"new": new, "session": session}


def run_launch(tmp_path, command: NewCommand, **options) -> Frontend:
    daemon = FakeCoreDaemon(tmp_path, commands=command.handlers())
    try:
        _, frontend, thread, _ = start_proxy(daemon.workspace, **options)
        frontend.wait_for(lambda line: line["type"] == "control")
        time.sleep(1.0)
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()
    return frontend


def test_a_launch_opens_a_new_session_instead_of_sitting_in_the_one_the_daemon_was_left_in(server_tools, tmp_path):
    command = NewCommand()

    frontend = run_launch(tmp_path, command)

    assert command.asked == ["new"]
    assert frontend.events("command_result")[0]["effects"] == {"changed": True}


def test_a_reconnect_does_not_start_another_session_in_the_middle_of_the_work(server_tools, tmp_path):
    command = NewCommand()
    daemon = FakeCoreDaemon(tmp_path, commands=command.handlers())
    try:
        proxy, frontend, thread, _ = start_proxy(daemon.workspace)
        frontend.wait_for(lambda line: line["type"] == "command_result")
        proxy._proc.kill()
        states = lambda: [event["state"] for event in frontend.events("remote_state")]  # noqa: E731
        frontend.wait_for(lambda line: states()[-2:] == ["reconnecting", "connected"])
        time.sleep(1.0)
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert command.asked == ["new"]


def test_a_person_who_asked_to_go_on_with_a_session_is_not_given_a_new_one(server_tools, tmp_path):
    chosen, resumed = NewCommand(), NewCommand()

    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
    run_launch(tmp_path / "a", chosen, open_session="chat-from-before")
    run_launch(tmp_path / "b", resumed, resume=True)

    assert chosen.asked == ["session switch chat-from-before"]
    assert resumed.asked == []


def test_a_new_session_that_the_core_refused_because_it_is_busy_shows_nothing(server_tools, tmp_path):
    command = NewCommand(ok=False)

    frontend = run_launch(tmp_path, command)

    assert command.asked == ["new"]
    assert frontend.events("command_result") == []


def test_a_watching_interface_does_not_open_a_session_over_the_one_that_is_driving(server_tools, tmp_path):
    command = NewCommand()
    daemon = FakeCoreDaemon(tmp_path, commands=command.handlers())
    try:
        _, driver, driver_thread, _ = start_proxy(daemon.workspace)
        driver.wait_for(lambda line: line["type"] == "command_result")
        _, watcher, watcher_thread, _ = start_proxy(daemon.workspace)
        watcher.wait_for(lambda line: line["type"] == "control")
        time.sleep(1.0)
        driver.leave()
        watcher.leave()
        driver_thread.join(timeout=10)
        watcher_thread.join(timeout=10)
    finally:
        daemon.close()

    assert command.asked == ["new"]


def test_work_the_agent_did_while_nobody_was_attached_is_what_the_person_comes_back_to_not_a_new_session(server_tools, tmp_path):
    command = NewCommand()
    daemon = FakeCoreDaemon(tmp_path, commands=command.handlers())
    try:
        daemon.daemon.inbox.put("work while the person is away")
        deadline = time.monotonic() + 5
        while b'"turn_end"' not in daemon.paths.log.read_bytes() and time.monotonic() < deadline:
            time.sleep(0.02)
        _, frontend, thread, _ = start_proxy(daemon.workspace)
        frontend.wait_for(lambda line: line["type"] == "control")
        final = frontend.wait_for(lambda line: line["type"] == "final")
        time.sleep(1.0)
        frontend.leave()
        thread.join(timeout=10)
    finally:
        daemon.close()

    assert final["message"] == "done work while the person is away"
    assert command.asked == []
