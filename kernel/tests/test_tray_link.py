# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import socket
import threading

import pytest

from kernel import tray_link, user_config
from kernel.tray_link import TrayLink


class FakeDaemon:
    """Loopback server that records the NDJSON lines its clients send."""

    def __init__(self, endpoint_path):
        self.lines: list[dict] = []
        self._lock = threading.Condition()
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(5)
        port = self._server.getsockname()[1]
        endpoint_path.write_text(json.dumps({"port": port, "token": "secret"}), encoding="utf-8")
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        try:
            connection, _ = self._server.accept()
        except OSError:
            return
        with connection, connection.makefile("r", encoding="utf-8") as reader:
            for line in reader:
                with self._lock:
                    self.lines.append(json.loads(line))
                    self._lock.notify_all()

    def wait_for(self, count: int) -> list[dict]:
        with self._lock:
            assert self._lock.wait_for(lambda: len(self.lines) >= count, timeout=5)
            return list(self.lines)

    def close(self) -> None:
        self._server.close()


def make_link(tmp_path, **overrides) -> TrayLink:
    options = dict(
        enabled=lambda: True,
        launcher=lambda: None,
        endpoint=lambda: tmp_path / "tray.json",
        spawn=lambda argv: None,
    )
    options.update(overrides)
    return TrayLink(tmp_path, lambda: "chat-1", **options)


def test_link_sends_hello_then_state_snapshots(tmp_path):
    daemon = FakeDaemon(tmp_path / "tray.json")
    link = make_link(tmp_path)
    try:
        link.turn_started()
        link.turn_finished()
        # Snapshots are complete, so a fast pair may reach the daemon as one.
        lines = daemon.wait_for(2)
    finally:
        link.close()
        daemon.close()
    assert lines[0] == {"type": "hello", "token": "secret"}
    assert lines[-1]["state"] in {"running", "done"}
    assert lines[1]["workspace"] == str(tmp_path)
    assert lines[1]["session"] == "chat-1"


def test_link_starts_the_daemon_once_per_process(tmp_path, monkeypatch):
    monkeypatch.setattr(TrayLink, "_daemon_start_attempted", False)
    spawned: list[list[str]] = []
    link = make_link(tmp_path, launcher=lambda: ["electron", "tray"], spawn=spawned.append)
    original = tray_link._DAEMON_START_WAIT_SECONDS
    tray_link._DAEMON_START_WAIT_SECONDS = 0.3
    other = make_link(tmp_path, launcher=lambda: ["electron", "tray"], spawn=spawned.append)
    try:
        link.turn_started()
        link.turn_finished()
        other.turn_started()
    finally:
        tray_link._DAEMON_START_WAIT_SECONDS = original
        link.close()
        other.close()
    assert spawned == [["electron", "tray"]]


def test_link_without_launcher_spawns_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(TrayLink, "_daemon_start_attempted", False)
    spawned: list[list[str]] = []
    link = make_link(tmp_path, spawn=spawned.append)
    link.turn_started()
    link.close()
    assert spawned == []


def test_disabled_link_never_touches_the_network(tmp_path):
    daemon = FakeDaemon(tmp_path / "tray.json")
    link = make_link(tmp_path, enabled=lambda: False)
    try:
        link.turn_started()
        link.close()
    finally:
        daemon.close()
    assert daemon.lines == []


def test_find_launcher_prefers_explicit_command_and_validates_it(tmp_path):
    assert tray_link.find_launcher({tray_link.LAUNCHER_ENV: '["a", "b"]'}) == ["a", "b"]
    with pytest.raises(ValueError):
        tray_link.find_launcher({tray_link.LAUNCHER_ENV: '{"a": 1}'})
    assert tray_link.find_launcher({}, tray_dir=tmp_path) is None


def test_find_launcher_uses_the_installed_electron(tmp_path):
    electron = tmp_path / "node_modules" / "electron"
    (electron / "dist").mkdir(parents=True)
    (electron / "path.txt").write_text("electron\n", encoding="utf-8")
    (electron / "dist" / "electron").write_text("", encoding="utf-8")
    assert tray_link.find_launcher({}, tray_dir=tmp_path) == [str(electron / "dist" / "electron"), str(tmp_path)]


def test_terminal_host_marker():
    assert tray_link.is_terminal_host({}) is False
    assert tray_link.is_terminal_host({tray_link.HOST_ENV: "1"}) is True


def test_tray_switch_stores_only_the_non_default(tmp_path):
    path = tmp_path / "config.json"
    assert user_config.get_tray_enabled(path) is True
    user_config.save_tray_enabled(False, path)
    assert user_config.get_tray_enabled(path) is False
    assert user_config.load_user_config(path) == {"tray_enabled": False}
    user_config.save_tray_enabled(True, path)
    assert user_config.load_user_config(path) == {}
