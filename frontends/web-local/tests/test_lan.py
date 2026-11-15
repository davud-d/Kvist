# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from kvist_web import http_api, lan
from kvist_web.ask import ask
from kvist_web.devices import DeviceStore
from kvist_web.http_api import ControllerHTTPServer
from kvist_web.pairing import Pairing, clean_name


# ---- who this computer is ------------------------------------------------------------------------------

def test_computer_id_is_made_once_and_kept(tmp_path):
    path = tmp_path / "web" / "computer-id"
    first = lan.computer_id(path)
    assert len(first) == 32 and lan.computer_id(path) == first


@pytest.mark.parametrize(
    "system, expected_kind",
    [("Linux", "linux"), ("Darwin", "macos"), ("Windows", "windows")],
)
def test_the_operating_system_is_named_on_every_platform(monkeypatch, system, expected_kind):
    monkeypatch.setattr(lan.platform, "system", lambda: system)
    monkeypatch.setattr(lan.platform, "release", lambda: "11")
    monkeypatch.setattr(lan.platform, "mac_ver", lambda: ("15.1", ("", "", ""), ""))
    monkeypatch.setattr(lan.platform, "freedesktop_os_release", lambda: {"PRETTY_NAME": "Ubuntu 24.04 LTS"})
    kind, label = lan.os_info()
    assert kind == expected_kind
    assert label == {"linux": "Ubuntu 24.04 LTS", "macos": "macOS 15.1", "windows": "Windows 11"}[kind]


def test_a_linux_without_os_release_is_still_linux(monkeypatch):
    monkeypatch.setattr(lan.platform, "system", lambda: "Linux")

    def missing():
        raise OSError("no os-release")

    monkeypatch.setattr(lan.platform, "freedesktop_os_release", missing)
    assert lan.os_info() == ("linux", "Linux")


def test_the_user_name_falls_back_to_the_environment(monkeypatch):
    def no_passwd():
        raise KeyError("uid not found")

    monkeypatch.setattr(lan.getpass, "getuser", no_passwd)
    monkeypatch.setenv("USERNAME", "svc-kvist")
    monkeypatch.delenv("USER", raising=False)
    assert lan.user_name() == "svc-kvist"


@pytest.mark.parametrize(
    "name, private",
    [
        ("192.168.1.20", True), ("10.0.2.2", True), ("172.16.5.5", True), ("169.254.1.1", True), ("127.0.0.1", True),
        ("8.8.8.8", False), ("100.64.0.1", False), ("studio.local", False), ("evil.example", False), ("", False), (None, False),
    ],
)
def test_only_private_ip_literals_count_as_the_local_network(name, private):
    assert lan.is_private_host(name) is private


def test_beacon_tells_a_phone_who_the_computer_is():
    who = {"service": "kvist-web", "version": 2, "id": "a" * 32, "name": "studio", "os": "macos", "os_name": "macOS 15.1", "user": "ana", "port": 9671}
    beacon = lan.Beacon(9671, port=0, who=who)
    beacon.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            client.sendto(lan.PROBE, ("127.0.0.1", beacon.port))
            assert json.loads(client.recv(512)) == who
    finally:
        beacon.close()


def test_beacon_ignores_anything_but_the_probe():
    beacon = lan.Beacon(9671, port=0, who={"service": "kvist-web"})
    beacon.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(0.6)
            client.sendto(b"hello", ("127.0.0.1", beacon.port))
            with pytest.raises(socket.timeout):
                client.recv(512)
    finally:
        beacon.close()


# ---- trusted phones --------------------------------------------------------------------------------------

def test_a_phone_is_known_by_its_token_and_the_file_keeps_only_a_hash(tmp_path):
    store = DeviceStore(tmp_path / "web" / "devices.json")
    token = store.approve("phone-aaaa-1111", "Pixel 8")
    assert store.verify(token) == "phone-aaaa-1111"
    assert store.verify("wrong") is None
    assert token not in (tmp_path / "web" / "devices.json").read_text(encoding="utf-8")
    if os.name == "posix":
        assert (tmp_path / "web" / "devices.json").stat().st_mode & 0o077 == 0


