#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A fake OpenAI-compatible model for running Kvist Cloud locally without spending anything.

It is not a model: every task gets the same short script, played through the real Kvist core.
The agent writes CLOUD_NOTES.md with the task in it, reads it back, and answers. That is
enough to see the whole path: key check, machine, runner, Kvist, events, diff, billing.

    python3 fake_model.py [--port 8799] [--delay 1.5]

In the onboarding screen pick "Other": base URL http://127.0.0.1:8799/v1, model fake-model,
any key of 8+ characters.
"""
from __future__ import annotations

import argparse
import json
import socketserver
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fake-model"


def first_user_line(messages: list[dict]) -> str:
    for message in messages:
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            lines = [line.strip() for line in message["content"].splitlines() if line.strip()]
            return lines[0] if lines else ""
    return ""


def tool_results(messages: list[dict]) -> int:
    return sum(1 for message in messages if message.get("role") == "tool")


def reply(request: dict) -> dict:
    """The next assistant message of the script, from where the conversation is."""
    tools = {tool["function"]["name"] for tool in request.get("tools") or [] if tool.get("type") == "function"}
    messages = request.get("messages") or []
    if tools == {"ping"}:
        # Kvist's probe for native tool calling.
        return call("ping", {"ok": True})
    if not tools:
        prompt = json.dumps(messages).lower()
        # Kvist's role router asks which role a request needs; the script is a coder's.
        if "coder" in prompt and "chat" in prompt:
            return {"role": "assistant", "content": "coder"}
        return {"role": "assistant", "content": "Kvist Cloud task"}
    done = tool_results(messages)
    if done == 0 and "write_file" in tools:
        content = f"# Cloud notes\n\nFirst line of the request: {first_user_line(messages)[:200]}\n\n" \
                  "Written by the fake model.\n"
        return call("write_file", {"path": "CLOUD_NOTES.md", "content": content})
    if done == 1 and "read_file" in tools:
        return call("read_file", {"path": "CLOUD_NOTES.md"})
    return {"role": "assistant", "content": "Done: wrote CLOUD_NOTES.md and checked it."}


def call(name: str, arguments: dict) -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": f"call_{name}_{int(time.time() * 1000)}", "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)}}]}


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self) -> None:
        # No reverse DNS lookup of the bind address (slow on some machines).
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def make_handler(delay: float):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            return

        def _json(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
            if self.path.rstrip("/").endswith("/models"):
                self._json({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "local"}]})
                return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
            # A pause per answer, so the meter has something to count in a demo.
            time.sleep(delay)
            message = reply(request)
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            if request.get("stream"):
                self._stream(message, finish, usage)
                return
            self._json({"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                        "model": request.get("model", MODEL),
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage})

        def _stream(self, message: dict, finish: str, usage: dict) -> None:
            delta: dict = {"role": "assistant"}
            if message.get("tool_calls"):
                delta["tool_calls"] = [{**item, "index": index} for index, item in enumerate(message["tool_calls"])]
            else:
                delta["content"] = message.get("content") or ""
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in chunks:
                payload = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
                           "model": MODEL, **chunk}
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()
    server = Server((args.host, args.port), make_handler(args.delay))
    print(f"fake model on http://{args.host}:{server.server_address[1]}/v1 (model {MODEL})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
