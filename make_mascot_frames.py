# -*- coding: utf-8 -*-
"""Turn the mascot animation (PNG frames) into terminal cells for the claude skin.

    python3 make_mascot_frames.py [--source preview/animate] [--rows 8]

Writes ``ui/mascot-frames.ts``: for every frame a grid of cells, each a block
character with a foreground and a background colour, twice: as quadrants (2x2 pixels a
cell, drawn by every font) and as octants (2x4, twice as sharp, needs a font that has
Unicode 16's octants); and ``ui/mascot-images.ts``: the same frames as small
transparent PNGs (base64) for terminals that draw pictures.
Two things are decided here so the UI stays a plain renderer:

* the background of a frame becomes transparent. It is found by flood fill from the
  corners, so an eye that happens to be the background colour stays opaque;
* a cell holds 2x2 or 2x4 pixels, each cell two colours at most. ``--rows`` text rows make
  a square picture ``2 * rows`` columns wide;
* each of those pixels takes the most common colour of the source block it covers, not
  the average: the source is pixel art, and averaging turns dark eyes on a pale face into grey.

Needs Pillow and numpy (dev-time only; the generated file is what ships).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent
# A pixel of a cell has bit index row * 2 + column, the top-left one being bit 0.
# Glyphs by the set of painted pixels, for the cell shapes a terminal can draw with block characters.
QUADRANT_GLYPHS = {
    0b0001: "▘", 0b0010: "▝", 0b0011: "▀", 0b0100: "▖", 0b0101: "▌", 0b0110: "▞", 0b0111: "▛",
    0b1000: "▗", 0b1001: "▚", 0b1010: "▐", 0b1011: "▜", 0b1100: "▄", 0b1101: "▙", 0b1110: "▟", 0b1111: "█",
}
# The octant patterns that Unicode 16 did not give a character of their own because an older
# one already draws them (block elements, quarter blocks, the centre quarters); the four single
# corner pixels (bits 0, 1, 6, 7) have no character at all.
OCTANT_OLDER_GLYPHS = {
    3: "\U0001fb82", 5: "▘", 10: "▝", 15: "▀", 20: "\U0001fbe6", 40: "\U0001fbe7", 63: "\U0001fb85",
    80: "▖", 85: "▌", 90: "▞", 95: "▛", 160: "▗", 165: "▚", 170: "▐", 175: "▜", 192: "▂",
    240: "▄", 245: "▙", 250: "▟", 252: "▆", 255: "█",
}
# A pixel counts as background when it is this close to the frame's corner colour.
BACKGROUND_DISTANCE = 40
# A downsampled pixel is painted from this opacity up.
PAINTED_ALPHA = 96
FLOOD_MARK = 128
# Colours that agree in their top QUANT_BITS bits per channel count as one when picking the most common.
QUANT_BITS = 4
# The picture sent to a terminal that can draw one: this many pixels square, at most this many colours.
IMAGE_PIXELS = 256
IMAGE_COLOURS = 96
# Blocks whose pixels differ less than this from their mean are one flat colour.
FLAT_ERROR = 30.0

Cell = tuple[str, "str | None", "str | None"]


def octant_glyphs() -> dict[int, str]:
    """Every 2x4 pattern that has a character, from the Unicode names ("BLOCK OCTANT-1357" lists the pixels)."""
    glyphs = dict(OCTANT_OLDER_GLYPHS)
    for code in range(0x1CD00, 0x1CDE6):
        pixels = unicodedata.name(chr(code)).rsplit("-", 1)[1]
        glyphs[sum(1 << (int(pixel) - 1) for pixel in pixels)] = chr(code)
    # 256 patterns less the blank one and the four single corner pixels.
    assert len(glyphs) == 251, f"expected 251 octant patterns with a character, got {len(glyphs)}"
    return glyphs


def hex_of(color: np.ndarray) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in color)


def transparent_background(path: Path) -> Image.Image:
    pixels = np.array(Image.open(path).convert("RGBA"))
    like = pixels[..., 3] < 128
    if pixels[0, 0, 3] == 255:
        like |= np.abs(pixels[..., :3].astype(int) - pixels[0, 0, :3].astype(int)).sum(axis=2) < BACKGROUND_DISTANCE
    # .copy(): Pillow's flood fill does nothing, silently, on an image made by fromarray.
    mask = Image.fromarray((like * 255).astype("uint8")).copy()
    width, height = mask.size
    for corner in [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]:
        if mask.getpixel(corner) == 255:
            ImageDraw.floodfill(mask, corner, FLOOD_MARK, thresh=0)
    pixels[..., 3] = np.where(np.array(mask) == FLOOD_MARK, 0, 255)
    return Image.fromarray(pixels)


def shared_box(frames: list[Image.Image]) -> tuple[int, int, int, int]:
    boxes = [frame.getchannel("A").getbbox() for frame in frames]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def nearest_glyph(mask: int, glyphs: dict[int, str]) -> int:
    """The pattern with a character that differs from `mask` in the fewest pixels."""
    return min(glyphs, key=lambda candidate: (bin(candidate ^ mask).count("1"), -bin(candidate).count("1")))


def best_split(pixels: list[np.ndarray], glyphs: dict[int, str]) -> tuple[int, np.ndarray, np.ndarray]:
    """The pattern with a character that leaves the least colour error when the block is two flat colours."""
    everything = (1 << len(pixels)) - 1
    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    for mask in glyphs:
        if mask == everything:
            continue
        inside = [p for i, p in enumerate(pixels) if mask >> i & 1]
        outside = [p for i, p in enumerate(pixels) if not mask >> i & 1]
        near, far = np.mean(inside, axis=0), np.mean(outside, axis=0)
        error = sum(((p - near) ** 2).sum() for p in inside) + sum(((p - far) ** 2).sum() for p in outside)
        if best is None or error < best[0]:
            best = (error, mask, near, far)
    assert best is not None
    return best[1], best[2], best[3]


def cell_of(block: list[np.ndarray], glyphs: dict[int, str]) -> Cell:
    painted = [p[3] >= PAINTED_ALPHA for p in block]
    if not any(painted):
        return (" ", None, None)
    if not all(painted):
        # The rest is the terminal's own background, so the cell has one colour.
        mask = nearest_glyph(sum(1 << i for i, on in enumerate(painted) if on), glyphs)
        return (glyphs[mask], hex_of(np.mean([p[:3] for p, on in zip(block, painted) if on], axis=0)), None)
    colours = [p[:3].astype(float) for p in block]
    mean = np.mean(colours, axis=0)
    if sum(((c - mean) ** 2).sum() for c in colours) < FLAT_ERROR * len(colours):
        return ("█", hex_of(mean), None)
    mask, near, far = best_split(colours, glyphs)
    return (glyphs[mask], hex_of(near), hex_of(far))


def dominant_colours(frame: Image.Image, box: tuple[int, int, int, int], width: int, height: int) -> np.ndarray:
    """The frame cut to `box` and reduced to width x height RGBA pixels, each the most common colour of its block."""
    source = np.array(frame.crop(box))
    reduced = np.zeros((height, width, 4), dtype=np.uint8)
    edges_y = np.linspace(0, source.shape[0], height + 1).astype(int)
    edges_x = np.linspace(0, source.shape[1], width + 1).astype(int)
    for y in range(height):
        for x in range(width):
            block = source[edges_y[y]:edges_y[y + 1], edges_x[x]:edges_x[x + 1]].reshape(-1, 4)
            opaque = block[block[:, 3] > 0]
            if len(opaque) * 2 < len(block):
                continue
            keys = (opaque[:, :3] >> (8 - QUANT_BITS)).astype(int)
            code = (keys[:, 0] << (2 * QUANT_BITS)) | (keys[:, 1] << QUANT_BITS) | keys[:, 2]
            values, counts = np.unique(code, return_counts=True)
            reduced[y, x, :3] = opaque[code == values[counts.argmax()], :3].mean(axis=0).astype(np.uint8)
            reduced[y, x, 3] = 255
    return reduced


def frame_cells(frame: Image.Image, box: tuple[int, int, int, int], rows: int, cell_height: int, glyphs: dict[int, str]) -> list[list[Cell]]:
    """Rows x 2*rows cells of 2 x cell_height pixels each; the picture stays square because a cell is twice as tall as wide."""
    columns = 2 * rows
    small = dominant_colours(frame, box, 2 * columns, cell_height * rows)
    return [
        [cell_of([small[cell_height * r + dy, 2 * c + dx] for dy in range(cell_height) for dx in range(2)], glyphs) for c in range(columns)]
        for r in range(rows)
    ]


def frame_png(frame: Image.Image, box: tuple[int, int, int, int]) -> str:
    """The frame cut to `box` as a small palette PNG with transparency, base64 encoded."""
    small = frame.crop(box).resize((IMAGE_PIXELS, IMAGE_PIXELS), Image.LANCZOS)
    palette = small.quantize(colors=IMAGE_COLOURS, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buffer = io.BytesIO()
    palette.save(buffer, "PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def render_images_module(pictures: list[str], source: str) -> str:
    lines = [
        f"// Generated by make_mascot_frames.py from {source}/frame_*.png. Do not edit by hand.",
        "// The mascot frames as base64 PNGs with a transparent background.",
        "export const MASCOT_PNGS: readonly string[] = [",
        *[f"  {json.dumps(picture)}," for picture in pictures],
        "]",
    ]
    return "\n".join(lines) + "\n"


def render_module(sets: dict[str, list[list[list[Cell]]]], source: str) -> str:
    lines = [
        f"// Generated by make_mascot_frames.py from {source}/frame_*.png. Do not edit by hand.",
        "// A frame is rows of cells; a cell is a block character, its colour and its background",
        "// (null = the terminal's own). Quadrants (2x2 pixels a cell) draw in every font; octants",
        "// (2x4, Unicode 16) are twice as sharp and need a font that has them, such as Cascadia Code 2404+.",
        "export type MascotCell = readonly [char: string, fg: string | null, bg: string | null]",
        "export type MascotFrames = readonly (readonly (readonly MascotCell[])[])[]",
    ]
    for name, frames in sets.items():
        lines += ["", f"export const {name}: MascotFrames = ["]
        for frame in frames:
            lines.append("  [")
            for row in frame:
                lines.append("    [" + ", ".join(json.dumps(list(cell), ensure_ascii=False) for cell in row) + "],")
            lines.append("  ],")
        lines.append("]")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert PNG animation frames to terminal cells.")
    parser.add_argument("--source", default="preview/animate", help="directory with frame_1.png, frame_2.png, …")
    parser.add_argument("--rows", type=int, default=8, help="text rows tall (the picture is twice as many columns wide)")
    parser.add_argument("--output", default="ui/mascot-frames.ts")
    parser.add_argument("--images-output", default="ui/mascot-images.ts")
    args = parser.parse_args()
    source = REPO / args.source
    paths = sorted(source.glob("frame_*.png"), key=lambda p: int(p.stem.split("_")[1]))
    if not paths:
        raise SystemExit(f"no frame_*.png in {source}")
    frames = [transparent_background(path) for path in paths]
    box = shared_box(frames)
    sets = {
        "MASCOT_QUADRANTS": [frame_cells(frame, box, args.rows, 2, QUADRANT_GLYPHS) for frame in frames],
        "MASCOT_OCTANTS": [frame_cells(frame, box, args.rows, 4, octant_glyphs()) for frame in frames],
    }
    (REPO / args.output).write_text(render_module(sets, args.source), encoding="utf-8")
    pictures = [frame_png(frame, box) for frame in frames]
    (REPO / args.images_output).write_text(render_images_module(pictures, args.source), encoding="utf-8")
    print(f"{len(frames)} frames, {args.rows} rows x {2 * args.rows} columns -> {args.output}, {args.images_output}")


if __name__ == "__main__":
    main()
