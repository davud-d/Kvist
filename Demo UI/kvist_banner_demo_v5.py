#!/usr/bin/env python3
"""
Hybrid Kvist banner preview.

Combined design:
- rounded blue terminal block from v2;
- large literal `>` prompt with a vertical bar cursor rendered from a built-in
  5x7 bitmap font through UPPER HALF BLOCK cells, so no terminal-specific text
  sizing protocol is required;
- size, brightness, spacing and information layout from v4.

Each terminal cell is drawn as `▀` (U+2580, UPPER HALF BLOCK): the foreground
colour fills the top pixel, the background colour fills the bottom one. One cell
is therefore two independent, near-square pixels. This is how chafa, timg and
figlet-like tools render bitmap glyphs, so the banner works on Kitty, Alacritty,
WezTerm, foot, Windows Terminal and conhost alike.

Drawing the bitmap requires a terminal that has all three of:
- the `▀` glyph in its font;
- 24-bit (truecolor) SGR colour -- the panel's dark blue does not survive
  reduction to a smaller palette without visible banding;
- cursor save/restore via DECSC/DECRC (`ESC 7` / `ESC 8`), used to reposition
  between pixel rows without assuming where the previous write left the cursor.

Terminals without truecolor fall back to the plain `>_` prompt.

Run:
    python kvist_banner_demo_v5.py

Cool-Retro-Term style typing loop:
    python kvist_banner_demo_v5.py --animate
"""

from __future__ import annotations

import argparse
import contextlib
import enum
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

try:
    import termios
except ImportError:  # Non-POSIX platform.
    termios = None

try:
    from rich.console import Console
    from rich.text import Text
except ImportError as exc:
    raise SystemExit(
        "Rich is not installed.\n"
        "Install it with: python -m pip install rich"
    ) from exc


MODEL = "deepseek-v4-pro"
PROVIDER = "OmniRoute"
ROLE = "auto"
COMMANDS_HINT = "/commands"

# Outer banner geometry from v4.
HEIGHT = 15
LEFT_WIDTH = 50
LEFT_GAP = 4
RIGHT_GAP = 4
MAX_WIDTH = 112

# Rounded terminal block from v2, enlarged.
INNER_WIDTH = 46
INNER_HEIGHT = 13
INNER_LEFT = (LEFT_WIDTH - INNER_WIDTH) // 2
INNER_TOP = (HEIGHT - INNER_HEIGHT) // 2

# Bitmap glyph geometry. Each glyph is GLYPH_WIDTH x GLYPH_HEIGHT pixels; two
# pixel rows share one terminal cell via UPPER HALF BLOCK, so GLYPH_CELL_ROWS
# terminal rows hold 2 * GLYPH_CELL_ROWS pixel rows (7 used, 1 spare).
GLYPH_WIDTH = 5          # pixels
GLYPH_HEIGHT = 7         # pixels
GLYPH_CELL_ROWS = 4      # 4 rows * 2 half-blocks = 8 pixel rows, 7 used
SLOT_WIDTH = 6           # GLYPH_WIDTH + 1 column of spacing

# Vivid palette from v1: deep saturated navy block with bright accents,
# which reads far brighter than the washed-out v4 blues.
BLUE_BACKGROUND = "#17365f"
BLUE_BACKGROUND_RGB = (23, 54, 95)
BLUE_BORDER = "#78a9ff"
BLUE_VALUE = "#9fc1ff"

PROMPT_WHITE = (255, 255, 255)
PROMPT_CYAN = (126, 238, 255)

# Cool-Retro-Term style typing animation.
#
# bloom: a freshly typed glyph flashes white and overheated, then cools down
# through two steps to the settled cyan (TYPE_RAMP).
# burn-in: an erased glyph does not vanish, it decays through a discrete colour
# ramp toward the panel background (DECAY_RAMP); each step roughly halves the
# remaining distance to the background (23, 54, 95), approximating the
# exponential phosphor fade of a CRT.
# cursor: a vertical bar that lights up instantly and fades out through
# DECAY_RAMP, matching the asymmetric phosphor behaviour: an instant rising
# edge, then a decaying afterglow tail instead of a hard on/off blink.
STEADY_TEXT_RGB = (126, 238, 255)
TYPE_RAMP = ((255, 255, 255), (200, 240, 255), STEADY_TEXT_RGB)
TYPE_FRAME_SECONDS = 0.045
TYPE_GAP_SECONDS = 0.05

