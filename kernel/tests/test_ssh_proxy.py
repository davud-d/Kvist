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
        **{"give_up_seconds": 20.0, "max_delay": 0.2, **options},
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
    assert not any(kind.startswith("remote_") for kind in frontend.types())


def test_after_a_broken_connection_it_continues_where_it_was_without_repeating(server_tools, core):
    proxy, frontend, thread, _ = start_proxy(core.workspace)
    frontend.wait_for(lambda line: line["type"] == "control")
    frontend.say("one")
    frontend.wait_for(lambda line: line["type"] == "turn_end")
    time.sleep(0.5)

    proxy._proc.kill()
    frontend.wait_for(lambda line: line["type"] == "remote_state" and line["state"] == "reconnecting")
    frontend.wait_for(lambda line: line["type"] == "remote_state" and line["state"] == "connected")
    frontend.say("two")
    frontend.wait_for(lambda line: line["type"] == "final" and line["message"] == "done two")
    frontend.leave()
    thread.join(timeout=10)

    assert [event["message"] for event in frontend.events("final")] == ["done one", "done two"]
    assert frontend.types().count("hello") == 1
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

    assert "your message was not sent" in frontend.stderr.getvalue()


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
