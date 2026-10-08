# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import socket
import threading

import pytest

from kernel import tray_bar
from kernel.tray_bar_client import run_stream, send_toggle

WAYBAR = """{
    "layer": "top",
    "modules-left": [
        "custom/logo",
        "hyprland/workspaces"
    ],
    "modules-right": ["clock"],
    "custom/logo": {
        "format": "{}"
    }
}
"""
POLYBAR = """[bar/main]
modules-left = i3
modules-right = date

[bar/secondary]
modules-left = i3 date

[module/i3]
type = internal/i3
"""
COMMAND = "/usr/bin/kvist --tray-op"


@pytest.fixture
def home(tmp_path):
    (tmp_path / ".config" / "waybar").mkdir(parents=True)
    (tmp_path / ".config" / "waybar" / "config").write_text(WAYBAR, encoding="utf-8")
    (tmp_path / ".config" / "waybar" / "style.css").write_text("* { color: red; }\n", encoding="utf-8")
    (tmp_path / ".config" / "polybar").mkdir(parents=True)
    (tmp_path / ".config" / "polybar" / "config.ini").write_text(POLYBAR, encoding="utf-8")
    return tmp_path


def install(home, reloaded=None):
    return tray_bar.install(home, command=COMMAND, reload=(reloaded.append if reloaded is not None else lambda name: None))


def test_waybar_module_goes_after_the_first_left_entry_and_config_stays_valid_json(home):
    reloaded: list[str] = []
    assert install(home, reloaded) == ["waybar", "polybar"]
    assert reloaded == ["waybar", "polybar"]
    config = json.loads((home / ".config/waybar/config").read_text(encoding="utf-8"))
    assert config["modules-left"] == ["custom/logo", "custom/kvist", "hyprland/workspaces"]
    module = config["custom/kvist"]
    assert module["exec"] == f"{COMMAND} bar-stream --bar waybar"
    assert module["on-click"] == f"{COMMAND} toggle"
    assert config["custom/logo"] == {"format": "{}"}
    assert "#custom-kvist.done" in (home / ".config/waybar/style.css").read_text(encoding="utf-8")


def test_polybar_module_goes_after_the_first_module_of_the_first_bar_only(home):
    install(home)
    text = (home / ".config/polybar/config.ini").read_text(encoding="utf-8")
    assert "modules-left = i3 kvist\n" in text
    assert "modules-left = i3 date\n" in text
    assert text.count("[module/kvist]") == 1
    assert f"exec = {COMMAND} bar-stream --bar polybar" in text
    assert f"click-left = {COMMAND} toggle" in text


def test_installing_again_changes_nothing_and_never_adds_a_second_module(home):
    install(home)
    before = {path: path.read_text(encoding="utf-8") for path in (home / ".config").rglob("*") if path.is_file()}
    reloaded: list[str] = []
    assert install(home, reloaded) == []
    assert reloaded == []
    after = {path: path.read_text(encoding="utf-8") for path in (home / ".config").rglob("*") if path.is_file()}
    assert after == before
    assert (home / ".config/waybar/config").read_text(encoding="utf-8").count("custom/kvist") == 2
    assert (home / ".config/waybar/style.css").read_text(encoding="utf-8").count("#custom-kvist {") == 1


