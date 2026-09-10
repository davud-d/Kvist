#!/usr/bin/env python3
"""A minimal OpenAI-compatible gateway, for recording the guides against.

Kvist queries the provider's /v1/models endpoint on every launch. Pointed at a
real provider with no key, that fails, and every guide would carry an
authentication error across the screen — which says nothing about the thing the
guide is showing.

OmniRoute exists for exactly this shape: it is the built-in "local
OpenAI-compatible gateway". The guides therefore run against a gateway that
really is listening — this one. Nothing is faked in the recording: Kvist asks a
local server what it serves, and a local server answers.

It listens on 20129 rather than OmniRoute's default 20128, so that a real
gateway already running on the default port is neither disturbed nor recorded.
The recording config points OmniRoute here explicitly.

It serves a model list and, for the credential check, a single canned
completion. It never reaches the network and it is not a model — the guides
never ask it to write anything.

    python3 stub_gateway.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 20129

# Named for the role each one is wired to in the guides, so a viewer is never
# left wondering which real model these are. They are not real models.
MODELS = ("local-fast", "local-smart", "local-planner")


class Gateway(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
        if self.path.rstrip("/").endswith("/models"):
            self._send({
                "object": "list",
                "data": [
                    {"id": name, "object": "model", "owned_by": "local"}
                    for name in MODELS
                ],
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._send({
                "id": "local-1",
                "object": "chat.completion",
                "model": MODELS[0],
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })
            return
        self.send_error(404)

    def log_message(self, *_args) -> None:
        """Quiet: this runs beside a recording."""


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    server = ThreadingHTTPServer(("127.0.0.1", port), Gateway)
    print(f"local gateway on http://127.0.0.1:{port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
