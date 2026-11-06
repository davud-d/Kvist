# -*- coding: utf-8 -*-
"""A stand-in for ``kvist --frontend stdio-multi``: real Mode B framing, scripted turns.

The goal text picks the script:
  quick        writes hello.txt, answers, done
  slow N       like quick, but works N seconds first
  ask          asks for a confirmation and waits for the answer
  hang         works until paused or cancelled
  stubborn     ignores pause, stops only on cancel
  long-command a shell command that never ends
  leak         prints the model key it was given in a message (the runner must scrub it)
  auth-error   the model refuses the key
  rate-limit   the model keeps answering 429 until paused
  bg N         starts a background job that runs N seconds after the answer
  say          answers with how many earlier messages of this conversation it remembers (sessions are files)
  screen N     opens a virtual desktop (a loopback viewer behind a secret token, like Kvist's own) for N seconds
Never calls a model, never touches the network.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

lock = threading.Lock()
inbox: list[dict] = []
arrived = threading.Event()


def emit(event: dict) -> None:
    with lock:
        sys.stdout.write(json.dumps(event) + "\n")
        sys.stdout.flush()


def read_stdin() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            message = {"type": "goal", "text": line}
        with lock:
            inbox.append(message)
        arrived.set()
    with lock:
        inbox.append({"type": "eof"})
    arrived.set()


def next_message(timeout: float | None = None) -> dict | None:
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        with lock:
            if inbox:
                return inbox.pop(0)
            arrived.clear()
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            return None
        arrived.wait(remaining)


def control_pending() -> dict | None:
    with lock:
        for index, message in enumerate(inbox):
            if message.get("type") == "eof":
                # Left in place: the main loop must see it too and exit.
                return message
            if message.get("type") in ("pause", "cancel"):
                return inbox.pop(index)
    return None


def stopped_by(control: dict) -> bool:
    if control["type"] == "pause":
        emit({"type": "task_state", "state": "paused", "reason": ""})
        emit({"type": "final", "message": "Paused.", "user_messages": [], "outcome": "incomplete", "paused": True})
    elif control["type"] == "cancel":
        emit({"type": "task_state", "state": "cancelled", "reason": ""})
        emit({"type": "final", "message": "Stopped.", "user_messages": [], "outcome": "incomplete"})
    else:
        return True
    emit({"type": "turn_end"})
    return True


def work(seconds: float) -> bool:
    """Busy for ``seconds``; True if a pause or cancel arrived meanwhile (the turn is over)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        control = control_pending()
        if control is not None:
            return stopped_by(control)
        time.sleep(0.05)
    return False


SCREEN_TOKEN = "fake-screen-token-0123456789"


def start_viewer() -> ThreadingHTTPServer:
    """A stand-in for Kvist's screen viewer: loopback only, a token in the URL, two alternating pictures."""
    frames = [base64.b64decode(item) for item in json.loads(Path(__file__).with_name("screen_frames.json").read_text())]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            if not self.path.startswith("/frame.jpg") or f"t={SCREEN_TOKEN}" not in self.path:
                self.send_response(401)
                self.end_headers()
                return
            body = frames[int(time.time()) % len(frames)]
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def screen_event(server: ThreadingHTTPServer, state: str) -> dict:
    base = f"http://127.0.0.1:{server.server_address[1]}"
    closed = state == "closed"
    return {"type": "screen", "screen_id": "desktop", "skill": "desktop", "title": "Desktop", "state": state,
            "url": "" if closed else f"{base}/?t={SCREEN_TOKEN}",
            "stream_url": "" if closed else f"{base}/stream.mjpeg?t={SCREEN_TOKEN}",
            "frame_url": "" if closed else f"{base}/frame.jpg?t={SCREEN_TOKEN}"}


def write_result(workspace: Path, step: int) -> None:
    (workspace / "hello.txt").write_text("hello from the cloud\n", encoding="utf-8")
    emit({"type": "step", "step": step, "action_type": "write_file", "action_args": {"path": "hello.txt"},
          "action_description": "writes hello.txt", "call_id": f"c{step}", "batch_id": "b1", "user_message": "",
          "thought": "", "plan_step": "working", "reasoning_seconds": 0})
    emit({"type": "action_result", "step": step, "action_type": "write_file", "ok": True, "summary": "Wrote 1 line",
          "output": "", "exit_code": None, "path": "hello.txt", "first_line": 1, "lines_added": 1,
          "lines_removed": 0, "patch": "--- /dev/null\n+++ b/hello.txt\n@@ -0,0 +1 @@\n+hello from the cloud\n",
          "call_id": f"c{step}", "batch_id": "b1"})


def finish(text: str = "Done: wrote hello.txt.") -> None:
    emit({"type": "usage", "input_tokens": 10, "output_tokens": 5, "context_tokens": 10, "context_limit": 1000,
          "model": "fake", "kind": "step", "step": 1})
    emit({"type": "final", "message": text, "user_messages": [], "outcome": "done"})
    emit({"type": "task_state", "state": "completed", "reason": ""})
    emit({"type": "turn_end"})


