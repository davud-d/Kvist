# -*- coding: utf-8 -*-
"""The browser MCP server: pointer paths, key chords, the WebSocket client, finding the browser, batches, Stop."""
from __future__ import annotations

import base64
import hashlib
import json
import queue
import random
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kernel.browser_mcp import endpoint
from kernel.browser_mcp.controller import STOPPED, Controller, ToolError, normalize_url
from kernel.browser_mcp.drivers import Tab
from kernel.browser_mcp.keys import parse_chord
from kernel.browser_mcp.motion import path, travel_ms
from kernel.browser_mcp.server import TOOLS, Server
from kernel.browser_mcp.websocket import WebSocket


def test_the_pointer_ends_exactly_on_the_target_and_long_reaches_take_longer():
    points = path((10, 10), (900, 500), random.Random(1))

    assert points[-1] == (900, 500)
    assert len(points) > len(path((10, 10), (60, 40), random.Random(1)))
    assert travel_ms(1000) < 1100 and travel_ms(1000) > travel_ms(50)
    assert path((5, 5), (6, 5)) == [(6, 5)]


def test_chords_name_the_key_not_the_letter_it_would_type():
    chord = parse_chord("Ctrl+A")
    assert chord.modifiers == ("ctrl",) and chord.key.key == "a" and chord.key.code == "KeyA"
    assert parse_chord("ctrl+shift+t").key.key == "T"
    assert parse_chord("Esc").key.key == "Escape" and parse_chord("enter").key.text == "\r"
    assert parse_chord("Cmd+L").modifiers == ("meta",)
    with pytest.raises(ValueError):
        parse_chord("Hyper+X")
    with pytest.raises(ValueError):
        parse_chord("NoSuchKey")


def _serve_websocket(sock: socket.socket, frames: list[bytes]) -> None:
    connection, _ = sock.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += connection.recv(4096)
    key = re.search(rb"Sec-WebSocket-Key: (\S+)", request).group(1)
    accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
    connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                       b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n" + b"".join(frames))
    time.sleep(0.5)
    connection.close()


def test_the_websocket_client_joins_fragments_and_answers_pings():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    text = json.dumps({"id": 1, "result": {"word": "привет" * 30}}).encode()
    frames = [bytes([0x89, 2]) + b"hi", bytes([0x01, 126]) + struct.pack("!H", 100) + text[:100],
              bytes([0x80, 126]) + struct.pack("!H", len(text) - 100) + text[100:]]
    thread = threading.Thread(target=_serve_websocket, args=(server, frames), daemon=True)
    thread.start()

    ws = WebSocket(f"ws://127.0.0.1:{server.getsockname()[1]}/devtools/browser/x")

    assert json.loads(ws.recv())["result"]["word"] == "привет" * 30
    ws.close()


def test_the_endpoint_spec_and_the_port_file(tmp_path):
    assert endpoint.parse_spec("9333") == endpoint.Candidate("127.0.0.1", 9333, "")
    assert endpoint.parse_spec("ws://127.0.0.1:9222/session").ws_path == "/session"
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("40123\n/devtools/browser/abc\n")
    assert endpoint.read_port_file(port_file) == endpoint.Candidate("127.0.0.1", 40123, "/devtools/browser/abc")


def test_an_app_on_the_debugging_port_is_not_taken_for_the_browser(monkeypatch):
    monkeypatch.setattr(endpoint, "_cdp_version", lambda _candidate: {
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/x",
        "User-Agent": "Mozilla/5.0 Chrome/151.0 Spotify/1.3.8 Safari/537.36"})

    with pytest.raises(endpoint.EndpointError, match="Spotify"):
        endpoint.connect_candidate(endpoint.Candidate("127.0.0.1", 9222, ""))


def test_only_web_addresses_open():
    assert normalize_url("example.org/a") == "https://example.org/a"
    assert normalize_url("localhost:8080/x") == "https://localhost:8080/x"
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "chrome://settings"):
        with pytest.raises(ToolError):
            normalize_url(bad)


