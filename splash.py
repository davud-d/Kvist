# -*- coding: utf-8 -*-
"""Splash screen for a second, prompt_toolkit-based Kvist terminal frontend.

This is a feasibility probe, not a shipped product. It answers one question
before weeks are spent on it: can prompt_toolkit carry the opencode splash
layout with native containers, or does it need hand-rolled geometry? The
answer, worked out while building this, is in ``SPLASH_REPORT`` at the bottom.

Every colour comes from ``theme.py``; there is not one hard-coded hex here. Run
it standalone::

    python splash.py --workspace <path> [--model M] [--provider P]
                     [--theme kvist] [--mode dark]

Nothing imports this module from the existing frontend and ``main.py`` is
untouched.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Size
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, WindowAlign
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Container,
    HSplit,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import AfterInput, ConditionalProcessor
from prompt_toolkit.styles import Style

from theme import Mode, Theme, ThemeError, load_theme

LOGO_TEXT = "Kvist"

# Left half-block. opencode draws the input frame's edge as a solid rule; the
# left-quarter block read as a stray dash against Kvist's darker screen.
ACCENT_BAR_CHAR = "▌"

# ── geometry thresholds ──────────────────────────────────────────────────
#
# When height runs out, elements are dropped in this order, decoration first
# and the reason-the-screen-exists last:
#
#     gap  →  hints  →  block logo  →  text logo
#
# so the height thresholds below are strictly decreasing in that same order.
#
#     pad  →  gap  →  hints  →  block logo  →  text logo
#
# where "pad" covers the input block's own blank rows and the blank row that
# separates the block from the hints line.
PAD_MIN_HEIGHT = 17
GAP_MIN_HEIGHT = 15
HINTS_MIN_HEIGHT = 12
LOGO_MIN_HEIGHT = 9
TEXT_LOGO_MIN_HEIGHT = 5

# The block glyphs for "Kvist" are 29 columns wide; the rest is breathing room.
LOGO_MIN_WIDTH = 46

# The input block: a one-column accent bar, then a padded background slab. The
# accent bar sits at column c, the slab spans c+1 onward, the text starts at
# c+BLOCK_TEXT_INDENT. The same inner pad is mirrored on the right so a long
# model name never touches the slab's edge. Measured against opencode: bar at
# 23, text at 26.
ACCENT_BAR_WIDTH = 1
BLOCK_INNER_PAD = 2
BLOCK_TEXT_INDENT = ACCENT_BAR_WIDTH + BLOCK_INNER_PAD

# banner.FONT glyphs are 7 px tall, two px per half-block cell -> 4 rows. Kept as
# a literal so the layout never imports banner (and the OpenAI SDK behind it)
# just to size a Window that a narrow terminal will not even show.
BLOCK_LOGO_HEIGHT = 4

# The centred column is capped so long lines do not span a wide terminal, and
# floored so a narrow one still shows a usable frame. 75 is the opencode block
# width (columns 23..97 on a 120-wide grid); it stays a hard constant, not a
# fraction of the screen — there is no measurement to justify a fraction.
COLUMN_WIDTH = 75
MIN_COLUMN_WIDTH = 20

_ORDERED_HEIGHT_THRESHOLDS = (
    PAD_MIN_HEIGHT,
    GAP_MIN_HEIGHT,
    HINTS_MIN_HEIGHT,
    LOGO_MIN_HEIGHT,
    TEXT_LOGO_MIN_HEIGHT,
)
if list(_ORDERED_HEIGHT_THRESHOLDS) != sorted(_ORDERED_HEIGHT_THRESHOLDS, reverse=True):
    raise RuntimeError(
        "height thresholds must decrease as pad > gap > hints > block logo > text logo"
    )


# ── colours: style classes mapped to theme slots, never to literals ──────

# fg slot, then optional bg slot. Resolved through Theme.prompt_toolkit_color,
# so a "none" slot becomes "ansidefault" and the terminal default shows through.
_STYLE_SLOTS: dict[str, tuple[str, ...]] = {
    "splash.body": ("text", "background"),
    "splash.logo": ("primary",),
    "splash.accent": ("accent",),
    "splash.block": ("text", "backgroundPanel"),
    "splash.input": ("text",),
    "splash.placeholder": ("textMuted",),
    "splash.meta": ("textMuted",),
    "splash.meta.mode": ("accent",),
    "splash.meta.model": ("text",),
    "splash.hint": ("textMuted",),
    "splash.hint.key": ("text",),
    "splash.status": ("textMuted",),
}


def build_style(theme: Theme, mode: Mode) -> Style:
    """Turn the class→slot map into a prompt_toolkit ``Style`` for one mode."""
    rules: dict[str, str] = {}
    for klass, slots in _STYLE_SLOTS.items():
        fg = theme.prompt_toolkit_color(slots[0], mode)
        parts = [fg]
        if len(slots) > 1:
            parts.append(f"bg:{theme.prompt_toolkit_color(slots[1], mode)}")
        rules[klass] = " ".join(parts)
    return Style.from_dict(rules)


# ── logo ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=4)
def logo_lines(text: str) -> tuple[str, ...]:
    """Render ``text`` as four rows of half-block glyphs from ``banner.FONT``.

    The import is deferred: ``banner`` transitively pulls the OpenAI SDK
    (``banner.py`` -> ``llm_clients`` -> ``openai``), which the splash otherwise
    never needs. A narrow terminal that shows the plain text logo never pays it.
    """
    from banner import FONT, GLYPH_HEIGHT, GLYPH_WIDTH

    rows: list[str] = []
    for cell_row in range((GLYPH_HEIGHT + 1) // 2):
        top = 2 * cell_row
        bottom = top + 1
        line: list[str] = []
        for char in text:
            bitmap = FONT.get(char) or FONT[" "]
            for column in range(GLYPH_WIDTH):
                top_on = top < GLYPH_HEIGHT and bitmap[top][column] == "#"
                bot_on = bottom < GLYPH_HEIGHT and bitmap[bottom][column] == "#"
                line.append("█" if top_on and bot_on else "▀" if top_on else "▄" if bot_on else " ")
            line.append(" ")
        rows.append("".join(line).rstrip())
    return tuple(rows)


def _logo_fragments() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    lines = logo_lines(LOGO_TEXT)
    for index, line in enumerate(lines):
        out.append(("class:splash.logo", line))
        if index < len(lines) - 1:
            out.append(("", "\n"))
    return out


# ── visibility decisions: one source of truth for layout and tests ──────


def logo_mode(width: int, height: int) -> str:
    """``"block"``, ``"text"`` or ``"none"`` for the given terminal size."""
    if width >= LOGO_MIN_WIDTH and height >= LOGO_MIN_HEIGHT:
        return "block"
    if height >= TEXT_LOGO_MIN_HEIGHT:
        return "text"
    return "none"


def visible_elements(width: int, height: int) -> dict[str, int]:
    """Ordinal visibility level of each optional element (higher = more shown).

    Tests assert this is monotone in height so the degradation ladder can never
    regress: ``logo`` is none<text<block, ``gap``/``hints`` are absent<present.
    """
    return {
        "logo": {"none": 0, "text": 1, "block": 2}[logo_mode(width, height)],
        "hints": int(height >= HINTS_MIN_HEIGHT),
        "gap": int(height >= GAP_MIN_HEIGHT),
        "pad": int(height >= PAD_MIN_HEIGHT),
    }


def _default_size() -> Size:
    return get_app().output.get_size()


def _truncate(text: str, room: int) -> str:
    if room <= 0:
        return ""
    if len(text) <= room:
        return text
    return text[: room - 1] + "…" if room > 1 else "…"


def _truncate_fragments(
    fragments: list[tuple[str, str]], room: int
) -> list[tuple[str, str]]:
    """Trim a fragment list to ``room`` columns, keeping each fragment's style.

    Whole fragments that still fit pass through untouched; the first one that
    overruns is cut with ``_truncate`` (which adds the ellipsis) and the rest
    are dropped.
    """
    out: list[tuple[str, str]] = []
    for style, text in fragments:
        if room <= 0:
            break
        if len(text) <= room:
            out.append((style, text))
            room -= len(text)
            continue
        out.append((style, _truncate(text, room)))
        break
    return out


# ── layout ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Splash:
    container: Container
    style: Style
    buffer: Buffer


def build_splash(
    *,
    theme: Theme,
    mode: Mode,
    workspace: Path | str,
    model: str,
    provider: str,
    version: str,
    get_size=None,
) -> Splash:
    """Assemble the splash. ``get_size`` is the seam that lets tests render it
    without a real terminal; it defaults to the running application's output."""
    size = get_size or _default_size
    buffer = Buffer(name="splash-input")

    def width() -> int:
        return size().columns

    def height() -> int:
        return size().rows

    block_logo = Condition(lambda: logo_mode(width(), height()) == "block")
    text_logo = Condition(lambda: logo_mode(width(), height()) == "text")
    show_gap = Condition(lambda: height() >= GAP_MIN_HEIGHT)
    show_hints = Condition(lambda: height() >= HINTS_MIN_HEIGHT)
    show_pad = Condition(lambda: height() >= PAD_MIN_HEIGHT)

    input_control = BufferControl(
        buffer=buffer,
        input_processors=[
            ConditionalProcessor(
                AfterInput(
                    lambda: [("class:splash.placeholder", "Ask Kvist to build something…")]
                ),
                Condition(lambda: not buffer.text),
            )
        ],
    )

    def meta_fragments() -> list[tuple[str, str]]:
        room = max(
            0, min(COLUMN_WIDTH, width()) - BLOCK_TEXT_INDENT - BLOCK_INNER_PAD
        )
        return _truncate_fragments(
            [
                ("class:splash.meta.mode", "Build"),
                ("class:splash.meta", " · "),
                ("class:splash.meta.model", model),
                ("class:splash.meta", f" · {provider}"),
            ],
            room,
        )

    def hint_fragments() -> list[tuple[str, str]]:
        return [
            ("class:splash.hint.key", "enter"),
            ("class:splash.hint", " send  "),
            ("class:splash.hint.key", "ctrl+c"),
            ("class:splash.hint", " exit  "),
            ("class:splash.hint.key", "/help"),
            ("class:splash.hint", " commands"),
        ]

    def pad_row() -> ConditionalContainer:
        return ConditionalContainer(Window(height=1), filter=show_pad)

    block = VSplit(
        [
            Window(width=BLOCK_INNER_PAD),
            HSplit(
                [
                    pad_row(),
                    Window(input_control, height=1, style="class:splash.input"),
                    pad_row(),
                    Window(FormattedTextControl(meta_fragments), height=1),
                ]
            ),
            Window(width=BLOCK_INNER_PAD),
        ],
        style="class:splash.block",
    )

    frame = VSplit(
        [
            Window(
                char=ACCENT_BAR_CHAR,
                width=ACCENT_BAR_WIDTH,
                style="class:splash.accent",
            ),
            block,
        ],
        height=lambda: 4 if show_pad() else 2,
    )

    column = HSplit(
        [
            ConditionalContainer(
                Window(
                    FormattedTextControl(_logo_fragments),
                    height=BLOCK_LOGO_HEIGHT,
                    align=WindowAlign.CENTER,
                ),
                filter=block_logo,
            ),
            ConditionalContainer(
                Window(
                    FormattedTextControl([("class:splash.logo", LOGO_TEXT)]),
                    height=1,
                    align=WindowAlign.CENTER,
                ),
                filter=text_logo,
            ),
            ConditionalContainer(Window(height=1), filter=show_gap),
            frame,
            ConditionalContainer(Window(height=1), filter=show_pad),
            ConditionalContainer(
                Window(FormattedTextControl(hint_fragments), height=1),
                filter=show_hints,
            ),
        ],
        width=Dimension(min=MIN_COLUMN_WIDTH, preferred=COLUMN_WIDTH, max=COLUMN_WIDTH),
    )

    # Left-biased horizontal centring: when the leftover width is odd, the extra
    # column goes to the left margin. That is what opencode does — bar at 23,
    # right margin 22, on a 120-wide grid.
    def left_margin() -> int:
        return max(0, (width() - COLUMN_WIDTH + 1) // 2)

    body = HSplit(
        [
            Window(),
            VSplit([Window(width=left_margin), column, Window()]),
            Window(),
        ]
    )

    workspace_text = str(workspace)
    version_text = f"v{version}" if not version.startswith("v") else version

    def cwd_fragments() -> list[tuple[str, str]]:
        room = max(0, width() - len(version_text) - 2)
        return [("class:splash.status", _truncate(workspace_text, room))]

    status = VSplit(
        [
            Window(FormattedTextControl(cwd_fragments), height=1),
            Window(
                FormattedTextControl([("class:splash.status", version_text)]),
                width=len(version_text),
                align=WindowAlign.RIGHT,
                height=1,
            ),
        ],
        height=1,
    )

    root = HSplit([body, status], style="class:splash.body")
    return Splash(container=root, style=build_style(theme, mode), buffer=buffer)


# ── standalone entry point ──────────────────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="splash",
        description="Preview the second Kvist frontend's splash screen.",
    )
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--theme", default="kvist")
    parser.add_argument("--mode", default="dark", choices=("dark", "light"))
    parser.add_argument("--version-label", dest="version", default="dev")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        theme = load_theme(args.theme, workspace=args.workspace)
    except ThemeError as error:
        print(error, file=sys.stderr)
        return 1

    splash = build_splash(
        theme=theme,
        mode=args.mode,
        workspace=args.workspace,
        model=args.model,
        provider=args.provider,
        version=args.version,
    )

    bindings = KeyBindings()

    @bindings.add("c-c")
    @bindings.add("c-d")
    @bindings.add("enter")
    def _exit(event) -> None:
        event.app.exit()

    app: Application = Application(
        layout=Layout(splash.container, focused_element=splash.buffer),
        style=splash.style,
        key_bindings=bindings,
        full_screen=True,
        mouse_support=False,
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