DECAY_RAMP = ((96, 170, 205), (58, 112, 150), (36, 78, 118))
DECAY_FRAME_SECONDS = 0.05
DECAY_GAP_SECONDS = 0.03

HOLD_SECONDS = 2.5
IDLE_SECONDS = 0.9

CURSOR_MID = (126, 190, 215)
CURSOR_ON_SECONDS = 0.53   # VT100 cursor blink period.
CURSOR_OFF_SECONDS = 0.53

OUTER_BORDER = "#667085"
DIVIDER = "#596579"
PRIMARY = "#e5e7eb"
MUTED = "#9ca3af"

ESC = "\x1b"

# One drawing operation: place ``glyph`` in ``slot`` using foreground ``rgb``.
Rgb = tuple[int, int, int]
Operation = tuple[int, str, Rgb]


# --------------------------------------------------------------------------- #
# Built-in 5x7 bitmap font.
# --------------------------------------------------------------------------- #

# A glyph is GLYPH_HEIGHT strings of GLYPH_WIDTH chars, each '#' (foreground)
# or '.' (background).
Bitmap = tuple[str, ...]

# The cursor is keyed by a non-printable sentinel so it can never collide with
# --text input. A 2-pixel bar flush to the left edge of the slot; the gap to
# the previous letter then matches the inter-letter gap. Change the width here.
CURSOR_GLYPH = "\x00"          # internal key, not a printable character

