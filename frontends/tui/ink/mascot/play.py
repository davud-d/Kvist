# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from PIL import Image

FRAMES_DIR = Path(__file__).parent / "frames"
HALF_BLOCK = "▀"  # upper half: foreground = top pixel, background = bottom pixel


def load_frames(columns: int, rows: int) -> list[str]:
    # Two pixel rows per text row, so the image is resized to rows * 2.
    paths = sorted(FRAMES_DIR.glob("frame_*.png"), key=lambda p: int(p.stem.split("_")[1]))
    if not paths:
        raise SystemExit(f"no frame_*.png in {FRAMES_DIR}")
    return [render(Image.open(path).convert("RGB"), columns, rows) for path in paths]


def render(image: Image.Image, columns: int, rows: int) -> str:
    side = min(columns, rows * 2)  # frames are square; keep them square on screen
    image = image.resize((side, side), Image.LANCZOS)
    lines = []
    for y in range(0, side - 1, 2):
        cells = []
        for x in range(side):
            top = image.getpixel((x, y))
            bottom = image.getpixel((x, y + 1))
            cells.append(f"\x1b[38;2;{top[0]};{top[1]};{top[2]}m\x1b[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m{HALF_BLOCK}")
        lines.append("".join(cells) + "\x1b[0m")
    return "\n".join(lines)


def play(frames: list[str], fps: float, loops: int) -> None:
    sys.stdout.write("\x1b[?1049h\x1b[?25l")  # alternate screen, hide cursor
    try:
        delay = 1 / fps
        played = 0
        while loops == 0 or played < loops:
            for frame in frames:
                sys.stdout.write("\x1b[H" + frame)
                sys.stdout.flush()
                time.sleep(delay)
            played += 1
    except KeyboardInterrupt:
        pass  # Ctrl+C is the normal way to stop an endless loop
    finally:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Play frame_*.png as a terminal animation.")
    parser.add_argument("--fps", type=float, default=2)
    parser.add_argument("--loops", type=int, default=0, help="0 = until Ctrl+C")
    args = parser.parse_args()
    size = shutil.get_terminal_size()
    play(load_frames(size.columns, size.lines - 1), args.fps, args.loops)


if __name__ == "__main__":
    main()
