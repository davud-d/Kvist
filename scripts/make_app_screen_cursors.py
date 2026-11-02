# -*- coding: utf-8 -*-
"""Draws the Xcursor theme of the app_screen skill: the browser skill's blue-glow cursor in every common shape.

The browser draws its cursor as a black arrow with a light outline and a blue glow (kernel/skills_bundled/browser/
plugin.py, _GLIDE_JS). Here the same look becomes real X cursors, so the X server, the window manager and the
programs on the agent's screen all use it, not a picture laid over the preview.

Run:  .venv/bin/python scripts/make_app_screen_cursors.py     (needs Pillow; the output is committed)
"""
from __future__ import annotations

import shutil
import struct
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUTPUT = Path(__file__).resolve().parent.parent / "kernel" / "skills_bundled" / "app_screen" / "icons" / "kvist"
SIZES = (32,)
SUPER = 8
CANVAS = 56      # in 32-unit design coordinates: the 32 box plus room for the glow on every side
MARGIN = 12      # where the design's (0, 0) sits on the canvas
LIGHT = (201, 206, 214)   # #c9ced6, the arrow's outline
GLOW = (40, 110, 220)     # rgba(40,110,220), the glow

# A shape is a hotspot and drawing ops in design coordinates: ("poly", points), ("line", points, width),
# ("ring", centre, radius, width), ("round", box, radius). Each op is drawn black over a wider light outline.
ARROW = [("poly", [(3, 3), (27, 12), (13.5, 13.5), (12, 27)])]
SHAPES: dict[str, tuple[tuple[float, float], list]] = {
    "left_ptr": ((3, 3), ARROW),
    "text": ((16, 16), [("line", [(16, 6), (16, 26)], 2.4), ("line", [(11, 6), (21, 6)], 2.4),
                        ("line", [(11, 26), (21, 26)], 2.4)]),
    "crosshair": ((16, 16), [("line", [(4, 16), (28, 16)], 2.4), ("line", [(16, 4), (16, 28)], 2.4)]),
    "pointer": ((13, 4), [("round", (11, 4, 16.5, 17), 2.6), ("round", (7.5, 14, 25, 28), 3.5),
                          ("round", (16, 11, 20.5, 19), 2.2), ("round", (20, 12, 24.5, 20), 2.2)]),
    "watch": ((16, 16), [("ring", (16, 16), 10, 2.6), ("line", [(16, 16), (16, 9)], 2.4),
                         ("line", [(16, 16), (21, 18)], 2.4)]),
    "sb_h_double_arrow": ((16, 16), [("line", [(8, 16), (24, 16)], 2.4), ("poly", [(3, 16), (10, 10), (10, 22)]),
                                     ("poly", [(29, 16), (22, 10), (22, 22)])]),
    "sb_v_double_arrow": ((16, 16), [("line", [(16, 8), (16, 24)], 2.4), ("poly", [(16, 3), (10, 10), (22, 10)]),
                                     ("poly", [(16, 29), (10, 22), (22, 22)])]),
    "fleur": ((16, 16), [("line", [(8, 16), (24, 16)], 2.4), ("line", [(16, 8), (16, 24)], 2.4),
                         ("poly", [(16, 3), (11, 9), (21, 9)]), ("poly", [(16, 29), (11, 23), (21, 23)]),
                         ("poly", [(3, 16), (9, 11), (9, 21)]), ("poly", [(29, 16), (23, 11), (23, 21)])]),
}
# Names programs and toolkits ask for, all answered by one of the shapes above.
ALIASES = {
    "left_ptr": ["default", "arrow", "top_left_arrow", "right_ptr", "context-menu", "help", "question_arrow",
                 "progress", "left_ptr_watch", "dnd-none", "grab", "grabbing", "copy", "alias", "not-allowed",
                 "X_cursor", "no-drop", "crossed_circle"],
    "text": ["xterm", "ibeam", "vertical-text"],
    "crosshair": ["cross", "tcross", "cross_reverse", "diamond_cross", "plus"],
    "pointer": ["hand1", "hand2", "pointing_hand", "openhand", "closedhand"],
    "watch": ["wait"],
    "sb_h_double_arrow": ["h_double_arrow", "ew-resize", "col-resize", "split_h", "left_side", "right_side",
                          "e-resize", "w-resize", "size_hor"],
    "sb_v_double_arrow": ["v_double_arrow", "ns-resize", "row-resize", "split_v", "top_side", "bottom_side",
                          "n-resize", "s-resize", "size_ver"],
    "fleur": ["move", "all-scroll", "size_all", "nw-resize", "ne-resize", "sw-resize", "se-resize",
              "top_left_corner", "top_right_corner", "bottom_left_corner", "bottom_right_corner", "nesw-resize",
              "nwse-resize", "size_bdiag", "size_fdiag"],
}


def _scaled(points, scale: float) -> list[tuple[float, float]]:
    return [((x + MARGIN) * scale, (y + MARGIN) * scale) for x, y in points]