class FakeDriver:
    """Answers the page helpers' calls the way the page would; records the input it gets."""

    protocol = "cdp"
    product = "Fake/1"

    def __init__(self) -> None:
        self.input: list[tuple] = []
        self.stopped = False
        self.page: dict[str, object] = {"k.info()": {"w": 1000, "h": 700, "url": "http://site/", "title": "Site",
                                                     "ready": "complete", "mac": False}}

    def tabs(self):
        return [Tab("T1", "http://site/", "Site")]

    def activate(self, tab):
        pass

    def add_preload(self, tab, source):
        return "p1"

    def remove_preload(self, tab, handle):
        self.input.append(("unpreload",))

    def evaluate(self, tab, source, timeout=15.0):
        if not source.startswith("(async"):
            return "3"
        expression = source.rsplit("return JSON.stringify(await (", 1)[1].rsplit("));", 1)[0]
        if expression == "k.stopped":
            return json.dumps(self.stopped)
        if expression.startswith("k.target("):
            ref = json.loads(expression[len("k.target("):-1])
            return json.dumps({"x": 100, "y": 200, "label": f"button {ref}"} if ref != "e9" else {"error": "e9 is gone"})
        if expression.startswith("k.at("):
            return json.dumps("div")
        return json.dumps(self.page.get(expression))

    def move(self, tab, points, cancelled):
        self.input.append(("move", points[-1]))

    def button(self, tab, x, y, pressed, button, clicks):
        self.input.append(("down" if pressed else "up", button, clicks))

    def press(self, tab, chord, hold=0.0):
        self.input.append(("press", chord.modifiers, chord.key.key, hold))

    def type_text(self, tab, text, pauses):
        self.input.append(("type", text))

    def insert_text(self, tab, text):
        self.input.append(("insert", text))

    def wheel(self, tab, x, y, dx, dy):
        self.input.append(("wheel", dx, dy))

    def screenshot(self, tab):
        return b"\xff\xd8jpeg"

    def close(self):
        pass


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr("kernel.browser_mcp.controller._pause", lambda low, high: None)
    driver = FakeDriver()
    return driver, Controller(connect=lambda _spec: driver)


def test_a_batch_runs_in_order_with_held_keys_and_buttons(fake):
    driver, control = fake

    report, ok = control.act([{"click": "e1", "count": 2}, {"press": "Ctrl+Shift+T", "hold_ms": 300},
                              {"type": "привет", "into": [50, 60]}, {"click": "e2", "hold_ms": 10}])

    assert ok, report
    downs = [item for item in driver.input if item[0] == "down"]
    assert downs[:2] == [("down", "left", 1), ("down", "left", 2)]
    assert ("press", ("ctrl", "shift"), "T", 0.3) in driver.input
    assert ("type", "привет") in driver.input
    assert "double-clicked button e1" in report and "held 10 ms" in report


def test_a_batch_stops_at_the_first_failure_and_says_what_was_not_run(fake):
    driver, control = fake

    report, ok = control.act([{"click": "e1"}, {"click": "e9"}, {"press": "Enter"}])

    assert not ok
    assert "2. FAILED: e9 is gone; steps 3–3 were not run" in report
    assert not any(item[0] == "press" for item in driver.input)


def test_a_long_text_is_inserted_at_once_and_bad_steps_are_explained(fake):
    driver, control = fake

    control.act([{"type": "x" * 500}])
    report, ok = control.act([{"click": "e1", "press": "Enter"}])

    assert ("insert", "x" * 500) in driver.input
    assert not ok and "exactly one of" in report


def test_after_stop_every_tool_refuses(fake):
    driver, control = fake
    control.snapshot(False)
    driver.stopped = True

    with pytest.raises(ToolError, match="pressed Stop"):
        control.act([{"click": "e1"}])
    with pytest.raises(ToolError, match="pressed Stop"):
        control.open_tab("example.org")
    assert ("unpreload",) in driver.input


def test_the_server_returns_a_look_after_the_batch(fake):
    driver, control = fake
    replies = []
    server = Server(control, replies.append)

    result = server.call("browser_act", {"steps": [{"click": "e1"}], "look": "screenshot"})

    assert result["content"][1] == {"type": "image", "mimeType": "image/jpeg",
                                    "data": base64.b64encode(b"\xff\xd8jpeg").decode()}
    assert {tool["name"] for tool in TOOLS} >= {"browser_act", "browser_snapshot", "browser_open_tab", "browser_close_tab"}
    assert STOPPED.startswith("The person pressed Stop")


# --- the real thing ---------------------------------------------------------------------

PAGE = """<!doctype html><meta charset="utf-8"><title>Form</title>
<label>Name <input id="name"></label> <button onclick="log('next')">Next</button>
<div style="height:2000px"></div><button onclick="log('far')">Far</button><pre id="log"></pre>
<script>function log(m){document.getElementById('log').textContent += m + '\\n'}
addEventListener('mousedown', e => log('down ' + e.isTrusted));
addEventListener('keydown', e => e.key === 'Enter' && log('enter ' + e.isTrusted));</script>"""