def test_pairing_a_phone_again_replaces_its_token_and_forgetting_removes_all(tmp_path):
    store = DeviceStore(tmp_path / "devices.json")
    first = store.approve("phone-aaaa-1111", "Pixel 8")
    second = store.approve("phone-aaaa-1111", "Pixel 8")
    other = store.approve("phone-bbbb-2222", "Galaxy")
    assert store.verify(first) is None and store.verify(second) == "phone-aaaa-1111"
    assert store.forget_all() == 2
    assert store.verify(second) is None and store.verify(other) is None


# ---- asking the owner ---------------------------------------------------------------------------------------

def done(code=0, out=""):
    return subprocess.CompletedProcess([], code, stdout=out, stderr="")


def test_the_question_is_asked_with_the_tool_of_each_platform():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return done(0, "button returned:Allow, gave up:false" if argv[0] == "osascript" else "Yes\r\n" if argv[0] == "powershell" else "")

    has_zenity = lambda name: "/usr/bin/zenity" if name == "zenity" else None
    assert ask("T", "M", platform_name="linux", which=has_zenity, run=run, env={"DISPLAY": ":0"}) is True
    assert calls[-1][0][0] == "zenity" and "--no-markup" in calls[-1][0]
    assert ask("T", 'say "hi"', platform_name="darwin", run=run) is True
    assert calls[-1][0][0] == "osascript" and '\\"hi\\"' in calls[-1][0][2]
    assert ask("T", "M", platform_name="win32", run=run) is True
    assert calls[-1][1]["env"]["KVIST_ASK_MESSAGE"] == "M"


def test_a_refusal_a_timeout_and_a_missing_tool_are_told_apart():
    has_zenity = lambda name: "/usr/bin/zenity" if name == "zenity" else None
    env = {"WAYLAND_DISPLAY": "wayland-0"}
    assert ask("T", "M", platform_name="linux", which=has_zenity, run=lambda *a, **k: done(1), env=env) is False
    assert ask("T", "M", platform_name="darwin", run=lambda *a, **k: done(0, "button returned:, gave up:true")) is False

    def too_slow(*args, **kwargs):
        raise subprocess.TimeoutExpired("zenity", 1)

    assert ask("T", "M", platform_name="linux", which=has_zenity, run=too_slow, env=env) is False
    assert ask("T", "M", platform_name="linux", which=lambda name: None, env=env) is None
    assert ask("T", "M", platform_name="linux", which=has_zenity, env={}) is None


# ---- the pairing process -------------------------------------------------------------------------------------

class Owner:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, title, text):
        self.asked.append(text)
        return self.answers.pop(0)


def make_pairing(tmp_path, owner, clock=lambda: 1000.0):
    return Pairing(DeviceStore(tmp_path / "devices.json"), asker=owner, clock=clock, start=lambda work: work())


def test_an_allowed_phone_gets_a_token_once(tmp_path):
    pairing = make_pairing(tmp_path, Owner(True))
    request = pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")
    first = pairing.status(request)
    assert first["state"] == "approved" and first["token"]
    assert "token" not in pairing.status(request)
    assert pairing._devices.verify(first["token"]) == "phone-aaaa-1111"


def test_the_window_names_the_phone_and_its_address(tmp_path):
    owner = Owner(False)
    make_pairing(tmp_path, owner).request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")
    assert "Pixel 8" in owner.asked[0] and "192.168.1.37" in owner.asked[0]


