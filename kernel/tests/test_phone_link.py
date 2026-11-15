# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import time

from kernel import phone_link
from kernel.phone_link import PhoneLink


class Descriptor:
    def __init__(self, running: bool, lan: bool) -> None:
        self.running, self._lan = running, lan

    def health(self) -> bool:
        return self.running

    def lan(self) -> bool:
        return self.running and self._lan


def make(spawned, descriptor):
    def spawn(argv):
        spawned.append(argv)
        descriptor.running, descriptor._lan = True, True

    return PhoneLink(spawn=spawn, sleep=lambda seconds: None)


def test_a_controller_that_serves_phones_is_left_alone(tmp_path):
    spawned = []
    descriptor = Descriptor(True, True)
    assert make(spawned, descriptor).ensure_controller(descriptor, tmp_path / "starting") is True
    assert spawned == []


def test_no_controller_means_the_core_starts_one_for_phones_that_stops_by_itself(tmp_path):
    spawned = []
    descriptor = Descriptor(False, False)
    assert make(spawned, descriptor).ensure_controller(descriptor, tmp_path / "starting") is True
    assert len(spawned) == 1
    assert spawned[0][-3:] == ["--web-controller", "--lan", "--auto-exit"]


def test_a_browser_only_controller_is_not_fought_over_its_port(tmp_path):
    spawned = []
    descriptor = Descriptor(True, False)
    assert make(spawned, descriptor).ensure_controller(descriptor, tmp_path / "starting") is False
    assert spawned == []


def test_agents_that_start_together_start_one_controller(tmp_path):
    marker = tmp_path / "starting"
    spawned = []
    descriptor = Descriptor(False, False)
    link = PhoneLink(spawn=spawned.append, sleep=lambda seconds: None)
    phone_link.START_WAIT_SECONDS, saved = 0.0, phone_link.START_WAIT_SECONDS
    try:
        link.ensure_controller(descriptor, marker)
        link.ensure_controller(descriptor, marker)
    finally:
        phone_link.START_WAIT_SECONDS = saved
    assert len(spawned) == 1


def test_a_stale_marker_does_not_block_a_new_start(tmp_path):
    marker = tmp_path / "starting"
    marker.touch()
    old = time.time() - 3600
    os.utime(marker, (old, old))
    assert PhoneLink._claim_start(marker) is True
    assert PhoneLink._claim_start(marker) is False


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setattr(phone_link, "load_user_config", lambda: {})
    assert phone_link.enabled({}) is True
    assert phone_link.enabled({"KVIST_PHONE": "0"}) is False
    monkeypatch.setattr(phone_link, "load_user_config", lambda: {"phone_access": False})
    assert phone_link.enabled({}) is False


def test_start_does_nothing_when_off(monkeypatch):
    started = []
    monkeypatch.setattr(PhoneLink, "start", lambda self: started.append(1))
    monkeypatch.setenv("KVIST_PHONE", "0")
    phone_link.start("/tmp")
    assert started == []


def test_a_core_tells_the_controller_which_folder_it_works_in(tmp_path):
    requests = []

    class Connection:
        def request(self, method, path, headers):
            requests.append((method, path, headers))

        def getresponse(self):
            return type("Response", (), {"status": 200, "read": lambda self, size: b""})()

        def close(self):
            pass

    class Described:
        def open(self, timeout):
            return Connection(), {"admin_token": "t"}

    PhoneLink.hold(Described(), "/work/my project")
    assert requests[0][1] == "/api/admin/presence?workspace=/work/my%20project"
    assert requests[0][2] == {"Authorization": "Bearer t"}
    requests.clear()
    PhoneLink.hold(Described())
    assert requests[0][1] == "/api/admin/presence"