def _draw_ops(draw: ImageDraw.ImageDraw, ops: list, scale: float, fill, grow: float) -> None:
    for op in ops:
        kind = op[0]
        if kind == "poly":
            points = _scaled(op[1], scale)
            draw.polygon(points, fill=fill)
            if grow:
                draw.line([*points, points[0]], fill=fill, width=round(grow * scale), joint="curve")
        elif kind == "line":
            points = _scaled(op[1], scale)
            width = round((op[2] + grow) * scale)
            draw.line(points, fill=fill, width=width, joint="curve")
            for x, y in points:
                draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=fill)
        elif kind == "ring":
            (cx, cy), radius, width = op[1], op[2], op[3] + grow
            x0, y0 = _scaled([(cx - radius, cy - radius)], scale)[0]
            x1, y1 = _scaled([(cx + radius, cy + radius)], scale)[0]
            draw.ellipse((x0, y0, x1, y1), outline=fill, width=round(width * scale))
        elif kind == "round":
            left, top, right, bottom = op[1]
            (x0, y0), (x1, y1) = _scaled([(left - grow / 2, top - grow / 2), (right + grow / 2, bottom + grow / 2)], scale)
            draw.rounded_rectangle((x0, y0, x1, y1), radius=(op[2] + grow / 2) * scale, fill=fill)


def render(name: str, size: int) -> tuple[Image.Image, tuple[int, int]]:
    hotspot, ops = SHAPES[name]
    unit = size / 32
    scale = SUPER * unit
    side = round(CANVAS * unit * SUPER)
    light = Image.new("L", (side, side), 0)
    dark = Image.new("L", (side, side), 0)
    _draw_ops(ImageDraw.Draw(light), ops, scale, 255, 2.2)
    _draw_ops(ImageDraw.Draw(dark), ops, scale, 255, 0)
    out = round(CANVAS * unit)
    light, dark = (layer.resize((out, out), Image.LANCZOS) for layer in (light, dark))
    body = ImageChops.lighter(light, dark)
    # The glow of the browser's CSS: drop-shadow(0 0 5px .95) then drop-shadow(0 0 12px .55); a CSS blur radius is
    # twice the Gaussian's standard deviation.
    near = body.filter(ImageFilter.GaussianBlur(2.5 * unit)).point(lambda v: round(v * 0.95))
    far = body.filter(ImageFilter.GaussianBlur(6 * unit)).point(lambda v: round(v * 0.55))
    glow = ImageChops.lighter(near, far)
    canvas = Image.new("RGBA", (out, out), GLOW + (0,))
    canvas.putalpha(glow)
    outline = Image.new("RGBA", (out, out), LIGHT + (0,))
    outline.putalpha(light)
    canvas = Image.alpha_composite(canvas, outline)
    black = Image.new("RGBA", (out, out), (0, 0, 0, 0))
    black.putalpha(dark)
    canvas = Image.alpha_composite(canvas, black)
    return canvas, (round((hotspot[0] + MARGIN) * unit), round((hotspot[1] + MARGIN) * unit))


def xcursor(images: list[tuple[int, Image.Image, tuple[int, int]]]) -> bytes:
    """The Xcursor file format: a table of contents, then one 32-bit premultiplied ARGB chunk per nominal size."""
    header = 16 + 12 * len(images)
    chunks, table, position = [], [], header
    for nominal, picture, (xhot, yhot) in images:
        width, height = picture.size
        pixels = bytearray()
        for red, green, blue, alpha in picture.getdata():
            pixels += struct.pack("<I", (alpha << 24) | ((red * alpha // 255) << 16) | ((green * alpha // 255) << 8)
                                  | (blue * alpha // 255))
        chunk = struct.pack("<9I", 36, 0xFFFD0002, nominal, 1, width, height, xhot, yhot, 50) + bytes(pixels)
        table.append(struct.pack("<3I", 0xFFFD0002, nominal, position))
        chunks.append(chunk)
        position += len(chunk)
    return b"Xcur" + struct.pack("<3I", 16, 0x10000, len(images)) + b"".join(table) + b"".join(chunks)


def _contact_sheet(path: Path) -> None:
    """The shapes on light and dark, to look at them (the cursor sits on any program's colours)."""
    sheet = Image.new("RGBA", (len(SHAPES) * 90, 180), (236, 238, 242, 255))
    ImageDraw.Draw(sheet).rectangle((0, 90, sheet.width, 180), fill=(24, 26, 32, 255))
    for index, shape in enumerate(SHAPES):
        picture, _ = render(shape, 32)
        for row in (0, 1):
            sheet.alpha_composite(picture, (index * 90 + 17, row * 90 + 17))
    sheet.resize((sheet.width * 2, sheet.height * 2), Image.NEAREST).save(path)


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "cursors").mkdir(parents=True)
    (OUTPUT / "index.theme").write_text("[Icon Theme]\nName=Kvist\nComment=The agent's own pointer\n", encoding="utf-8")
    names = 0
    for shape in SHAPES:
        data = xcursor([(size, *render(shape, size)) for size in SIZES])
        for name in [shape, *ALIASES.get(shape, [])]:
            (OUTPUT / "cursors" / name).write_bytes(data)
            names += 1
    if "--sheet" in sys.argv:
        _contact_sheet(Path(sys.argv[sys.argv.index("--sheet") + 1]))
    print(f"{len(SHAPES)} shapes, {names} names -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