@pytest.mark.slow
def test_a_real_chromium_gets_trusted_input_and_scrolls_by_wheel(tmp_path):
    binary = shutil.which("chromium") or shutil.which("google-chrome")
    if binary is None:
        pytest.skip("no Chromium here")
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    site = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args: SimpleHTTPRequestHandler(*args, directory=str(tmp_path)))
    threading.Thread(target=site.serve_forever, daemon=True).start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    browser = subprocess.Popen([binary, "--headless=new", f"--remote-debugging-port={port}",
                                f"--user-data-dir={tmp_path / 'profile'}", "--no-first-run", "about:blank"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while endpoint._cdp_version(endpoint.Candidate("127.0.0.1", port, "")) is None:
            assert time.monotonic() < deadline, "Chromium did not open its debugging port"
            time.sleep(0.2)
        control = Controller(str(port))
        control.navigate(f"http://127.0.0.1:{site.server_address[1]}/", "")
        snapshot = control.snapshot(True)
        refs = dict(re.findall(r"\[(e\d+)\] \w+ \"(\w+)\"", snapshot))
        by_name = {name: ref for ref, name in refs.items()}

        report, ok = control.act([{"type": "Kvist\n", "into": by_name["Name"]}, {"click": by_name["Next"]},
                                  {"click": by_name["Far"]}])

        assert ok, report
        log = control.page_text(5000)
        assert "enter true" in log and "next" in log and "far" in log and "down false" not in log
        assert control._work("scrollY") > 0
        control.shutdown()
    finally:
        browser.terminate()
        browser.wait(10)
        site.shutdown()


class FakeBrowser:
    """A browser end of the relay's WebSocket: answers every command and remembers it."""

    def __init__(self, url: str) -> None:
        self.sent: list[dict] = []
        self.sessions = 0
        self._inbox: "queue.Queue[str]" = queue.Queue()
        FakeBrowser.last = self

    def send(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        result = {"script": f"s{len(self.sent)}"} if message["method"] == "script.addPreloadScript" else {}
        if message["method"] == "session.new":
            self.sessions += 1
            result = {"sessionId": "x", "capabilities": {"browserName": "waterfox", "browserVersion": "140"}}
        self._inbox.put(json.dumps({"id": message["id"], "type": "success", "result": result}))

    def recv(self) -> str:
        return self._inbox.get()

    def close(self) -> None:
        pass


def test_the_relay_shares_one_session_and_cleans_up_after_a_server_that_died():
    from kernel.browser_mcp.drivers import BidiDriver
    from kernel.browser_mcp.relay import Relay

    relay = Relay("ws://127.0.0.1:1/session", connect=FakeBrowser)

    def server():
        ours, theirs = socket.socketpair()
        threading.Thread(target=relay.serve_client, args=(theirs,), daemon=True).start()
        return ours, BidiDriver(_SocketLines(ours))

    first, driver = server()
    assert driver.product == "waterfox 140"
    assert driver.add_preload("T1", "1") == "s2"
    driver._call("input.performActions", {"context": "T1", "actions": []})
    first.shutdown(socket.SHUT_RDWR)  # killed: no session.end, no cleanup of its own
    deadline = time.monotonic() + 5
    while not any(m["method"] == "input.releaseActions" for m in FakeBrowser.last.sent):
        assert time.monotonic() < deadline, FakeBrowser.last.sent
        time.sleep(0.05)

    _second, again = server()

    methods = [m["method"] for m in FakeBrowser.last.sent]
    assert {"method": "script.removePreloadScript", "params": {"script": "s2"}}.items() <= {
        k: v for k, v in next(m for m in FakeBrowser.last.sent if m["method"] == "script.removePreloadScript").items()
        if k != "id"}.items()
    assert methods.count("session.new") == 1 and again.product == "waterfox 140"


class _SocketLines:
    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._reader = sock.makefile("rb")

    def send(self, text: str) -> None:
        self._sock.sendall(text.encode() + b"\n")

    def recv(self) -> str:
        from kernel.browser_mcp.websocket import WebSocketError

        line = self._reader.readline()
        if not line:
            raise WebSocketError("closed")
        return line.decode()

    def close(self) -> None:
        self._sock.close()
