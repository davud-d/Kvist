# -*- coding: utf-8 -*-
"""The screen's dock: a floating rounded plate at the bottom with one tile per window, drawn here with plain X requests.

JWM can neither round a tray nor draw app tiles, so the dock is its own override-redirect window with its own X
connection (a thread of the skill process, no extra process). Each tile is rendered in Python as raw pixels: the app's
own ``_NET_WM_ICON`` when it has one, otherwise a coloured tile with the first letter of its class. A click raises
the window. Nothing here reads or writes anything but the screen.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import time
from functools import cache
from pathlib import Path
from typing import Any

TILE, GAP, PAD, HEIGHT, MARGIN = 56, 12, 14, 76, 12
PLATE_RADIUS, TILE_RADIUS, ICON_SIZE = 26, 13, 40
PLATE, EDGE, TILE_BACKDROP = (0xC4, 0xD0, 0xE2), (0xE6, 0xEC, 0xF6), (0xF5, 0xF5, 0xF7)
DOT_ACTIVE, DOT_IDLE = (0x2B, 0x2B, 0x30), (0x6C, 0x78, 0x8C)
PALETTE = ((0x3B, 0x82, 0xF6), (0x34, 0xC7, 0x59), (0xFF, 0x9F, 0x0A), (0xFF, 0x45, 0x3A),
           (0xAF, 0x52, 0xDE), (0x30, 0xB0, 0xC7), (0xFF, 0x64, 0x82), (0x8E, 0x8E, 0x93))
POLL_SECONDS = 0.4
_REQUEST_BYTES = 200_000

Color = tuple[int, int, int]


@cache
def _coverage(width: int, height: int, radius: int) -> bytes:
    """How much of each pixel of a rounded rectangle is inside it, 0..255, with soft corners."""
    rows = bytearray()
    for y in range(height):
        for x in range(width):
            dx = max(radius - 0.5 - x, x - (width - radius - 0.5), 0.0)
            dy = max(radius - 0.5 - y, y - (height - radius - 0.5), 0.0)
            distance = (dx * dx + dy * dy) ** 0.5
            rows.append(max(0, min(255, round((radius - distance + 0.5) * 255))))
    return bytes(rows)


def _blend(under: Color, over: Color, alpha: int) -> Color:
    return tuple((under[i] * (255 - alpha) + over[i] * alpha) // 255 for i in range(3))


def _bgrx(color: Color) -> bytes:
    return bytes((color[2], color[1], color[0], 0))


def load_glyphs(theme: str) -> tuple[int, dict[str, bytes]]:
    data = json.loads((Path(theme) / "glyphs.json").read_text(encoding="utf-8"))
    return data["size"], {char: base64.b64decode(blob) for char, blob in data["glyphs"].items()}


def _tile_pixels(foreground: list[Color | None]) -> bytes:
    """A TILE x TILE tile as BGRX over the plate; ``None`` pixels show the tile's own rounded backdrop colour."""
    mask, out = _coverage(TILE, TILE, TILE_RADIUS), bytearray()
    for index, pixel in enumerate(foreground):
        colour = _blend(PLATE, pixel, mask[index]) if pixel is not None else PLATE
        out += _bgrx(colour)
    return bytes(out)


def letter_tile(color: Color, letter: str, glyphs: tuple[int, dict[str, bytes]]) -> bytes:
    size, shapes = glyphs
    alpha = shapes.get(letter.upper())
    left = (TILE - size) // 2
    pixels: list[Color | None] = []
    for y in range(TILE):
        for x in range(TILE):
            covered = alpha[(y - left) * size + (x - left)] if alpha and 0 <= y - left < size and 0 <= x - left < size else 0
            pixels.append(_blend(color, (255, 255, 255), covered))
    return _tile_pixels(pixels)


def icon_tile(icon: tuple[int, int, list[int]]) -> bytes:
    """The app's icon (width, height, ARGB words) scaled to ICON_SIZE on a light backdrop."""
    width, height, words = icon
    scaled = [_sample(words, width, height, x, y) for y in range(ICON_SIZE) for x in range(ICON_SIZE)]
    left = (TILE - ICON_SIZE) // 2
    pixels: list[Color | None] = []
    for y in range(TILE):
        for x in range(TILE):
            inside = 0 <= y - left < ICON_SIZE and 0 <= x - left < ICON_SIZE
            if not inside:
                pixels.append(TILE_BACKDROP)
                continue
            red, green, blue, alpha = scaled[(y - left) * ICON_SIZE + (x - left)]
            pixels.append(_blend(TILE_BACKDROP, (red, green, blue), alpha))
    return _tile_pixels(pixels)


