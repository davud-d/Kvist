# -*- coding: utf-8 -*-
"""Remote core, parallel sessions: two sessions work in one folder at the same time, each with its own daemon (a lane).

Real daemons and interfaces over a stand-in ssh, a fake provider that holds the first request until it is told to go on.
  1. interface A starts a turn, which the provider holds: the first lane is busy;
  2. interface B opens: it is asked in its own dialog whether to join A's session or start another, and starts another;
     B's turn runs to the end while A's is still held (parallel work);
  3. A's interface is killed and the held turn finishes with nobody attached;
  4. interface C opens: it is offered both sessions, joins the first and sees the finished work, in its session, with no new one on top.
Standard library only; needs a POSIX system with `sh`. Exit code 0 means every check passed.

    python scripts/e2e_remote_lanes.py
"""
from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_remote_smoke import TIMEOUT, Frontend, build_tools  # noqa: E402
from e2e_tools_smoke import ROOT, SCENARIOS, FakeProvider, configure  # noqa: E402


class HoldingProvider(FakeProvider):
    """The fake provider, except that the first request of a run can be held back until ``release`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.hold = threading.Event()
        self.holding = threading.Event()
        self.release = threading.Event()

    def completion(self, request: dict) -> dict:
        tools = {tool["function"]["name"] for tool in request.get("tools") or []}
        first_of_a_run = tools and tools != {"ping"} and not any(item.get("role") == "tool" for item in request.get("messages") or [])
        if self.hold.is_set() and first_of_a_run:
            self.hold.clear()
            self.holding.set()
            self.release.wait(TIMEOUT * 2)
        return super().completion(request)


def op(name: str, workspace: Path, lane: str = "") -> dict:
    command = [sys.executable, "-m", "kernel", "--remote-op", name, "--workspace", str(workspace)]
    command += ["--remote-lane", lane] if lane else []
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=60)
    return json.loads(done.stdout.splitlines()[-1])


def drive(frontend: Frontend, event_type: str, choose, seen: list[dict]) -> dict:
    """Answer the interface's dialogs with ``choose(title, values)`` until an event of ``event_type`` (a hello with a provider) arrives."""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        try:
            event = frontend.events.get(timeout=1)
        except queue.Empty:
            continue
        seen.append(event)
        if event.get("type") == "input_request":
            values = [option["value"] for option in event.get("options") or []]
            reply = {"request_id": event["request_id"], "value": choose(event["title"], values)}
            frontend.process.stdin.write(json.dumps(reply) + "\n")
            frontend.process.stdin.flush()
        if event.get("type") == event_type and (event_type != "hello" or event.get("provider")):
            return event
    raise TimeoutError(f"no '{event_type}' in {TIMEOUT}s; got {[event.get('type') for event in seen]}")


def as_driver(frontend: Frontend, seen: list[dict]) -> None:
    drive(frontend, "control", lambda *_: "", seen)
    if seen[-1].get("role") != "driver":
        raise AssertionError("not the driver")


def wait_for_file(path: Path, content: bytes) -> None:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if path.exists() and path.read_bytes() == content:
            return
        time.sleep(0.2)
    raise AssertionError(f"{path.name} is {path.read_bytes() if path.exists() else None!r}, expected {content!r}")


def main() -> int:
    provider = HoldingProvider()
    with tempfile.TemporaryDirectory(prefix="kvist-lanes-") as scratch:
        base = Path(scratch)
        computer, server, workspace, tools, stand_in = (base / name for name in ("computer", "server", "workspace", "tools", "stand-in"))
        for folder in (computer, server, workspace, tools, stand_in):
            folder.mkdir()
        configure(computer, provider.url)
        build_tools(tools, server)
        os.environ.update(HOME=str(server), AGENT_SANDBOX_NET="allow", AGENT_WRITE_APPROVAL="0", AGENT_API_INTERVAL="0", PYTHONUTF8="1")
        os.environ.pop("AGENT_LLM_PROVIDER", None)
        os.environ["PATH"] = f"{tools}:{os.environ['PATH']}"
        assert op("start", workspace)["ok"]
        hello_txt = workspace / "hello.txt"
        a = b = c = None
        try:
            # 1. A starts a turn that the provider holds.
            a = Frontend(computer, tools, workspace, stand_in)
            seen_a: list[dict] = []
            drive(a, "hello", lambda *_: "", seen_a)
            as_driver(a, seen_a)
            provider.hold.set()
            a.send_goal("first task, held by the provider")
            assert provider.holding.wait(TIMEOUT), "the provider never got A's request"
            first = op("lanes", workspace)["lanes"]
            assert [(lane["lane"], lane["busy"]) for lane in first] == [("", True)], first
            print("ok  the first lane is working")

            # 2. B opens beside it and is asked what to do.
            b = Frontend(computer, tools, workspace, stand_in)
            seen_b: list[dict] = []
            asked: list[tuple[str, list[str]]] = []

            def start_another(title: str, values: list[str]) -> str:
                asked.append((title, values))
                return "@new"

            drive(b, "hello", start_another, seen_b)
            assert asked and "already" in asked[0][0] and asked[0][1] == ["@first", "@new"], asked
            print("ok  the interface asked, in its own dialog, whether to join the working session or start another")
            as_driver(b, seen_b)
            lanes = op("lanes", workspace)["lanes"]
            assert sorted(lane["lane"] for lane in lanes) == ["", "2"], lanes
            b.send_goal("second task, in the other session")
            b.collect_until("turn_end", TIMEOUT)
            wait_for_file(hello_txt, SCENARIOS[0][2])
            assert provider.holding.is_set() and not provider.release.is_set()
            print("ok  the second session ran to the end while the first one was still working")

            # 3. A's interface goes away, and the held turn finishes with nobody attached.
            a.process.send_signal(signal.SIGKILL)
            a.process.wait(timeout=5)
            provider.release.set()
            wait_for_file(hello_txt, SCENARIOS[1][2])
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline and not op("status", workspace)["daemon"]["unseen_work"]:
                time.sleep(0.2)
            first = op("status", workspace)["daemon"]
            assert first["unseen_work"] and not first["busy"], first
            print("ok  the first session finished while nobody was attached")

            # 4. C opens and is offered both; it joins the first and is shown what happened there.
            c = Frontend(computer, tools, workspace, stand_in)
            seen_c: list[dict] = []
            options: list[tuple[str, list[str]]] = []

            def join_first(title: str, values: list[str]) -> str:
                options.append((title, values))
                return "@first"

            drive(c, "hello", join_first, seen_c)
            assert options and options[0][1] == ["@first", "2", "@new"], options
            as_driver(c, seen_c)
            time.sleep(1.5)
            while not c.events.empty():
                seen_c.append(c.events.get_nowait())
            finals = [event for event in seen_c if event.get("type") == "final"]
            assert finals and finals[-1].get("message") == "Done.", [event.get("type") for event in seen_c]
            assert not [event for event in seen_c if event.get("type") == "command_result" and event.get("name") == "new"], "a new session hid the work"
            print("ok  the interface joined the finished session and was shown its result, without a new session on top")
        except (AssertionError, TimeoutError) as error:
            print(f"FAIL: {error}")
            for frontend in (a, b, c):
                if frontend is not None:
                    print("--- stderr:\n" + "\n".join(frontend.stderr_lines[-15:]))
            return 1
        finally:
            provider.release.set()
            for frontend in (a, b, c):
                if frontend is not None and frontend.process.poll() is None:
                    frontend.stop()
            for lane in ("2", ""):
                op("stop", workspace, lane)
            provider.server.shutdown()
    print("remote lanes smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