def test_parallel_installs_add_one_module(home):
    threads = [threading.Thread(target=install, args=(home,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    config = (home / ".config/waybar/config").read_text(encoding="utf-8")
    assert config.count('"custom/kvist": {') == 1
    assert json.loads(config)["modules-left"].count("custom/kvist") == 1
    assert (home / ".config/polybar/config.ini").read_text(encoding="utf-8").count("[module/kvist]") == 1


def test_first_edit_keeps_one_backup_of_the_original(home):
    install(home)
    backup = home / ".config/waybar/config.kvist-backup"
    assert backup.read_text(encoding="utf-8") == WAYBAR
    tray_bar.uninstall(home, reload=lambda name: None)
    install(home)
    assert backup.read_text(encoding="utf-8") == WAYBAR


def test_uninstall_restores_the_original_content(home):
    install(home)
    assert tray_bar.uninstall(home, reload=lambda name: None) == ["waybar", "polybar"]
    config = json.loads((home / ".config/waybar/config").read_text(encoding="utf-8"))
    assert config == json.loads(WAYBAR)
    polybar = (home / ".config/polybar/config.ini").read_text(encoding="utf-8")
    assert "kvist" not in polybar
    assert "modules-left = i3\n" in polybar
    assert tray_bar.uninstall(home, reload=lambda name: None) == []


def test_unsupported_waybar_layouts_are_left_alone(home):
    config = home / ".config/waybar/config"
    config.write_text('[{"modules-left": ["a"]}, {"modules-left": ["b"]}]', encoding="utf-8")
    (home / ".config/polybar/config.ini").unlink()
    assert install(home) == []
    config.write_text('{"layer": "top"}', encoding="utf-8")
    assert install(home) == []


def test_waybar_config_with_comments_and_a_single_left_entry(home):
    config = home / ".config/waybar/config"
    config.write_text('{\n  // top bar\n  "modules-left": [ "clock" ], /* x */\n  "clock": {}\n}\n', encoding="utf-8")
    install(home)
    text = config.read_text(encoding="utf-8")
    assert '"clock", "custom/kvist"' in text
    assert "// top bar" in text


def test_no_bar_configs_means_nothing_to_do(tmp_path):
    assert install(tmp_path) == []


def test_running_integrated_bars_requires_both_the_module_and_a_running_bar(home):
    assert tray_bar.running_integrated_bars(home, running=lambda name: True) == []
    install(home)
    assert tray_bar.running_integrated_bars(home, running=lambda name: name == "waybar") == ["waybar"]
    assert tray_bar.running_integrated_bars(home, running=lambda name: False) == []


def test_render_state_for_each_bar():
    shown = json.loads(tray_bar.render_state("waybar", "done", 2, "tip"))
    assert shown == {"text": tray_bar.GLYPH, "class": "done", "tooltip": "tip"}
    assert json.loads(tray_bar.render_state("waybar", "idle", 0, ""))["class"] == "hidden"
    assert tray_bar.render_state("polybar", "running", 1, "") == "%{F#ffb300}◆%{F-}"
    assert tray_bar.render_state("polybar", "idle", 0, "") == ""


class FakeBarDaemon:
    def __init__(self, endpoint_path, summaries):
        self.received: list[dict] = []
        self._summaries = summaries
        self._server = socket.create_server(("127.0.0.1", 0))
        self._server.settimeout(5)
        endpoint_path.write_text(
            json.dumps({"port": self._server.getsockname()[1], "token": "t"}), encoding="utf-8"
        )
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        connection, _ = self._server.accept()
        with connection, connection.makefile("r", encoding="utf-8") as reader:
            self.received.append(json.loads(reader.readline()))
            for summary in self._summaries:
                connection.sendall((json.dumps(summary) + "\n").encode("utf-8"))
            if self._summaries:
                self._server.close()
                return
            for line in reader:
                self.received.append(json.loads(line))
        self._server.close()


def test_stream_prints_hidden_then_each_summary_and_hides_again_when_the_daemon_goes(tmp_path):
    endpoint = tmp_path / "tray.json"
    daemon = FakeBarDaemon(endpoint, [{"state": "running", "count": 1, "tooltip": "a"}, {"state": "done", "count": 1, "tooltip": "b"}])
    out = io.StringIO()
    rounds = iter([True, True, False])
    run_stream("waybar", endpoint=lambda: endpoint, out=out, sleep=lambda seconds: None, keep_running=lambda: next(rounds))
    daemon.thread.join(timeout=5)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [line["class"] for line in lines] == ["hidden", "running", "done", "hidden"]
    assert daemon.received[0] == {"type": "hello", "token": "t", "role": "bar"}


def test_toggle_sends_one_message_and_reports_a_missing_daemon(tmp_path):
    endpoint = tmp_path / "tray.json"
    assert send_toggle(endpoint=lambda: endpoint) is False
    daemon = FakeBarDaemon(endpoint, [])
    assert send_toggle(endpoint=lambda: endpoint) is True
    daemon.thread.join(timeout=5)
    assert daemon.received[-1] == {"type": "toggle"}