def run_turn(goal: str, workspace: Path) -> None:
    words = goal.split()
    script = words[0] if words else "quick"
    emit({"type": "task_state", "state": "running", "reason": ""})
    emit({"type": "route", "role": "coder", "source": "explicit"})
    if script == "slow":
        if work(float(words[1]) if len(words) > 1 else 2.0):
            return
    if script == "hang":
        while not work(3600):
            pass
        return
    if script == "stubborn":
        # Ignores pause (a command that will not stop); only cancel ends it.
        while True:
            control = control_pending()
            if control is not None and control["type"] in ("cancel", "eof"):
                stopped_by(control)
                return
            time.sleep(0.05)
    if script == "long-command":
        emit({"type": "step", "step": 1, "action_type": "run_shell", "action_args": {"command": ["make"]},
              "action_description": "builds", "call_id": "c1", "batch_id": "b1", "user_message": "",
              "thought": "", "plan_step": "working", "reasoning_seconds": 0})
        while not work(3600):
            pass
        return
    if script == "leak":
        key = next((value for name, value in os.environ.items() if name.endswith("_API_KEY")), "")
        emit({"type": "message", "markdown": f"my key is {key}", "source": "reply"})
    if script == "auth-error":
        emit({"type": "error", "message": "Authentication failed (HTTP 401): invalid api key"})
        emit({"type": "turn_end"})
        return
    if script == "rate-limit":
        while True:
            emit({"type": "status", "user_message": "Rate limit exceeded: 429", "status": "retrying", "step": 1,
                  "progress": 0, "plan_step": "working", "is_error": True})
            if work(0.2):
                return
    if script == "ask":
        emit({"type": "input_request", "request_id": "r1", "kind": "confirm", "title": "Run rm -rf build?",
              "body": "rm -rf build", "options": None, "current_value": "", "help_text": ""})
        emit({"type": "task_state", "state": "awaiting_input", "reason": ""})
        answer = next_message()
        while answer is not None and answer.get("request_id") != "r1":
            if answer.get("type") in ("pause", "cancel", "eof"):
                stopped_by(answer)
                return
            answer = next_message()
        emit({"type": "task_state", "state": "running", "reason": ""})
        if not (answer or {}).get("approved"):
            finish("You said no, so nothing was removed.")
            return
    if script == "screen":
        viewer = start_viewer()
        emit(screen_event(viewer, "running"))
        stopped = work(float(words[1]) if len(words) > 1 else 8.0)
        emit(screen_event(viewer, "closed"))
        viewer.shutdown()
        if stopped:
            return
    if script == "bg":
        emit({"type": "job_started", "job_id": "job_1", "command": ["sleep", words[1] if len(words) > 1 else "2"],
              "purpose": "dev server", "call_id": "c9"})
    write_result(workspace, 1)
    finish()
    if script == "bg":
        if work(float(words[1]) if len(words) > 1 else 2.0):
            # stdin closed: Kvist kills what the answer left running and exits.
            emit({"type": "job_finished", "job_id": "job_1", "status": "stopped", "exit_code": None, "output": ""})
            return
        emit({"type": "job_finished", "job_id": "job_1", "status": "exited", "exit_code": 0, "output": ""})


def session_file(workspace: Path, name: str) -> Path:
    folder = workspace / ".fake-sessions"
    folder.mkdir(exist_ok=True)
    return folder / f"{name}.txt"


def main() -> int:
    args = sys.argv[1:]
    workspace = Path(args[args.index("--workspace") + 1]) if "--workspace" in args else Path.cwd()
    threading.Thread(target=read_stdin, daemon=True).start()
    # Like Kvist: every launch begins a session of its own; `session switch NAME` goes back into an older one.
    session = f"fake-{os.getpid()}"
    emit({"type": "hello", "protocol_version": 1, "provider": os.environ.get("AGENT_LLM_PROVIDER", ""),
          "session": session, "has_checkpoint": False})
    while True:
        message = next_message()
        if message is None or message.get("type") == "eof":
            return 0
        if message.get("type") == "goal":
            text = str(message.get("text", ""))
            remembered = session_file(workspace, session)
            earlier = remembered.read_text(encoding="utf-8").splitlines() if remembered.exists() else []
            remembered.write_text("\n".join([*earlier, text]) + "\n", encoding="utf-8")
            if text.split()[:1] == ["say"]:
                emit({"type": "task_state", "state": "running", "reason": ""})
                remembered_words = "; ".join(earlier)
                emit({"type": "message", "markdown": f"I remember {len(earlier)} earlier messages: {remembered_words}",
                      "source": "reply"})
                emit({"type": "task_state", "state": "completed", "reason": ""})
                emit({"type": "turn_end"})
                continue
            run_turn(text, workspace)
        elif message.get("type") == "command" and message.get("name") == "new":
            wanted = (message.get("args") or [""])[0]
            made = bool(wanted) and not session_file(workspace, wanted).exists()
            if made:
                session = wanted
                session_file(workspace, session).write_text("", encoding="utf-8")
                emit({"type": "session", "session": session, "has_checkpoint": False})
            emit({"type": "command_result", "name": "new", "ok": made, "message": "", "effects": {}})
        elif message.get("type") == "command" and message.get("name") == "session":
            wanted = (message.get("args") or ["", ""])[1]
            if session_file(workspace, wanted).exists():
                session = wanted
                emit({"type": "session", "session": session, "has_checkpoint": True})
            emit({"type": "command_result", "name": "session", "ok": session == wanted, "message": "", "effects": {}})
        elif message.get("type") == "command" and message.get("name") == "resume":
            run_turn("quick", workspace)


if __name__ == "__main__":
    sys.exit(main())
