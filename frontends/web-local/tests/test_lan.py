# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import threading
from types import SimpleNamespace

import pytest

from kvist_web import lan
from kvist_web.http_api import ControllerHTTPServer


def test_code_is_saved_private_and_stable(tmp_path):
    path = tmp_path / "web" / "lan-code"
    code = lan.load_code(path)
    assert len(code) == lan.CODE_LENGTH
    assert set(code) <= set(lan._ALPHABET)
    assert lan.load_code(path) == code
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    assert lan.load_code(path, rotate=True) != code


def test_a_damaged_code_file_is_replaced(tmp_path):
    path = tmp_path / "lan-code"
    path.write_text("short", encoding="utf-8")
    assert len(lan.load_code(path)) == lan.CODE_LENGTH


def test_code_is_read_as_typed_on_a_phone():
    assert lan.normalize_code("k7qd-4m2x 9rtb") == "K7QD4M2X9RTB"
    assert lan.format_code("K7QD4M2X9RTB") == "K7QD-4M2X-9RTB"


@pytest.mark.parametrize(
    "name, private",
    [
        ("192.168.1.20", True), ("10.0.2.2", True), ("172.16.5.5", True), ("169.254.1.1", True), ("127.0.0.1", True),
        ("8.8.8.8", False), ("100.64.0.1", False), ("studio.local", False), ("evil.example", False), ("", False), (None, False),
    ],
)
def test_only_private_ip_literals_count_as_the_local_network(name, private):
    assert lan.is_private_host(name) is private


def test_beacon_answers_a_probe_with_name_and_port_only():
    beacon = lan.Beacon(9671, name="studio", port=0)
    beacon.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(lan.PROBE, ("127.0.0.1", beacon.port))
            reply = json.loads(client.recv(512))
    finally:
        beacon.close()
    assert reply == {"service": "kvist-web", "version": 1, "name": "studio", "port": 9671}


def test_beacon_ignores_anything_but_the_probe():
    beacon = lan.Beacon(9671, name="studio", port=0)
    beacon.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.6)
            client.sendto(b"hello", ("127.0.0.1", beacon.port))
            with pytest.raises(socket.timeout):
                client.recv(512)
    finally:
        beacon.close()


@pytest.fixture
def lan_server():
    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="admin", external_token="K7QD4M2X9RTB", lan=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def fetch(server, path, *, host=None, code="K7QD4M2X9RTB"):
    headers = {"Host": host or f"192.168.1.20:{server.server_address[1]}"}
    if code is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(f"kvist:{code}".encode()).decode()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read()
    finally:
        connection.close()


def test_phone_page_needs_the_code(lan_server):
    assert fetch(lan_server, "/m/", code=None)[0] == 401
    assert fetch(lan_server, "/m/", code="AAAAAAAAAAAA")[0] == 401
    status, kind, body = fetch(lan_server, "/m/")
    assert status == 200 and kind.startswith("text/html")
    page = body.decode()
    assert "{{KVIST_CSRF}}" not in page
    assert "/assets/mobile/app.js" in page


def test_a_lan_server_answers_a_private_address_but_never_a_name(lan_server):
    port = lan_server.server_address[1]
    assert fetch(lan_server, "/m/", host=f"10.0.2.2:{port}")[0] == 200
    assert fetch(lan_server, "/m/", host=f"evil.example:{port}")[0] == 421
    assert fetch(lan_server, "/m/", host=f"8.8.8.8:{port}")[0] == 421
    assert fetch(lan_server, "/m/", host="192.168.1.20:1")[0] == 421


def test_a_loopback_server_still_refuses_a_lan_address():
    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="admin")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        assert fetch(server, "/m/", code=None)[0] == 421
        assert fetch(server, "/m/", host=f"127.0.0.1:{server.server_address[1]}", code=None)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "path, kind",
    [
        ("/assets/mobile/pixel.css", "text/css"),
        ("/assets/mobile/sprites/squirrel-winter-1.png", "image/png"),
        ("/assets/mobile/fonts/PixelifySans.woff2", "font/woff2"),
    ],
)
def test_phone_assets_are_served(lan_server, path, kind):
    status, content_type, body = fetch(lan_server, path)
    assert status == 200 and content_type.startswith(kind) and body
