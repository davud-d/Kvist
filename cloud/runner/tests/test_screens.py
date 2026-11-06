# -*- coding: utf-8 -*-
"""The runner keeps a screen's address and token on this machine and relays only pictures."""
from __future__ import annotations

import asyncio
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from kvist_runner import screens
from kvist_runner.screens import ScreenRelay, is_loopback

TOKEN = "SCREEN-TOKEN-0123456789abcdef"
JPEG = b"\xff\xd8\xff\xe0" + b"a frame" * 10


class Viewer:
    """A stand-in for Kvist's loopback viewer: records what it was asked for."""

    def __init__(self, body: bytes = JPEG, content_type: str = "image/jpeg", status: int = 200) -> None:
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def frame_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/frame.jpg?t={TOKEN}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def viewer():
    made: list[Viewer] = []

    def make(**kwargs) -> Viewer:
        made.append(Viewer(**kwargs))
        return made[-1]

    yield make
    for item in made:
        item.close()


def announce(frame_url: str, state: str = "running") -> dict:
    return {"type": "screen", "screen_id": "browser", "skill": "browser", "title": "Browser", "state": state,
            "url": frame_url.replace("frame.jpg", ""), "stream_url": frame_url, "frame_url": frame_url}


def test_the_event_loses_its_address_and_token():
    relay = ScreenRelay(1, lambda _m: True)
    clean = relay.sanitize(announce(f"http://127.0.0.1:4100/frame.jpg?t={TOKEN}"))
    assert clean["url"] == clean["stream_url"] == clean["frame_url"] == ""
    assert TOKEN not in str(clean)
    assert (clean["screen_id"], clean["state"], clean["title"]) == ("browser", "running", "Browser")


@pytest.mark.parametrize(
    "url", ["http://example.com/frame.jpg", "https://127.0.0.1:1/x", "http://127.0.0.1/x", "file:///etc/passwd",
            "http://169.254.169.254:80/latest", "http://127.0.0.1.evil.example:80/x"],
)
def test_only_a_loopback_viewer_is_ever_fetched(url):
    assert not is_loopback(url)
    sent: list[dict] = []
    relay = ScreenRelay(1, sent.append)
    relay.sanitize(announce(url))
    relay.watch("browser", 5)
    assert not relay._pumps and sent == []


async def test_pictures_flow_only_while_someone_watches(viewer, monkeypatch):
    monkeypatch.setattr(screens, "FRAME_INTERVAL_SECONDS", 0.02)
    server = viewer()
    sent: list[dict] = []
    relay = ScreenRelay(7, lambda message: sent.append(message) or True)
    relay.sanitize(announce(server.frame_url))
    await asyncio.sleep(0.15)
    assert sent == [] and server.requests == []  # nobody asked: nothing is read

    relay.watch("browser", 1.0)
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.02)
    assert sent[0]["type"] == "screen_frame" and (sent[0]["task_id"], sent[0]["screen_id"]) == (7, "browser")
    assert base64.b64decode(sent[0]["jpeg"]) == JPEG
    # The smaller picture was asked for, and the same frame is not sent twice.
    assert "w=960" in server.requests[0] and "q=65" in server.requests[0]
    await asyncio.sleep(0.15)
    assert len(sent) == 1
    await relay.stop()


async def test_the_pump_stops_when_the_lease_runs_out(viewer, monkeypatch):
    monkeypatch.setattr(screens, "FRAME_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(screens, "MAX_LEASE_SECONDS", 0.2)
    server = viewer()
    relay = ScreenRelay(1, lambda _m: True)
    relay.sanitize(announce(server.frame_url))
    relay._leases["browser"] = 0.0
    relay.watch("browser", 0.01)  # raised to the 1 s floor, capped at the 0.2 s maximum
    await asyncio.sleep(0.6)
    seen = len(server.requests)
    await asyncio.sleep(0.2)
    assert len(server.requests) == seen
    await relay.stop()


async def test_a_closed_screen_stops_everything(viewer, monkeypatch):
    monkeypatch.setattr(screens, "FRAME_INTERVAL_SECONDS", 0.02)
    server = viewer()
    relay = ScreenRelay(1, lambda _m: True)
    relay.sanitize(announce(server.frame_url))
    relay.watch("browser", 5)
    relay.sanitize(announce("", state="closed"))
    await asyncio.sleep(0.1)
    assert not relay._frame_urls and not relay._pumps


@pytest.mark.parametrize("kwargs", [{"content_type": "text/html"}, {"body": b"x" * (screens.MAX_FRAME_BYTES + 5)}])
async def test_anything_but_a_small_picture_is_not_relayed(viewer, kwargs):
    server = viewer(**kwargs)
    assert await asyncio.to_thread(screens._fetch, server.frame_url) == b""


async def test_a_line_from_kvist_reaches_the_server_without_the_address(tmp_path):
    import json

    from test_task import Recorder, make

    recorder = Recorder()
    task = make(tmp_path, "quick", recorder)
    line = json.dumps(announce(f"http://127.0.0.1:4100/frame.jpg?t={TOKEN}")).encode()
    await task._handle_line(line)
    assert TOKEN not in json.dumps(recorder.messages)
    assert recorder.events("screen")[0]["frame_url"] == ""
    assert task.screens._frame_urls["browser"].endswith(TOKEN)  # kept here, on this machine, to fetch frames


async def test_a_cancel_for_a_task_this_runner_does_not_have_is_answered(tmp_path):
    from test_task import config

    from kvist_runner.runner import Runner

    runner = Runner(config(tmp_path))
    await runner.handle({"type": "control", "task_id": 9, "action": "cancel"})
    await runner.handle({"type": "control", "task_id": 9, "action": "pause"})
    assert list(runner._outbox) == [{"type": "task_exit", "task_id": 9, "exit_code": None, "reason": "cancelled"}]
