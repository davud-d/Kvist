# -*- coding: utf-8 -*-
"""Remote core smoke test: the real daemon, the real proxy, a stand-in ssh, a fake provider.

A frontend is killed in the middle of a turn (the computer lost power); a new one connects, takes
over from the silent one, sees the turn finish and carries on. Standard library only; needs a POSIX
system with `sh`. Exit code 0 means every check passed.

    python scripts/e2e_remote_smoke.py
"""
from __future__ import annotations

import json
import os
import queue
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_tools_smoke import ROOT, FakeProvider, Kvist, SCENARIOS, configure  # noqa: E402

TIMEOUT = 30


class Frontend(Kvist):
    """A frontend as the daemon sees it: the proxy started with the arguments Ink gives its core."""

    def __init__(self, env_home: Path, tools: Path, workspace: Path, stand_in: Path) -> None:
        self._argv = [
            sys.executable, "-m", "kernel", "--remote", "me@box", "--remote-workspace", str(workspace),
            "--workspace", str(stand_in), "--frontend", "stdio-multi", "--wake", "--role", "coder",
        ]
        env = dict(os.environ, HOME=str(env_home), USERPROFILE=str(env_home), PYTHONUTF8="1",
                   PYTHONUNBUFFERED="1", KVIST_SSH=str(tools / "ssh"), PATH=f"{tools}:{os.environ['PATH']}")
        self.process = subprocess.Popen(
            self._argv, cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.events = queue.Queue()
        self.stderr_lines = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()


def build_tools(directory: Path) -> None:
    ssh = directory / "ssh"
    ssh.write_text('#!/bin/sh\nfor last; do :; done\nexec sh -c "$last"\n')
    core = directory / "kvist-core"
    core.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "kernel" / "__main__.py"}" "$@"\n')
    for script in (ssh, core):
        script.chmod(script.stat().st_mode | stat.S_IXUSR)


def operation(name: str, workspace: Path, home: Path) -> dict:
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1", AGENT_SANDBOX_NET="allow",
               AGENT_WRITE_APPROVAL="0", AGENT_API_INTERVAL="0", AGENT_LLM_PROVIDER="custom:fake")
    done = subprocess.run(
        [sys.executable, "-m", "kernel", "--remote-op", name, "--workspace", str(workspace)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout.splitlines()[-1])


def become_driver(frontend: Frontend) -> None:
    control = frontend.collect_until("control", TIMEOUT)[-1]
    if control["role"] != "driver":
        frontend.process.stdin.write(json.dumps({"type": "takeover"}) + "\n")
        frontend.process.stdin.flush()
        while True:
            if frontend.collect_until("control", TIMEOUT)[-1]["role"] == "driver":
                return


def main() -> int:
    provider = FakeProvider()
    with tempfile.TemporaryDirectory(prefix="kvist-remote-") as scratch:
        base = Path(scratch)
        home, workspace, tools, stand_in = base / "home", base / "workspace", base / "tools", base / "stand-in"
        for folder in (home, workspace, tools, stand_in):
            folder.mkdir()
        configure(home, provider.url)
        build_tools(tools)
        daemon_env = dict(os.environ, HOME=str(home), AGENT_SANDBOX_NET="allow", AGENT_WRITE_APPROVAL="0",
                          AGENT_API_INTERVAL="0", PYTHONUTF8="1")
        os.environ.update(daemon_env)
        started = operation("start", workspace, home)
        if not started["ok"]:
            for log in home.glob(".kvist/remote/*/daemon.log"):
                print(log.read_text())
        assert started["ok"], started
        print("ok  daemon started")
        first = second = None
        try:
            first = Frontend(home, tools, workspace, stand_in)
            first.collect_until("hello", TIMEOUT)
            become_driver(first)
            first.send_goal(SCENARIOS[0][0])
            first.collect_until("turn_end", TIMEOUT)
            assert (workspace / "hello.txt").read_bytes() == SCENARIOS[0][2]
            print("ok  turn through ssh")

            first.send_goal(SCENARIOS[1][0])
            # The goal has to have reached the server before the power goes.
            first.collect_until("route", TIMEOUT)
            first.process.send_signal(signal.SIGKILL)
            first.process.wait(timeout=5)
            print("ok  frontend killed in the middle of a turn")

            second = Frontend(home, tools, workspace, stand_in)
            events = second.collect_until("hello", TIMEOUT)
            become_driver(second)
            more = second.collect_until("turn_end", TIMEOUT)
            types = [event["type"] for event in events + more]
            assert (workspace / "hello.txt").read_bytes() == SCENARIOS[1][2], "the turn did not finish without a frontend"
            assert types.count("turn_end") >= 1
            print("ok  the new frontend saw the turn finish")

            time.sleep(1.0)
            while not second.events.empty():  # what was replayed is not this turn's answer
                second.events.get_nowait()
            second.send_goal(SCENARIOS[2][0])
            second.collect_until("turn_end", TIMEOUT)
            content = (workspace / "hello.txt").read_bytes()
            assert content == SCENARIOS[2][2], f"hello.txt is {content!r}"
            print("ok  the new frontend drives")
        except (AssertionError, TimeoutError) as error:
            print(f"FAIL: {error}")
            for frontend in (first, second):
                if frontend is not None:
                    print("--- stderr:\n" + "\n".join(frontend.stderr_lines[-20:]))
            return 1
        finally:
            for frontend in (first, second):
                if frontend is not None and frontend.process.poll() is None:
                    frontend.stop()
            print(operation("stop", workspace, home))
            provider.server.shutdown()
    print("remote smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