def _sample(words: list[int], width: int, height: int, x: int, y: int) -> tuple[int, int, int, int]:
    """Average of the source block that one pixel of the ICON_SIZE grid covers."""
    x0, x1 = x * width // ICON_SIZE, max(x * width // ICON_SIZE + 1, (x + 1) * width // ICON_SIZE)
    y0, y1 = y * height // ICON_SIZE, max(y * height // ICON_SIZE + 1, (y + 1) * height // ICON_SIZE)
    total = [0, 0, 0, 0]
    count = 0
    for sy in range(y0, y1):
        for sx in range(x0, x1):
            word = words[sy * width + sx]
            total[0] += (word >> 16) & 255
            total[1] += (word >> 8) & 255
            total[2] += word & 255
            total[3] += (word >> 24) & 255
            count += 1
    return tuple(part // count for part in total)  # type: ignore[return-value]


def best_icon(values: Any) -> tuple[int, int, list[int]] | None:
    """The largest icon in a ``_NET_WM_ICON`` value: [width, height, pixels...] repeated."""
    words, best, index = list(values), None, 0
    while index + 2 <= len(words):
        width, height = words[index], words[index + 1]
        end = index + 2 + width * height
        if width <= 0 or height <= 0 or end > len(words):
            break
        if best is None or width * height > best[0] * best[1]:
            best = (width, height, words[index + 2:end])
        index = end
    return best


def render_dock(tiles: list[bytes], running: list[bool]) -> tuple[int, bytes]:
    """The whole plate as BGRX rows: soft rounded body, light edge, one tile per window and a dot under each."""
    width = 2 * PAD + len(tiles) * TILE + max(len(tiles) - 1, 0) * GAP
    inner = _coverage(width - 2, HEIGHT - 2, PLATE_RADIUS - 1)
    rows = [bytearray(_bgrx(PLATE) * width) for _ in range(HEIGHT)]
    for y in range(HEIGHT):
        for x in range(width):
            edge = 255 - (inner[(y - 1) * (width - 2) + (x - 1)] if 0 < x < width - 1 and 0 < y < HEIGHT - 1 else 0)
            if edge:
                rows[y][x * 4:x * 4 + 4] = _bgrx(_blend(PLATE, EDGE, edge))
    top = 8
    for number, tile in enumerate(tiles):
        left = PAD + number * (TILE + GAP)
        for y in range(TILE):
            rows[top + y][left * 4:(left + TILE) * 4] = tile[y * TILE * 4:(y + 1) * TILE * 4]
        _dot(rows, left + TILE // 2, top + TILE + 6, DOT_ACTIVE if running[number] else DOT_IDLE)
    return width, b"".join(rows)


def _dot(rows: list[bytearray], cx: int, cy: int, color: Color) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            alpha = max(0, min(255, round((2.4 - (dx * dx + dy * dy) ** 0.5) * 255)))
            if alpha:
                offset = (cx + dx) * 4
                under = (rows[cy + dy][offset + 2], rows[cy + dy][offset + 1], rows[cy + dy][offset])
                rows[cy + dy][offset:offset + 4] = _bgrx(_blend(under, color, alpha))


def tile_color(wm_class: str) -> Color:
    return PALETTE[sum(wm_class.encode("utf-8")) % len(PALETTE)]


class Dock:
    """Owns the dock window and keeps it in step with the screen's windows until stopped."""

    def __init__(self, display_name: str, theme: str) -> None:
        self._display_name = display_name
        self._glyphs = load_glyphs(theme)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="app-screen-dock", daemon=True)
        self._cache: dict[int, bytes] = {}
        self._frame: tuple[int, bytes] | None = None
        self._shown: tuple[Any, ...] = ()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        from Xlib import X, display
        from Xlib.error import ConnectionClosedError, XError

        try:
            self._connection = display.Display(self._display_name)
            self._loop(X, XError)
        except (ConnectionClosedError, OSError) as error:
            if not self._stop.is_set():
                print(f"the dock stopped: {error}", file=sys.stderr)

    def _loop(self, X: Any, XError: type) -> None:
        d = self._connection
        root = d.screen().root
        window = root.create_window(0, 0, 1, 1, 0, d.screen().root_depth, X.InputOutput, X.CopyFromParent,
                                    override_redirect=1, background_pixel=0xC4D0E2,
                                    event_mask=X.ButtonPressMask | X.ExposureMask)
        gc = window.create_gc()
        clients: list[int] = []
        next_poll = 0.0
        while not self._stop.is_set():
            while d.pending_events():
                event = d.next_event()
                if event.type == X.Expose and self._frame is not None:
                    self._paint(window, gc, X)
                elif event.type == X.ButtonPress and event.detail == 1:
                    self._click(root, clients, event.event_x)
            if time.monotonic() >= next_poll:
                next_poll = time.monotonic() + POLL_SECONDS
                try:
                    clients = self._refresh(window, gc, root, X)
                except XError:  # a window closed between listing it and asking about it; the next poll sees the new list
                    continue
            d.sync()
            time.sleep(0.05)

    def _refresh(self, window: Any, gc: Any, root: Any, X: Any) -> list[int]:
        d = self._connection
        listed = root.get_full_property(d.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        ids = list(listed.value) if listed is not None else []
        active = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
        active_id = active.value[0] if active is not None and len(active.value) else 0
        entries = [self._entry(wid, X) for wid in ids]
        self._cache = {wid: tile for wid, tile in self._cache.items() if wid in ids}
        shown = tuple((wid, minimized, active_id == wid) for wid, _, minimized in entries)
        if shown != self._shown:
            self._shown = shown
            self._layout(window, gc, [tile for _, tile, _ in entries], [wid == active_id for wid in ids], root, X)
        self._raise(window, root)
        return ids

    def _entry(self, wid: int, X: Any) -> tuple[int, bytes, bool]:  # id, tile, minimized
        d = self._connection
        window = d.create_resource_object("window", wid)
        if wid not in self._cache:
            icon = window.get_full_property(d.intern_atom("_NET_WM_ICON"), X.AnyPropertyType)
            parsed = best_icon(icon.value) if icon is not None else None
            wm_class = (window.get_wm_class() or ("", ""))[-1]
            name = window.get_wm_name() or ""
            letter = (wm_class or name or "K")[0]
            self._cache[wid] = icon_tile(parsed) if parsed else letter_tile(tile_color(wm_class or name), letter, self._glyphs)
        state = window.get_full_property(d.intern_atom("WM_STATE"), X.AnyPropertyType)
        minimized = bool(state is not None and state.value[0] == 3)
        return wid, self._cache[wid], minimized

    def _layout(self, window: Any, gc: Any, tiles: list[bytes], active: list[bool], root: Any, X: Any) -> None:
        screen = root.get_geometry()
        if not tiles:
            window.unmap()
            self._frame = None
            return
        width, pixels = render_dock(tiles, active)
        self._frame = (width, pixels)
        window.configure(x=(screen.width - width) // 2, y=screen.height - HEIGHT - MARGIN, width=width, height=HEIGHT)
        self._mask(window, width, X)
        window.map()
        self._paint(window, gc, X)

    def _paint(self, window: Any, gc: Any, X: Any) -> None:
        assert self._frame is not None
        width, pixels = self._frame
        per_chunk = max(1, _REQUEST_BYTES // (width * 4))
        for top in range(0, HEIGHT, per_chunk):
            rows = min(per_chunk, HEIGHT - top)
            window.put_image(gc, 0, top, width, rows, X.ZPixmap, 24, 0, pixels[top * width * 4:(top + rows) * width * 4])

    @staticmethod
    def _mask(window: Any, width: int, X: Any) -> None:
        from Xlib.ext import shape

        mask = window.create_pixmap(width, HEIGHT, 1)
        gc = mask.create_gc(foreground=0)
        mask.fill_rectangle(gc, 0, 0, width, HEIGHT)
        gc.change(foreground=1)
        radius, span = PLATE_RADIUS, PLATE_RADIUS * 2
        for x, y, start in ((0, 0, 90), (width - span - 1, 0, 0), (0, HEIGHT - span - 1, 180),
                            (width - span - 1, HEIGHT - span - 1, 270)):
            mask.fill_arc(gc, x, y, span, span, start * 64, 90 * 64)
        mask.fill_rectangle(gc, radius, 0, width - span, HEIGHT)
        mask.fill_rectangle(gc, 0, radius, width, HEIGHT - span)
        window.shape_mask(shape.SO.Set, shape.SK.Bounding, 0, 0, mask)
        mask.free()

    def _raise(self, window: Any, root: Any) -> None:
        children = root.query_tree().children
        if children and children[-1].id != window.id:
            window.configure(stack_mode=0)  # Above: a window raised after the dock would otherwise cover it

    def _click(self, root: Any, clients: list[int], x: int) -> None:
        from Xlib import X
        from Xlib.protocol import event

        slot, inside = divmod(x - PAD, TILE + GAP)
        if x < PAD or inside >= TILE or slot >= len(clients):
            return
        d = self._connection
        target = d.create_resource_object("window", clients[slot])
        notice = event.ClientMessage(window=target, client_type=d.intern_atom("_NET_ACTIVE_WINDOW"),
                                     data=(32, [1, 0, 0, 0, 0]))
        root.send_event(notice, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