FONT: dict[str, Bitmap] = {
    " ": (".....",) * GLYPH_HEIGHT,
    CURSOR_GLYPH: ("##...",) * GLYPH_HEIGHT,

    # Uppercase.
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".####", "#....", "#....", "#....", "#....", "#....", ".####"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".####", "#....", "#....", "#.###", "#...#", "#...#", ".####"),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "J": ("####.", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),

    # Lowercase. x-height is 5 rows; `v` and `s` sit in the bottom 5 rows,
    # `t` and `i` rise into the ascender zone.
    "a": (".....", ".....", ".###.", "....#", ".####", "#...#", ".####"),
    "b": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "####."),
    "c": (".....", ".....", ".####", "#....", "#....", "#....", ".####"),
    "d": ("....#", "....#", ".####", "#...#", "#...#", "#...#", ".####"),
    "e": (".....", ".....", ".###.", "#...#", "#####", "#....", ".###."),
    "f": ("..##.", ".#...", "###..", ".#...", ".#...", ".#...", ".#..."),
    "g": (".....", ".....", ".####", "#...#", ".####", "....#", ".###."),
    "h": ("#....", "#....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "i": ("..#..", ".....", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "j": ("...#.", ".....", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "k": ("#....", "#....", "#..#.", "#.#..", "##...", "#.#..", "#..#."),
    "l": (".##..", "..#..", "..#..", "..#..", "..#..", "..#..", "..###"),
    "m": (".....", ".....", "##.#.", "#.#.#", "#.#.#", "#.#.#", "#...#"),
    "n": (".....", ".....", "####.", "#...#", "#...#", "#...#", "#...#"),
    "o": (".....", ".....", ".###.", "#...#", "#...#", "#...#", ".###."),
    "p": (".....", ".....", "####.", "#...#", "####.", "#....", "#...."),
    "q": (".....", ".....", ".####", "#...#", ".####", "....#", "....#"),
    "r": (".....", ".....", "#.##.", "##...", "#....", "#....", "#...."),
    "s": (".....", ".....", ".####", "#....", ".###.", "....#", "####."),
    "t": ("..#..", "..#..", "#####", "..#..", "..#..", "..#..", "...##"),
    "u": (".....", ".....", "#...#", "#...#", "#...#", "#...#", ".####"),
    "v": (".....", ".....", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "w": (".....", ".....", "#...#", "#...#", "#.#.#", "#.#.#", ".#.#."),
    "x": (".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
    "y": (".....", ".....", "#...#", "#...#", ".####", "....#", ".###."),
    "z": (".....", ".....", "#####", "...#.", "..#..", ".#...", "#####"),

    # Digits.
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),

    # Punctuation.
    ">": ("#....", ".#...", "..#..", "...#.", "..#..", ".#...", "#...."),
    "_": (".....", ".....", ".....", ".....", ".....", ".....", "#####"),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
}


def _validate_font() -> None:
    """Fail loudly at import time if any glyph is malformed."""
    for key, bitmap in FONT.items():
        if len(bitmap) != GLYPH_HEIGHT:
            raise ValueError(
                f"FONT[{key!r}] has {len(bitmap)} rows, "
                f"expected {GLYPH_HEIGHT}."
            )
        for row_index, row in enumerate(bitmap):
            if len(row) != GLYPH_WIDTH:
                raise ValueError(
                    f"FONT[{key!r}] row {row_index} has width {len(row)}, "
                    f"expected {GLYPH_WIDTH}."
                )
            bad = set(row) - {"#", "."}
            if bad:
                raise ValueError(
                    f"FONT[{key!r}] row {row_index} has illegal chars "
                    f"{sorted(bad)!r}; only '#' and '.' are allowed."
                )


_validate_font()


# --------------------------------------------------------------------------- #
# Terminal capability detection and colour encoding.
# --------------------------------------------------------------------------- #


class FallbackReason(enum.Enum):
    NONE = "none"
    NOT_A_TTY = "not_a_tty"
    LINUX_CONSOLE = "linux_console"
    DUMB_TERMINAL = "dumb_terminal"
    NO_COLORTERM = "no_colorterm"
    NO_WINDOWS_VT = "no_windows_vt"


def terminal_supports_graphics() -> tuple[bool, FallbackReason]:
    """True when the terminal can render truecolor half-blocks.

    Returns ``(supports, fallback_reason)`` so the caller can explain why the
    plain ``>_`` fallback was used when that is helpful.
    """
    if not sys.stdout.isatty():
        return False, FallbackReason.NOT_A_TTY
    term = os.environ.get("TERM", "")
    if term.startswith("linux"):
        # Linux console: no truecolor, palette limited to 16 colours.
        return False, FallbackReason.LINUX_CONSOLE
    if term == "dumb" or (term == "" and sys.platform != "win32"):
        # conhost leaves TERM unset; only bail on empty TERM elsewhere.
        return False, FallbackReason.DUMB_TERMINAL
    if os.environ.get("COLORTERM", "") not in ("truecolor", "24bit"):
        return False, FallbackReason.NO_COLORTERM
    if sys.platform == "win32" and not enable_windows_vt():
        return False, FallbackReason.NO_WINDOWS_VT
    return True, FallbackReason.NONE


def sgr_fg(rgb: Rgb) -> str:
    red, green, blue = rgb
    return f"{ESC}[38;2;{red};{green};{blue}m"


def sgr_bg(rgb: Rgb) -> str:
    red, green, blue = rgb
    return f"{ESC}[48;2;{red};{green};{blue}m"


def enable_windows_vt() -> bool:
    """Turn on ENABLE_VIRTUAL_TERMINAL_PROCESSING; return success.

    No-op returning True off Windows: there is no console mode to flip.
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        new_mode = mode.value | 0x0004
        if not kernel32.SetConsoleMode(handle, new_mode):
            return False
        return True
    except Exception:
        return False


def append_bracketed(target: Text, value: str) -> None:
    target.append("[", style=MUTED)
    target.append(value, style=f"bold {BLUE_VALUE}")
    target.append("]", style=MUTED)


def build_info_line(
    content_width: int,
    label: str,
    value: str,
    *,
    brackets: bool,
) -> Text:
    line = Text()
    label_text = f"{label:<20}"

    line.append(label_text, style=f"bold {PRIMARY}")

    if brackets:
        append_bracketed(line, value)
    else:
        line.append(value, style=f"bold {BLUE_VALUE}")

    used = len(label_text) + len(value) + (2 if brackets else 0)
    line.append(" " * max(0, content_width - used))
    return line


def build_left_block_row(row: int, *, draw_fallback: bool) -> Text:
    """Build one row of the rounded blue block from v2."""
    result = Text()

    inner_bottom = INNER_TOP + INNER_HEIGHT - 1
    prefix = " " * INNER_LEFT
    suffix = " " * (LEFT_WIDTH - INNER_LEFT - INNER_WIDTH)

    result.append(prefix)

    if row < INNER_TOP or row > inner_bottom:
        result.append(" " * INNER_WIDTH)
        result.append(suffix)
        return result

    if row == INNER_TOP:
        result.append("╭", style=BLUE_BORDER)
        result.append("─" * (INNER_WIDTH - 2), style=BLUE_BORDER)
        result.append("╮", style=BLUE_BORDER)
        result.append(suffix)
        return result

    if row == inner_bottom:
        result.append("╰", style=BLUE_BORDER)
        result.append("─" * (INNER_WIDTH - 2), style=BLUE_BORDER)
        result.append("╯", style=BLUE_BORDER)
        result.append(suffix)
        return result

    result.append("│", style=BLUE_BORDER)

    interior_width = INNER_WIDTH - 2
    fallback_row = INNER_TOP + INNER_HEIGHT // 2

    if draw_fallback and row == fallback_row:
        prompt = ">_"
        left_pad = (interior_width - len(prompt)) // 2
        right_pad = interior_width - left_pad - len(prompt)

        result.append(" " * left_pad, style=f"on {BLUE_BACKGROUND}")
        result.append(prompt, style=f"bold bright_white on {BLUE_BACKGROUND}")
        result.append(" " * right_pad, style=f"on {BLUE_BACKGROUND}")
    else:
        result.append(" " * interior_width, style=f"on {BLUE_BACKGROUND}")

    result.append("│", style=BLUE_BORDER)
    result.append(suffix)
    return result


def build_banner_lines(
    *,
    width: int,
    model: str,
    provider: str,
    role: str,
    commands_hint: str,
    draw_fallback: bool,
) -> list[Text]:
    fixed_width = (
        2
        + LEFT_WIDTH
        + LEFT_GAP
        + 1
        + RIGHT_GAP
    )
    right_width = width - fixed_width

    if right_width < 42:
        raise ValueError("Terminal is too narrow for this preview.")

    info_rows: dict[int, Text] = {
        3: build_info_line(right_width, "Current model:", model, brackets=True),
        5: build_info_line(
            right_width,
            "Current provider:",
            provider,
            brackets=True,
        ),
        7: build_info_line(right_width, "Current role:", role, brackets=True),
        9: build_info_line(
            right_width,
            "All commands:",
            commands_hint,
            brackets=False,
        ),
    }

    lines: list[Text] = []

    top = Text()
    top.append("╭", style=OUTER_BORDER)
    top.append("─" * (width - 2), style=OUTER_BORDER)
    top.append("╮", style=OUTER_BORDER)
    lines.append(top)

    for row in range(HEIGHT):
        line = Text()
        line.append("│", style=OUTER_BORDER)

        line.append_text(
            build_left_block_row(
                row,
                draw_fallback=draw_fallback,
            )
        )

        line.append(" " * LEFT_GAP)
        line.append("│", style=DIVIDER)
        line.append(" " * RIGHT_GAP)

        info = info_rows.get(row)
        if info is None:
            line.append(" " * right_width)
        else:
            line.append_text(info)

        line.append("│", style=OUTER_BORDER)
        lines.append(line)

    bottom = Text()
    bottom.append("╰", style=OUTER_BORDER)
    bottom.append("─" * (width - 2), style=OUTER_BORDER)
    bottom.append("╯", style=OUTER_BORDER)
    lines.append(bottom)

    return lines


def prompt_origin(num_slots: int) -> tuple[int, int]:
    """
    Return the prompt origin for a composition of ``num_slots`` slots as:
    - horizontal cursor movement from column 1;
    - content-row index inside the outer banner.

    The whole composition is centred once, so nothing shifts between frames.
    """
    interior_width = INNER_WIDTH - 2
    prompt_width = num_slots * SLOT_WIDTH

    inner_panel_column = 1 + INNER_LEFT
    inner_content_column = inner_panel_column + 1

    prompt_column = (
        inner_content_column
        + (interior_width - prompt_width) // 2
    )

    interior_height = INNER_HEIGHT - 2
    prompt_height = GLYPH_CELL_ROWS

    inner_content_row = INNER_TOP + 1
    prompt_content_row = (
        inner_content_row
        + (interior_height - prompt_height) // 2
    )

    return prompt_column, prompt_content_row


def render_operation(
    operation: Operation,
    *,
    total_lines: int,
    num_slots: int,
) -> str:
    """Return the ANSI sequence that draws a single slot operation.

    The glyph's bitmap is painted through UPPER HALF BLOCK cells: every cell is
    two pixels, the foreground colour on top and the background colour below.
    Each cell-row of the glyph is positioned independently with DECSC/DECRC so
    the cursor is never assumed to have stayed where the last write left it.
    """
    slot, glyph, rgb = operation

    try:
        bitmap = FONT[glyph]
    except KeyError:
        raise KeyError(f"render_operation: no bitmap for glyph {glyph!r}")

    background = BLUE_BACKGROUND_RGB
    prompt_column, prompt_content_row = prompt_origin(num_slots)
    slot_column = prompt_column + slot * SLOT_WIDTH

    parts: list[str] = []
    for j in range(GLYPH_CELL_ROWS):
        target_row = 1 + prompt_content_row + j
        rows_up = total_lines - target_row

        parts.append(f"{ESC}7")            # DECSC: save cursor + attributes.
        if rows_up > 0:
            parts.append(f"{ESC}[{rows_up}A")
        if slot_column > 0:
            parts.append(f"{ESC}[{slot_column}C")

        for c in range(GLYPH_WIDTH):
            top_row = 2 * j
            bottom_row = 2 * j + 1
            top_on = top_row < GLYPH_HEIGHT and bitmap[top_row][c] == "#"
            bottom_on = (
                bottom_row < GLYPH_HEIGHT and bitmap[bottom_row][c] == "#"
            )
            fg = rgb if top_on else background
            bg = rgb if bottom_on else background
            parts.append(f"{sgr_fg(fg)}{sgr_bg(bg)}▀")

        # The trailing spacing column(s) of the slot: pure panel background,
        # so the inter-letter gap matches the gap before the cursor bar.
        for _ in range(SLOT_WIDTH - GLYPH_WIDTH):
            parts.append(f"{sgr_fg(background)}{sgr_bg(background)}▀")

        parts.append(f"{ESC}[0m")
        parts.append(f"{ESC}8")            # DECRC: restore cursor + attributes.

    return "".join(parts)


def render_frame(
    operations: list[Operation],
    *,
    total_lines: int,
    num_slots: int,
) -> str:
    """Concatenate operations into one frame string (one stdout write)."""
    return "".join(
        render_operation(op, total_lines=total_lines, num_slots=num_slots)
        for op in operations
    )


@dataclass
class Animation:
    """Immutable geometry and timing context for the typing loop."""

    text: str
    total_lines: int
    num_slots: int
    hold_seconds: float
    drain_input: Callable[[], None] | None


def emit_frame(anim: Animation, operations: list[Operation]) -> None:
    """Write one frame in a single stdout write, then drain typed input."""
    sys.stdout.write(
        render_frame(
            operations,
            total_lines=anim.total_lines,
            num_slots=anim.num_slots,
        )
    )
    sys.stdout.flush()
    if anim.drain_input is not None:
        anim.drain_input()


def erase_slot(slot: int) -> Operation:
    """A space glyph whose pixels all fall to the panel background."""
    return (slot, " ", BLUE_BACKGROUND_RGB)


def sleep_until(deadline: float) -> None:
    """Sleep only up to ``deadline`` so timings never accumulate drift."""
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def draw_static_composition(anim: Animation) -> None:
    """Draw the settled `>` word `cursor` frame, no animation."""
    operations: list[Operation] = [(0, ">", PROMPT_WHITE)]
    for index, character in enumerate(anim.text, start=1):
        operations.append((index, character, STEADY_TEXT_RGB))
    operations.append((1 + len(anim.text), CURSOR_GLYPH, CURSOR_MID))
    emit_frame(anim, operations)


def draw_final_frame(anim: Animation) -> None:
    """Clear every slot that can hold a glyph, then draw the static frame."""
    operations: list[Operation] = [
        erase_slot(slot) for slot in range(1, len(anim.text) + 2)
    ]
    operations.append((0, ">", PROMPT_WHITE))
    for index, character in enumerate(anim.text, start=1):
        operations.append((index, character, STEADY_TEXT_RGB))
    operations.append((1 + len(anim.text), CURSOR_GLYPH, CURSOR_MID))
    emit_frame(anim, operations)


def blink_cursor(anim: Animation, *, slot: int, duration: float) -> None:
    """Blink the cursor: instant on, then a phosphor fade through DECAY_RAMP.

    Each cycle is ON -> FADE -> OFF, like a CRT phosphor: an instant rising
    edge, an afterglow tail reusing the letter decay ramp, then darkness.
    Always exits with the cursor drawn in CURSOR_MID so the next stage of
    the state machine starts from a visible cursor.
    """
    end = time.monotonic() + duration
    while True:
        # ON: the phosphor lights up instantly.
        emit_frame(anim, [(slot, CURSOR_GLYPH, CURSOR_MID)])
        on_start = time.monotonic()
        sleep_until(min(on_start + CURSOR_ON_SECONDS, end))
        if time.monotonic() >= end:
            return  # Already ON.

        # FADE: the same afterglow tail as an erased letter.
        fade_start = time.monotonic()
        for frame, color in enumerate(DECAY_RAMP, start=1):
            emit_frame(anim, [(slot, CURSOR_GLYPH, color)])
            sleep_until(min(fade_start + frame * DECAY_FRAME_SECONDS, end))
            if time.monotonic() >= end:
                emit_frame(anim, [(slot, CURSOR_GLYPH, CURSOR_MID)])
                return

        # OFF: fully dark.
        emit_frame(anim, [erase_slot(slot)])
        off_start = time.monotonic()
        sleep_until(min(off_start + CURSOR_OFF_SECONDS, end))
        if time.monotonic() >= end:
            emit_frame(anim, [(slot, CURSOR_GLYPH, CURSOR_MID)])
            return


def boot(anim: Animation) -> None:
    """Draw `>` with an appearance ramp, place the cursor, blink briefly."""
    start = time.monotonic()
    # The `>` settles to white; it reuses the type ramp only for the flash-in.
    appearance = (TYPE_RAMP[0], TYPE_RAMP[1], PROMPT_WHITE)
    for frame, color in enumerate(appearance):
        sleep_until(start + frame * TYPE_FRAME_SECONDS)
        operations: list[Operation] = [(0, ">", color)]
        if frame == 0:
            operations.append((1, CURSOR_GLYPH, CURSOR_MID))
        emit_frame(anim, operations)
    sleep_until(start + len(appearance) * TYPE_FRAME_SECONDS)
    blink_cursor(anim, slot=1, duration=0.6)


def type_letter(anim: Animation, i: int) -> None:
    """Type the ``i``-th letter (bloom flash), advancing the cursor to i+1."""
    letter = anim.text[i - 1]
    start = time.monotonic()

    # Frame 1: erase the cursor at slot i, flash the letter white, and move the
    # cursor forward to slot i+1 -- all in one write.
    emit_frame(
        anim,
        [
            erase_slot(i),
            (i, letter, TYPE_RAMP[0]),
            (i + 1, CURSOR_GLYPH, CURSOR_MID),
        ],
    )

    # Frames 2 and 3: cool the letter down to the settled cyan.
    for frame, color in enumerate((TYPE_RAMP[1], TYPE_RAMP[2]), start=1):
        sleep_until(start + frame * TYPE_FRAME_SECONDS)
        emit_frame(anim, [(i, letter, color)])

    sleep_until(start + 2 * TYPE_FRAME_SECONDS + TYPE_GAP_SECONDS)


def delete_letter(anim: Animation, i: int) -> None:
    """Fade the ``i``-th letter out (burn-in), moving the cursor back to i."""
    letter = anim.text[i - 1]
    start = time.monotonic()

    for frame, color in enumerate(DECAY_RAMP):
        sleep_until(start + frame * DECAY_FRAME_SECONDS)
        emit_frame(anim, [(i, letter, color)])

    # Erase the faded letter and retreat the cursor from slot i+1 to slot i.
    sleep_until(start + len(DECAY_RAMP) * DECAY_FRAME_SECONDS)
    emit_frame(
        anim,
        [
            erase_slot(i + 1),
            erase_slot(i),
            (i, CURSOR_GLYPH, CURSOR_MID),
        ],
    )

    sleep_until(
        start + len(DECAY_RAMP) * DECAY_FRAME_SECONDS + DECAY_GAP_SECONDS
    )


def run_animation(anim: Animation) -> None:
    """Drive the BOOT / TYPE / HOLD / DELETE / IDLE state machine forever."""
    letters = len(anim.text)
    try:
        boot(anim)
        while True:
            for i in range(1, letters + 1):
                type_letter(anim, i)
            blink_cursor(anim, slot=1 + letters, duration=anim.hold_seconds)
            for i in range(letters, 0, -1):
                delete_letter(anim, i)
            blink_cursor(anim, slot=1, duration=IDLE_SECONDS)
    except KeyboardInterrupt:
        draw_final_frame(anim)


@contextlib.contextmanager
def keyboard_silenced():
    """Stop typed keys from disturbing the animated banner.

    Disables echo and canonical (line) input so keystrokes neither appear
    on screen nor push the banner around, while leaving ISIG enabled so
    Ctrl-C still raises KeyboardInterrupt. Pending input is flushed on exit.
    """
    if termios is None or not sys.stdin.isatty():
        yield
        return

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    silenced = termios.tcgetattr(fd)

    # lflags: drop ECHO and ICANON, keep ISIG so Ctrl-C keeps working.
    silenced[3] = (silenced[3] & ~(termios.ECHO | termios.ICANON)) | termios.ISIG
    termios.tcsetattr(fd, termios.TCSANOW, silenced)

    try:
        yield lambda: termios.tcflush(fd, termios.TCIFLUSH)
    finally:
        termios.tcflush(fd, termios.TCIFLUSH)
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview the Kvist banner.")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--provider", default=PROVIDER)
    parser.add_argument("--role", default=ROLE)
    parser.add_argument("--commands", default=COMMANDS_HINT)
    parser.add_argument("--text", default="Kvist")
    parser.add_argument("--hold", type=float, default=HOLD_SECONDS)
    parser.add_argument("--animate", action="store_true")
    parser.add_argument(
        "--color",
        choices=("auto", "truecolor", "off"),
        default="auto",
    )
    return parser.parse_args()


def validate_text(text: str) -> None:
    """Reject text that would break the fixed slot grid."""
    for character in text:
        if character not in FONT:
            raise SystemExit(
                f"--text contains an unsupported character {character!r}; "
                "only A-Z, a-z, 0-9 and the marks > _ - . : (plus space) "
                "have bitmaps."
            )

    num_slots = 2 + len(text)
    needed = num_slots * SLOT_WIDTH
    available = INNER_WIDTH - 2
    if needed > available:
        max_letters = available // SLOT_WIDTH - 2
        raise SystemExit(
            f"--text is too long: {len(text)} letters use {num_slots} slots "
            f"({needed} columns), but only {available} columns "
            f"are available; at most {max_letters} letters fit."
        )


def main() -> None:
    args = parse_args()
    validate_text(args.text)
    console = Console()

    width = min(MAX_WIDTH, console.width - 2)
    minimum_width = (
        2
        + LEFT_WIDTH
        + LEFT_GAP
        + 1
        + RIGHT_GAP
        + 42
    )

    if width < minimum_width:
        raise SystemExit(
            f"Make the terminal at least {minimum_width + 2} columns wide."
        )

    fallback_reason = FallbackReason.NONE
    if args.color == "off":
        draw_fallback = True
    elif args.color == "truecolor":
        draw_fallback = not enable_windows_vt()
    else:
        graphics, fallback_reason = terminal_supports_graphics()
        draw_fallback = not graphics

    lines = build_banner_lines(
        width=width,
        model=args.model,
        provider=args.provider,
        role=args.role,
        commands_hint=args.commands,
        draw_fallback=draw_fallback,
    )

    console.print()

    for line in lines:
        console.print(
            line,
            highlight=False,
            soft_wrap=True,
        )

    if draw_fallback:
        if args.color == "auto":
            fallback_hint = None
            if fallback_reason is FallbackReason.NO_COLORTERM:
                fallback_hint = (
                    "Truecolor not detected. Showing the plain >_ fallback.\n"
                    "Force it with: --color truecolor"
                )
            elif fallback_reason is FallbackReason.LINUX_CONSOLE:
                fallback_hint = (
                    "Linux console has 16 colours and no truecolor. "
                    "Run this in a graphical terminal."
                )
            elif fallback_reason is FallbackReason.NO_WINDOWS_VT:
                fallback_hint = "This console does not support VT sequences."

            if fallback_hint and args.animate:
                fallback_hint += (
                    "\n--animate needs a truecolor terminal and does "
                    "nothing here."
                )

            if fallback_hint:
                console.print(fallback_hint, style="dim")
        return

    anim = Animation(
        text=args.text,
        total_lines=len(lines),
        num_slots=2 + len(args.text),
        hold_seconds=args.hold,
        drain_input=None,
    )

    if not args.animate:
        draw_static_composition(anim)
        return

    sys.stdout.write(f"{ESC}[?25l")
    sys.stdout.flush()

    with keyboard_silenced() as drain_input:
        try:
            run_animation(replace(anim, drain_input=drain_input))
        finally:
            sys.stdout.write(f"{ESC}[?25h")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
