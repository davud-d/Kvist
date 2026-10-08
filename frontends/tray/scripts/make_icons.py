# -*- coding: utf-8 -*-
"""Generate assets/tray-<state>.png from the app icon plus a status dot (needs Pillow)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT.parent / "gui" / "electron" / "build" / "icon.png"
SIZE = 64
DOTS = {"idle": None, "running": (255, 179, 0, 255), "done": (46, 204, 113, 255)}


def render(dot: tuple[int, int, int, int] | None) -> Image.Image:
    base = Image.open(SOURCE).convert("RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    if dot is None:
        return base
    radius = SIZE // 4
    draw = ImageDraw.Draw(base)
    centre = (SIZE - radius - 1, SIZE - radius - 1)
    draw.ellipse(
        (centre[0] - radius - 3, centre[1] - radius - 3, centre[0] + radius + 3, centre[1] + radius + 3),
        fill=(20, 20, 20, 255),
    )
    draw.ellipse(
        (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius),
        fill=dot,
    )
    return base


def main() -> None:
    (ROOT / "assets").mkdir(exist_ok=True)
    for state, dot in DOTS.items():
        render(dot).save(ROOT / "assets" / f"tray-{state}.png")


if __name__ == "__main__":
    main()
