# -*- coding: utf-8 -*-
"""Agent tools smoke test: Kvist against a fake OpenAI-compatible provider.

The provider answers with scripted tool calls, one scenario per goal: create a
file, edit it, append to it, then run a command. After each turn the script
checks the workspace and the events Kvist emitted. Standard library only; the
turns are held to TURN_BUDGET_SECONDS. Exit code 0 means every check passed.

    python scripts/e2e_tools_smoke.py

e2e_install_smoke.py reuses these parts to test an installed binary.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TURN_BUDGET_SECONDS = 45
TURN_TIMEOUT_SECONDS = 15

# `python` is what Windows installs; the others ship `python3`.
PYTHON = "python" if sys.platform == "win32" else "python3"
CREATED = b"hello from kvist\n"
EDITED = b"goodbye from kvist\n"
APPENDED = EDITED + b"and another line\n"
# (goal, the tool call the fake provider makes, the file content to expect or
# None for the terminal turn, optionally the text the terminal output must hold,
# "git version" by default). A scenario's call may be a list: the provider makes
# them one per request, then answers "Done.". "{scratch}" in an argument becomes
# the scratchpad path the prompt names.
SCENARIOS = (
    ("create hello.txt with a greeting", {"name": "write_file", "arguments": {
        "path": "hello.txt", "content": CREATED.decode()}}, CREATED),
    ("change the greeting in hello.txt", {"name": "replace_in_file", "arguments": {
        "path": "hello.txt", "old_text": "hello", "new_text": "goodbye"}}, EDITED),
    ("add a line to hello.txt", {"name": "append_file", "arguments": {
        "path": "hello.txt", "content": "and another line\n"}}, APPENDED),
    ("print the git version", {"name": "run_shell", "arguments": {
        "command": ["git", "--version"], "purpose": "check git"}}, None),
    # A background job: started, followed with job_output, and its events reach the wire
    # before the turn ends.
    ("print the git version in the background", [
        {"name": "run_shell", "arguments": {
            "command": ["git", "--version"], "purpose": "check git", "background": True}},
        {"name": "job_output", "arguments": {"job_id": "job_1", "wait": 10}},
    ], None),
)

# The scratchpad: a throwaway script goes into the temp folder, runs there and leaves
# nothing in the workspace. Kvist refuses to run arbitrary code without Bubblewrap, so
# the turn exists only where the sandbox does.
if sys.platform.startswith("linux") and shutil.which("bwrap"):
    SCENARIOS += (
        ("try 2 + 2 in a scratch script", [
            {"name": "write_file", "arguments": {"path": "{scratch}/add.py", "content": "print(2 + 2)\n"}},
            {"name": "run_shell", "arguments": {"command": [PYTHON, "{scratch}/add.py"], "purpose": "run the script"}},
        ], None, "4"),
    )


class LocalServer(ThreadingHTTPServer):
    """HTTPServer without the reverse DNS lookup its server_bind makes (slow on a CI Mac)."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class FakeProvider:
    """Chat Completions endpoint that plays SCENARIOS, one per run of the agent."""

    def __init__(self) -> None:
        self.runs_started = 0
        # The file the current run changes: it is read back before the run says it is done.
        self.pending_calls: list[dict] = []
        self.requests: list[dict] = []
        self.scratch = ""
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: D401 - silence the access log
                return

            def _send(self, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                self._send({"object": "list", "data": [{"id": "fake-fast", "object": "model"},
                                                       {"id": "fake-smart", "object": "model"}]})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length) or b"{}")
                provider.requests.append(request)
                self._send(provider.completion(request))

        self.server = LocalServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def completion(self, request: dict) -> dict:
        tools = {tool["function"]["name"] for tool in request.get("tools") or []}
        messages = request.get("messages") or []
        self.scratch = self.scratch or self._scratch_named_in(messages)
        if tools == {"ping"}:
            message = self._call("ping", {"ok": True})
        elif not tools:
            message = {"role": "assistant", "content": "Smoke test"}
        elif any(item.get("role") == "tool" for item in messages):
            answered = sum(1 for item in messages if item.get("role") == "tool")
            if answered < len(self.pending_calls):
                message = self._call(**self._filled(self.pending_calls[answered]))
            else:
                message = {"role": "assistant", "content": "Done."}
        else:
            index = min(self.runs_started, len(SCENARIOS) - 1)
            scenario = SCENARIOS[index][1]
            calls = scenario if isinstance(scenario, list) else [scenario]
            self.pending_calls = [{"name": c["name"], "arguments": c["arguments"]} for c in calls]
            if SCENARIOS[index][2] is not None:
                # A run that changed a file is not finished until it has looked at the result;
                # an unverified one ends "incomplete" and keeps its checkpoint, so the next
                # goal would continue it instead of starting a run of its own.
                self.pending_calls.append({"name": "read_file", "arguments": {"path": "hello.txt"}})
            self.runs_started += 1
            message = self._call(**self._filled(self.pending_calls[0]))
        finish = "tool_calls" if message.get("tool_calls") else "stop"
        return {
            "id": "chatcmpl-smoke", "object": "chat.completion", "created": 0,
            "model": request.get("model", "fake-fast"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    @staticmethod
    def _scratch_named_in(messages: list[dict]) -> str:
        for item in messages:
            content = item.get("content")
            if isinstance(content, str) and "- Scratchpad: " in content:
                return content.split("- Scratchpad: ", 1)[1].split(" (500 MB", 1)[0]
        return ""

    def _filled(self, call: dict) -> dict:
        text = json.dumps(call["arguments"]).replace("{scratch}", json.dumps(self.scratch)[1:-1])
        return {"name": call["name"], "arguments": json.loads(text)}

    @staticmethod
    def _call(name: str, arguments: dict) -> dict:
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class Kvist:
    """One stdio-multi core process, read through a thread so waits can time out."""

    def __init__(self, argv: list[str], cwd: Path, home: Path) -> None:
        # Commands run on the host here, and a host with no network isolation (a bare CI runner,
        # Windows) refuses them unless network access is allowed explicitly. The smoke only runs
        # `git --version` against a local fake provider, so it says so.
        env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1",
                   PYTHONUNBUFFERED="1", AGENT_WRITE_APPROVAL="0", AGENT_API_INTERVAL="0",
                   AGENT_SANDBOX_NET="allow",
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        self.process = subprocess.Popen(
            argv + ["--frontend", "stdio-multi", "--provider", "custom:fake", "--role", "coder"],
            cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.events: queue.Queue[dict] = queue.Queue()
        self.stderr_lines: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        for line in self.process.stdout:
            try:
                self.events.put(json.loads(line))
            except json.JSONDecodeError:
                self.stderr_lines.append(f"[stdout, not JSON] {line.rstrip()}")

    def _read_stderr(self) -> None:
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def send_goal(self, text: str) -> None:
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def collect_until(self, event_type: str, timeout: float) -> list[dict]:
        """Events up to and including the first of ``event_type``."""
        deadline = time.monotonic() + timeout
        seen: list[dict] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no '{event_type}' event in {timeout:.0f}s; got {[e.get('type') for e in seen]}")
            try:
                event = self.events.get(timeout=remaining)
            except queue.Empty:
                continue
            seen.append(event)
            if event.get("type") == event_type:
                return seen

    def stop(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


def configure(home: Path, base_url: str) -> None:
    """Write the fake provider into the isolated user config through Kvist's own API."""
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
    sys.path.insert(0, str(ROOT))
    from kernel.config.user_config import save_custom_provider
    save_custom_provider("fake", {
        "label": "Fake", "base_url": base_url, "api_key": "smoke-test-key",
        "models": {"fast": "fake-fast", "smart": "fake-smart"}, "streaming": False})


def allow_python(workspace: Path) -> None:
    """Running a script is asked about; nobody answers in the smoke, so the project allows it up front."""
    (workspace / ".kvist").mkdir(exist_ok=True)
    (workspace / ".kvist" / "permissions.json").write_text(
        json.dumps({"allow": [f"{PYTHON} *"]}), encoding="utf-8")


def check_turn(expected: bytes | None, events: list[dict], workspace: Path, needle: str = "git version") -> None:
    """Assertions after each scenario; raises AssertionError with what was seen."""
    types = [event.get("type") for event in events]
    asked = [event for event in events if event.get("type") == "input_request"]
    assert not asked, f"the agent asked the person for approval: {asked}"
    assert "error" not in types, f"error event: {[e for e in events if e.get('type') == 'error']}"
    results = [event for event in events if event.get("type") == "action_result"]
    assert results, f"no action_result in {types}"
    assert all(r.get("ok", True) is not False for r in results), f"a tool failed: {results}"
    if expected is not None:
        actual = (workspace / "hello.txt").read_bytes()
        assert actual == expected, f"hello.txt is {actual!r}, expected {expected!r}"
        return
    # A background job's output reaches the model in a tool answer or in the job note taken
    # before the next request (job_notice is exactly that note); which one wins is timing.
    output = " ".join(str(r.get("output", "")) for r in results)
    output += " ".join(str(e.get("text", "")) for e in events if e.get("type") == "job_notice")
    assert needle in output, f"the terminal returned nothing useful: {results}"
    assert not (workspace / "add.py").exists(), "the scratch script landed in the workspace"
    if "job_started" in types:
        assert types.index("job_started") < types.index("job_finished") < types.index("turn_end"), types
        assert any(r.get("action_type") == "job_output" for r in results), f"job_output never ran: {results}"


def run_turns(core: Kvist, workspace: Path) -> float:
    """Send every scenario goal, check after each turn; returns the seconds the turns took."""
    started = mark = time.monotonic()
    for goal, _call, expected, *needle in SCENARIOS:
        core.send_goal(goal)
        events = core.collect_until("turn_end", TURN_TIMEOUT_SECONDS)
        check_turn(expected, events, workspace, *needle)
        now = time.monotonic()
        print(f"ok  turn: {goal}  (+{now - mark:.1f}s)")
        mark = now
    return time.monotonic() - started


def report_failure(error: Exception, core: Kvist, provider: FakeProvider) -> int:
    print(f"FAIL: {error}")
    print("--- core stderr:\n" + "\n".join(core.stderr_lines[-40:]))
    print(f"--- provider saw {len(provider.requests)} requests")
    return 1


def main() -> int:
    started = time.monotonic()
    provider = FakeProvider()
    print(f"provider ready  ({time.monotonic() - started:.1f}s)")
    with tempfile.TemporaryDirectory(prefix="kvist-smoke-") as scratch:
        home, workspace = Path(scratch, "home"), Path(scratch, "workspace")
        home.mkdir()
        workspace.mkdir()
        configure(home, provider.url)
        allow_python(workspace)
        launched = time.monotonic()
        core = Kvist([sys.executable, "-m", "kernel", "--workspace", str(workspace)], ROOT, home)
        try:
            core.collect_until("hello", TURN_TIMEOUT_SECONDS)
            print(f"core ready  ({time.monotonic() - launched:.1f}s after launch)")
            elapsed = run_turns(core, workspace)
        except (AssertionError, TimeoutError) as error:
            return report_failure(error, core, provider)
        finally:
            core.stop()
            provider.server.shutdown()
    print(f"all {len(SCENARIOS)} turns passed in {elapsed:.1f}s")
    if elapsed > TURN_BUDGET_SECONDS:
        print(f"FAIL: over the {TURN_BUDGET_SECONDS}s budget")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