def test_a_refused_phone_waits_before_it_may_ask_again(tmp_path):
    now = [1000.0]
    pairing = make_pairing(tmp_path, Owner(False, True), clock=lambda: now[0])
    request = pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")
    assert pairing.status(request) == {"state": "denied"}
    with pytest.raises(Exception) as error:
        pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")
    assert error.value.reason == "cooldown"
    now[0] += 31
    assert pairing.status(pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37"))["state"] == "approved"


def test_no_way_to_ask_is_not_a_refusal(tmp_path):
    pairing = make_pairing(tmp_path, Owner(None))
    assert pairing.status(pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")) == {"state": "unavailable"}


def test_only_one_question_at_a_time(tmp_path):
    started = []
    pairing = Pairing(DeviceStore(tmp_path / "devices.json"), asker=Owner(True), start=started.append)
    first = pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37")
    assert pairing.request("phone-aaaa-1111", "Pixel 8", "192.168.1.37") == first
    with pytest.raises(Exception) as error:
        pairing.request("phone-bbbb-2222", "Galaxy", "192.168.1.50")
    assert error.value.reason == "busy"
    assert len(started) == 1


@pytest.mark.parametrize("device_id", [None, "", "short", "has space in it", "x" * 65, 42])
def test_a_malformed_device_id_is_refused(tmp_path, device_id):
    with pytest.raises(Exception) as error:
        make_pairing(tmp_path, Owner()).request(device_id, "Pixel 8", "192.168.1.37")
    assert error.value.reason == "bad_request"


def test_a_phone_name_cannot_carry_control_characters_or_grow():
    assert clean_name("Pixel\n8\x1b[31m") == "Pixel8[31m"
    assert len(clean_name("x" * 500)) == 40 and clean_name("") == "Phone"


# ---- over HTTP ------------------------------------------------------------------------------------------------

@pytest.fixture
def lan_server(tmp_path):
    owner = Owner(True)
    devices = DeviceStore(tmp_path / "devices.json")
    pairing = Pairing(devices, asker=owner, start=lambda work: work())
    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="admin", lan=True, devices=devices, pairing=pairing)
    server.owner = owner
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def call(server, method, path, *, host=None, token=None, body=None, bearer=None):
    headers = {"Host": host or f"192.168.1.20:{server.server_address[1]}"}
    if token is not None:
        headers["Authorization"] = "Basic " + base64.b64encode(f"kvist:{token}".encode()).decode()
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    payload = json.dumps(body).encode() if body is not None else None
    if payload:
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read()
    finally:
        connection.close()


def pair(server, device_id="phone-aaaa-1111", name="Pixel 8"):
    status, _, body = call(server, "POST", "/pair/request", body={"device_id": device_id, "name": name})
    assert status == 200, body
    request_id = json.loads(body)["request_id"]
    return json.loads(call(server, "GET", f"/pair/status?request_id={request_id}")[2])


def test_an_untrusted_phone_sees_nothing_but_the_pairing_door(lan_server):
    assert call(lan_server, "GET", "/m/")[0] == 401
    assert call(lan_server, "GET", "/api/v1/bootstrap")[0] == 401
    assert call(lan_server, "GET", "/m/", token="made-up")[0] == 401
    assert call(lan_server, "GET", "/health")[0] == 200


def test_a_phone_the_owner_allowed_gets_in_with_its_token(lan_server):
    answer = pair(lan_server)
    assert answer["state"] == "approved"
    status, kind, body = call(lan_server, "GET", "/m/", token=answer["token"])
    assert status == 200 and kind.startswith("text/html")
    assert "{{KVIST_CSRF}}" not in body.decode() and "/assets/mobile/app.js" in body.decode()
    assert call(lan_server, "GET", "/assets/mobile/pixel.css", token=answer["token"])[0] == 200


def test_a_forgotten_phone_is_locked_out_at_once(lan_server):
    token = pair(lan_server)["token"]
    assert call(lan_server, "GET", "/m/", token=token)[0] == 200
    lan_server.devices.forget_all()
    assert call(lan_server, "GET", "/m/", token=token)[0] == 401


def test_the_pairing_door_refuses_junk_and_hostile_hosts(lan_server):
    port = lan_server.server_address[1]
    assert call(lan_server, "POST", "/pair/request", body={"device_id": "x"})[0] == 400
    assert call(lan_server, "POST", "/pair/request", host=f"evil.example:{port}", body={"device_id": "phone-aaaa-1111"})[0] == 421
    assert call(lan_server, "GET", "/m/", host=f"8.8.8.8:{port}", token="x")[0] == 421


def test_a_refused_phone_is_told_so_and_made_to_wait(lan_server):
    lan_server.owner.answers = [False]
    assert pair(lan_server)["state"] == "denied"
    status, _, body = call(lan_server, "POST", "/pair/request", body={"device_id": "phone-aaaa-1111", "name": "Pixel 8"})
    assert status == 429 and json.loads(body)["error"] == "cooldown"


def test_the_health_answer_says_whether_phones_are_served(lan_server):
    assert json.loads(call(lan_server, "GET", "/health")[2])["lan"] is True


def test_a_loopback_server_has_no_pairing_door_and_refuses_a_lan_address():
    server = ControllerHTTPServer(("127.0.0.1", 0), SimpleNamespace(), admin_token="admin")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        assert call(server, "GET", "/m/")[0] == 421
        assert call(server, "POST", "/pair/request", host=f"127.0.0.1:{server.server_address[1]}", body={"device_id": "phone-aaaa-1111"})[0] != 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_presence_counts_the_cores_that_hold_a_connection(lan_server, monkeypatch):
    monkeypatch.setattr(http_api, "PRESENCE_BEAT_SECONDS", 0.05)
    assert call(lan_server, "GET", "/api/admin/presence", bearer="wrong")[0] == 401
    connection = http.client.HTTPConnection("127.0.0.1", lan_server.server_address[1], timeout=3)
    connection.request("GET", "/api/admin/presence", headers={"Host": f"127.0.0.1:{lan_server.server_address[1]}", "Authorization": "Bearer admin"})
    response = connection.getresponse()
    assert response.status == 200 and response.read(1) == b"\n"
    assert lan_server.presence.idle_seconds() == 0.0
    # The response holds the socket too (a body with no length is read to the end of the connection).
    response.close()
    connection.close()
    deadline = time.monotonic() + 3
    while lan_server.presence.idle_seconds() == 0.0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert lan_server.presence.idle_seconds() > 0.0


@pytest.mark.parametrize(
    "path, kind",
    [
        ("/assets/mobile/pixel.css", "text/css"),
        ("/assets/mobile/sprites/squirrel-winter-1.png", "image/png"),
        ("/assets/mobile/fonts/PixelifySans.woff2", "font/woff2"),
    ],
)
def test_phone_assets_are_served_to_a_trusted_phone(lan_server, path, kind):
    token = pair(lan_server)["token"]
    status, content_type, body = call(lan_server, "GET", path, token=token)
    assert status == 200 and content_type.startswith(kind) and body


# ---- a phone works inside the folders where an agent runs -------------------------------------------------------

class FakeEvents:
    def __init__(self, envelopes):
        self._envelopes, self._sent = envelopes, False

    cursor, epoch = 0, "e"

    def read_after(self, cursor, epoch=None, timeout=0):
        if self._sent:
            time.sleep(0.1)
            return SimpleNamespace(reset=False, events=[])
        self._sent = True
        return SimpleNamespace(reset=False, events=self._envelopes)


def envelope(number, workspace, server_id="local"):
    return SimpleNamespace(id=number, workspace=workspace, to_dict=lambda: {"id": number, "workspace": workspace, "server_id": server_id, "event": {"type": "x"}})


def folder_entry(path):
    return {"path": path, "sessions": {"session_items": []}}


@pytest.fixture
def scoped(tmp_path):
    here, other = tmp_path / "project", tmp_path / "home-folder"
    here.mkdir()
    other.mkdir()
    navigation = {
        "servers": [
            {"server": {"id": "local"}, "connection": {"state": "connected"}, "workspaces": [folder_entry(str(here)), folder_entry(str(other))]},
            {"server": {"id": "gpu-box"}, "connection": {"state": "connected"}, "workspaces": [folder_entry("/srv/x")]},
        ],
        "inbox": [
            {"server_id": "local", "workspace": str(here), "session": "a"},
            {"server_id": "local", "workspace": str(other), "session": "b"},
            {"server_id": "gpu-box", "workspace": "/srv/x", "session": "c"},
        ],
    }
    opened = []
    controller = SimpleNamespace(
        navigation=lambda: navigation,
        bootstrap=lambda: {"navigation": navigation, "event_stream": {"cursor": 0, "epoch": "e"}},
        sessions=lambda server, workspace: {"session_items": []},
        send=lambda *args: {"ok": True},
        add_workspace=lambda server, path: opened.append(path) or {"workspace": path},
        events=FakeEvents([envelope(1, str(here)), envelope(2, str(other)), envelope(3, "")]),
    )
    devices = DeviceStore(tmp_path / "devices.json")
    server = ControllerHTTPServer(("127.0.0.1", 0), controller, admin_token="admin", lan=True, devices=devices,
                                  pairing=Pairing(devices, asker=Owner(True), start=lambda work: work()))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    server.here, server.other, server.opened = str(here), str(other), opened
    server.token = devices.approve("phone-aaaa-1111", "Pixel 8")
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def phone(server, method, path, body=None):
    port = server.server_address[1]
    host = f"192.168.1.20:{port}"
    basic = "Basic " + base64.b64encode(f"kvist:{server.token}".encode()).decode()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", "/m/", headers={"Host": host, "Authorization": basic})
        page = connection.getresponse()
        html = page.read().decode()
        cookie = page.getheader("Set-Cookie").split(";")[0]
        csrf = html.split('name="kvist-csrf" content="')[1].split('"')[0]
        headers = {"Host": host, "Authorization": basic, "Cookie": cookie, "X-Kvist-CSRF": csrf, "Origin": f"http://{host}", "Content-Type": "application/json"}
        connection.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def open_to_phones(server, folder):
    server.fixed_folders.add(http_api.resolve_folder(folder))


def test_a_phone_sees_only_this_computer_and_only_the_open_folders(scoped):
    open_to_phones(scoped, scoped.here)
    status, body = phone(scoped, "GET", "/api/v1/navigation")
    navigation = json.loads(body)
    assert status == 200
    assert [entry["server"]["id"] for entry in navigation["servers"]] == ["local"]
    assert [folder["path"] for folder in navigation["servers"][0]["workspaces"]] == [scoped.here]
    assert [item["session"] for item in navigation["inbox"]] == ["a"]
    boot = json.loads(phone(scoped, "GET", "/api/v1/bootstrap")[1])
    assert [folder["path"] for folder in boot["navigation"]["servers"][0]["workspaces"]] == [scoped.here]


def test_with_no_agent_running_a_phone_sees_nothing(scoped):
    navigation = json.loads(phone(scoped, "GET", "/api/v1/navigation")[1])
    assert navigation["servers"][0]["workspaces"] == [] and navigation["inbox"] == []


def test_a_phone_cannot_touch_a_folder_where_no_agent_runs(scoped):
    open_to_phones(scoped, scoped.here)
    query = "server=local&workspace="
    assert phone(scoped, "GET", f"/api/v1/sessions?{query}{scoped.here}")[0] == 200
    assert phone(scoped, "GET", f"/api/v1/sessions?{query}{scoped.other}")[0] == 403
    assert phone(scoped, "GET", f"/api/v1/history?{query}{scoped.other}&session=s")[0] == 403
    # a path that only looks like the open folder
    assert phone(scoped, "GET", f"/api/v1/sessions?{query}{scoped.here}/../home-folder")[0] == 403
    send = {"server_id": "local", "workspace": scoped.other, "session": "s", "text": "rm -rf"}
    assert phone(scoped, "POST", "/api/v1/send", send)[0] == 403
    assert phone(scoped, "POST", "/api/v1/send", {**send, "workspace": scoped.here})[0] == 200
    assert phone(scoped, "POST", "/api/v1/send", {**send, "workspace": scoped.here, "server_id": "gpu-box"})[0] == 403


def test_a_phone_cannot_add_a_folder_or_a_computer(scoped):
    open_to_phones(scoped, scoped.here)
    assert phone(scoped, "POST", "/api/v1/workspaces", {"server_id": "local", "path": "/"})[0] == 403
    assert phone(scoped, "POST", "/api/v1/servers", {"id": "x", "host": "example"})[0] == 403
    assert phone(scoped, "POST", "/api/v1/servers/connect", {"server_id": "x"})[0] == 403
    assert phone(scoped, "POST", "/api/v1/config-sync", {"server_id": "x", "credentials": []})[0] == 403
    assert phone(scoped, "GET", "/api/v1/ssh-aliases")[0] == 403


def test_the_folder_of_a_running_core_is_open_for_exactly_as_long_as_it_stays_connected(scoped, monkeypatch):
    monkeypatch.setattr(http_api, "PRESENCE_BEAT_SECONDS", 0.05)
    assert phone(scoped, "GET", f"/api/v1/sessions?server=local&workspace={scoped.here}")[0] == 403
    connection = http.client.HTTPConnection("127.0.0.1", scoped.server_address[1], timeout=3)
    connection.request("GET", f"/api/admin/presence?workspace={scoped.here}",
                       headers={"Host": f"127.0.0.1:{scoped.server_address[1]}", "Authorization": "Bearer admin"})
    response = connection.getresponse()
    assert response.read(1) == b"\n"
    assert scoped.opened == [scoped.here]
    assert phone(scoped, "GET", f"/api/v1/sessions?server=local&workspace={scoped.here}")[0] == 200
    assert phone(scoped, "GET", f"/api/v1/sessions?server=local&workspace={scoped.other}")[0] == 403
    response.close()
    connection.close()
    deadline = time.monotonic() + 3
    while scoped.phone_scope() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert phone(scoped, "GET", f"/api/v1/sessions?server=local&workspace={scoped.here}")[0] == 403


def test_the_live_stream_leaves_out_other_folders(scoped):
    open_to_phones(scoped, scoped.here)
    port = scoped.server_address[1]
    host = f"192.168.1.20:{port}"
    basic = "Basic " + base64.b64encode(f"kvist:{scoped.token}".encode()).decode()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request("GET", "/m/", headers={"Host": host, "Authorization": basic})
    page = connection.getresponse()
    page.read()
    cookie = page.getheader("Set-Cookie").split(";")[0]
    connection.request("GET", "/api/v1/events?after=0", headers={"Host": host, "Authorization": basic, "Cookie": cookie})
    stream = connection.getresponse()
    seen = []
    for _ in range(12):
        line = stream.fp.readline().decode()
        if line.startswith("data:"):
            seen.append(json.loads(line[5:])["id"])
        if len(seen) == 2:
            break
    stream.close()
    connection.close()
    assert seen == [1, 3]


# ---- a controller that lost its interface files ----------------------------------------------------------------

def test_lost_interface_files_answer_500_and_never_drop_the_connection(lan_server, monkeypatch):
    token = pair(lan_server)["token"]

    def gone():
        raise RuntimeError("Kvist web client assets are missing from this installation.")

    monkeypatch.setattr(http_api, "client_root", gone)
    for path in ("/", "/m/", "/assets/mobile/pixel.css"):
        status, kind, body = call(lan_server, "GET", path, token=token)
        assert status == 500, path
        assert "missing" in json.loads(body)["error"]
    # what needs no file keeps answering
    assert call(lan_server, "GET", "/health")[0] == 200
