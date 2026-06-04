from __future__ import annotations

from rich.console import Console

from ui import AgentUI


def test_ui_status_light_and_public_status_cleaning():
    ui = AgentUI(console=Console(record=True))
    assert ui._status_light("executing", False)[2] == "working"
    assert ui._status_light("error received", False)[2] == "adjusting"
    assert ui._clean_public_status("  many\n spaces ") == "many spaces"
    assert ui._clean_public_status("") == "Working on the task"
    assert ui._clean_public_status("x" * 200).endswith("...")


def test_ui_render_live_panel_clamps_progress_and_contains_message():
    console = Console(record=True)
    ui = AgentUI(console=console)
    panel = ui._render_live_panel("hello", "ok", 1, 200, "inspect", False)

    console.print(panel)
    rendered = console.export_text()
    assert "DevAgentXD" in rendered
    assert "Progress 100%" in rendered
    assert "hello" in rendered


def test_choose_option_returns_none_for_non_tty(monkeypatch):
    monkeypatch.setattr("ui.sys.stdin.isatty", lambda: False)
    assert AgentUI().choose_option("Title", [{"value": "x", "label": "X"}], "x") is None
