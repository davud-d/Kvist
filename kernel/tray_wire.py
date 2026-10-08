# -*- coding: utf-8 -*-
"""What the status-bar processes need, with standard-library imports only.

``--tray-op bar-stream`` stays alive for as long as the bar does, once per bar
instance, so importing the rest of the core here would keep tens of megabytes
resident for an icon.
"""
from __future__ import annotations

import json
from pathlib import Path

GLYPH = "◆"
COLORS = {"idle": "#8b919c", "running": "#ffb300", "done": "#2ecc71"}


def endpoint_file() -> Path:
    """``~/.kvist/tray.json``: the daemon's port and token."""
    return Path.home() / ".kvist" / "tray.json"


def render_state(bar: str, state: str, count: int, tooltip: str) -> str:
    """One line for the bar: Waybar takes JSON, Polybar takes text with color tags."""
    visible = count > 0
    if bar == "waybar":
        payload = {"text": GLYPH if visible else "", "class": state if visible else "hidden", "tooltip": tooltip}
        return json.dumps(payload, ensure_ascii=False)
    return f"%{{F{COLORS[state]}}}{GLYPH}%{{F-}}" if visible else ""
