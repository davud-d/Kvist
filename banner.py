"""Live prompt banner rendered through prompt_toolkit formatted text."""

from __future__ import annotations

import os
import sys
import textwrap
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.application.current import get_app
from prompt_toolkit.formatted_text import AnyFormattedText, to_formatted_text
from rich.console import Console

from banner_frames import CURSOR_GLYPH, Frame, Rgb, frame_at
from llm_clients import provider_label
from redaction import sanitize_display_text
from user_config import get_saved_role


BANNER_TEXT = "Kvist"

# Outer banner geometry.
HEIGHT = 15
LEFT_WIDTH = 50
LEFT_GAP = 4
RIGHT_GAP = 4
MAX_WIDTH = 112
# Upper bound for the pinned banner once the live panel is shown; wide enough
# to fill the top strip, capped so the thinking column stays readable.
LIVE_MAX_WIDTH = 200

# Rounded terminal block geometry.
INNER_WIDTH = 46
INNER_HEIGHT = 13
INNER_LEFT = (LEFT_WIDTH - INNER_WIDTH) // 2
INNER_TOP = (HEIGHT - INNER_HEIGHT) // 2

# Bitmap glyph geometry. Two pixel rows share one upper-half-block cell.
GLYPH_WIDTH = 5
GLYPH_HEIGHT = 7
GLYPH_CELL_ROWS = 4
SLOT_WIDTH = 6

BLUE_BACKGROUND = "#17365f"
BLUE_BORDER = "#78a9ff"
BLUE_VALUE = "#9fc1ff"
OUTER_BORDER = "#667085"
DIVIDER = "#596579"
PRIMARY = "#e5e7eb"
MUTED = "#9ca3af"

GRID_ROWS = HEIGHT + 2
FIXED_WIDTH = 2 + LEFT_WIDTH + LEFT_GAP + 1 + RIGHT_GAP
INFO_LABEL_WIDTH = 10
INFO_DECORATION_WIDTH = 2
MIN_VALUE_ROOM = 12
INFO_ROWS = (3, 5, 7, 9, 11, 13)
PROMPT_ROW = 6
INFO_COLUMN = 60

# Live telemetry panel geometry (right of the fixed info column). It is only
# drawn when a snapshot is supplied AND the banner is at least LIVE_MIN_WIDTH
# wide; below that the banner keeps its info-only layout unchanged.
INFO_VALUE_ROOM = 22
LIVE_DIVIDER_COLUMN = 96
LIVE_COLUMN = 99
LIVE_METRICS_WIDTH = 26
LIVE_GAP = 2
LIVE_TOP = 3
LIVE_HEIGHT = 10
LIVE_MIN_WIDTH = 144
STATE_ACCENT = "#3fb950"
THINK_BAR = "#4b5563"

# Fills the otherwise-empty thinking column while no run is active, so the idle
# banner does not leave a large blank zone.
IDLE_HINT = (
    "Kvist is ready — type a task, or /commands for the full list. "
    "Alt+Enter starts a multiline prompt, PgUp / PgDn scroll the transcript, "
    "Ctrl+C interrupts the current run."
)
# Shown in the thinking column while running when the provider streams no
# reasoning deltas, so the space reads as informational rather than broken.
NO_REASONING_NOTE = "This model does not stream its reasoning."

Bitmap = tuple[str, ...]
Cell = tuple[str, str, str]


# A glyph is GLYPH_HEIGHT strings of GLYPH_WIDTH chars, each '#' (foreground)
# or '.' (background).
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

    # Lowercase.
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


console = Console()


def enable_windows_vt() -> bool:
    """Turn on ENABLE_VIRTUAL_TERMINAL_PROCESSING; return success."""
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


def _rgb_hex(rgb: Rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _truncate_value(value: str, room: int) -> str:
    if len(value) <= room:
        return value
    return "…" + value[-(room - 1):]


def _truncate_head(value: str, room: int) -> str:
    """Truncate keeping the head, so a leading metric label survives."""
    if len(value) <= room:
        return value
    if room < 1:
        return ""
    return value[: room - 1] + "…"


def _banner_width(fields: list[tuple[str, str]], console_width: int) -> int:
    longest_value = max((len(value) for _, value in fields), default=0)
    requested = FIXED_WIDTH + INFO_LABEL_WIDTH + longest_value + INFO_DECORATION_WIDTH
    upper_bound = min(MAX_WIDTH, console_width - 2)
    return min(max(requested, 85), upper_bound)


def build_grid(
    frame: Frame,
    *,
    width: int,
    fields: list[tuple[str, str]],
    live: LivePanelState | None = None,
) -> list[list[Cell]] | None:
    """Build one complete 17-row banner frame."""
    right_width = width - FIXED_WIDTH
    value_room = right_width - INFO_LABEL_WIDTH - INFO_DECORATION_WIDTH
    num_slots = 2 + len(BANNER_TEXT)
    prompt_width = num_slots * SLOT_WIDTH

    if value_room < MIN_VALUE_ROOM or prompt_width > INNER_WIDTH - 2:
        return None
    if any(slot < 0 or slot >= num_slots for slot in frame):
        return None

    show_live = live is not None and width >= LIVE_MIN_WIDTH
    info_room = INFO_VALUE_ROOM if show_live else value_room

    grid: list[list[Cell]] = [
        [(" ", "", "") for _ in range(width)]
        for _ in range(GRID_ROWS)
    ]

    # Outer frame and the divider between the fixed left block and info panel.
    grid[0][0] = ("╭", OUTER_BORDER, "")
    grid[0][width - 1] = ("╮", OUTER_BORDER, "")
    grid[GRID_ROWS - 1][0] = ("╰", OUTER_BORDER, "")
    grid[GRID_ROWS - 1][width - 1] = ("╯", OUTER_BORDER, "")
    for column in range(1, width - 1):
        grid[0][column] = ("─", OUTER_BORDER, "")
        grid[GRID_ROWS - 1][column] = ("─", OUTER_BORDER, "")
    for row in range(1, GRID_ROWS - 1):
        grid[row][0] = ("│", OUTER_BORDER, "")
        grid[row][width - 1] = ("│", OUTER_BORDER, "")
        grid[row][55] = ("│", DIVIDER, "")

    # Fixed-width rounded blue block.
    grid[2][3] = ("╭", BLUE_BORDER, "")
    grid[2][48] = ("╮", BLUE_BORDER, "")
    grid[14][3] = ("╰", BLUE_BORDER, "")
    grid[14][48] = ("╯", BLUE_BORDER, "")
    for column in range(4, 48):
        grid[2][column] = ("─", BLUE_BORDER, "")
        grid[14][column] = ("─", BLUE_BORDER, "")
    for row in range(3, 14):
        grid[row][3] = ("│", BLUE_BORDER, "")
        grid[row][48] = ("│", BLUE_BORDER, "")
        for column in range(4, 48):
            grid[row][column] = (" ", "", BLUE_BACKGROUND)

    # The prompt uses a stable origin based on the full composition, not on
    # sparse slots present in this particular animation frame.
    prompt_column = 4 + (INNER_WIDTH - 2 - prompt_width) // 2
    for slot, (glyph, rgb) in frame.items():
        try:
            bitmap = FONT[glyph]
        except KeyError:
            raise KeyError(f"build_grid: no bitmap for glyph {glyph!r}")

        color = _rgb_hex(rgb)
        slot_column = prompt_column + slot * SLOT_WIDTH
        for cell_row in range(GLYPH_CELL_ROWS):
            top = 2 * cell_row
            bottom = top + 1
            for column in range(GLYPH_WIDTH):
                top_on = top < GLYPH_HEIGHT and bitmap[top][column] == "#"
                bottom_on = (
                    bottom < GLYPH_HEIGHT and bitmap[bottom][column] == "#"
                )
                foreground = color if top_on else BLUE_BACKGROUND
                background = color if bottom_on else BLUE_BACKGROUND
                grid[PROMPT_ROW + cell_row][slot_column + column] = (
                    "▀",
                    foreground,
                    background,
                )
            for gap in range(GLYPH_WIDTH, SLOT_WIDTH):
                grid[PROMPT_ROW + cell_row][slot_column + gap] = (
                    "▀",
                    BLUE_BACKGROUND,
                    BLUE_BACKGROUND,
                )

    # Info panel. Writing into a fixed grid keeps the right border anchored
    # even when a value fills every available column.
    for row, (label, value) in zip(INFO_ROWS, fields):
        label_text = f"{label:<{INFO_LABEL_WIDTH}}"[:INFO_LABEL_WIDTH]
        truncated = _truncate_value(str(value), info_room)
        for offset, character in enumerate(label_text):
            grid[row][INFO_COLUMN + offset] = (character, PRIMARY, "")

        value_column = INFO_COLUMN + INFO_LABEL_WIDTH
        grid[row][value_column] = ("[", MUTED, "")
        for offset, character in enumerate(truncated, start=1):
            grid[row][value_column + offset] = (character, BLUE_VALUE, "")
        grid[row][value_column + len(truncated) + 1] = ("]", MUTED, "")

    if show_live:
        assert live is not None
        _draw_live_panel(grid, live, width)

    return grid


def _write_cells(
    grid: list[list[Cell]],
    row: int,
    column: int,
    text: str,
    foreground: str,
    *,
    width: int,
) -> None:
    if not 0 <= row < GRID_ROWS:
        return
    for offset, character in enumerate(text):
        target = column + offset
        if 0 <= target < width - 1:
            grid[row][target] = (character, foreground, "")


def _draw_live_panel(
    grid: list[list[Cell]],
    live: LivePanelState,
    width: int,
) -> None:
    """Render the two-column metrics|thinking panel into the banner grid."""
    for row in range(1, GRID_ROWS - 1):
        grid[row][LIVE_DIVIDER_COLUMN] = ("│", DIVIDER, "")

    fields = live_panel_fields(live)
    label_room = max((len(label) for label, _ in fields), default=0)
    value_column = LIVE_COLUMN + label_room + 2
    value_room = LIVE_METRICS_WIDTH - label_room - 2

    for index, (label, value) in enumerate(fields):
        row = LIVE_TOP + index
        _write_cells(grid, row, LIVE_COLUMN, label, PRIMARY, width=width)
        accent = STATE_ACCENT if (label == "State" and live.running) else BLUE_VALUE
        _write_cells(
            grid,
            row,
            value_column,
            _truncate_head(str(value), value_room),
            accent,
            width=width,
        )

    think_column = LIVE_COLUMN + LIVE_METRICS_WIDTH + LIVE_GAP
    think_room = (width - 1) - think_column - 2
    for index, line in enumerate(
        wrap_thinking(_thinking_source(live), think_room, LIVE_HEIGHT)
    ):
        row = LIVE_TOP + index
        _write_cells(grid, row, think_column, "▏", THINK_BAR, width=width)
        _write_cells(grid, row, think_column + 2, line, MUTED, width=width)


def _cell_style(foreground: str, background: str) -> str:
    parts: list[str] = []
    if foreground == BLUE_VALUE:
        parts.append("bold")
    if foreground:
        parts.append(f"fg:{foreground}")
    if background:
        parts.append(f"bg:{background}")
    return " ".join(parts)


def grid_to_fragments(grid: list[list[Cell]]) -> list[tuple[str, str]]:
    """Collapse adjacent cells with the same colors into formatted text."""
    fragments: list[tuple[str, str]] = []

    for row_index, row in enumerate(grid):
        current_style: str | None = None
        current_text: list[str] = []
        for character, foreground, background in row:
            style = _cell_style(foreground, background)
            if current_style is not None and style != current_style:
                fragments.append((current_style, "".join(current_text)))
                current_text = []
            current_style = style
            current_text.append(character)

        if current_style is not None:
            fragments.append((current_style, "".join(current_text)))
        if row_index < len(grid) - 1:
            fragments.append(("", "\n"))

    return fragments


def banner_available(console_width: int, console_height: int) -> bool:
    """True when the terminal can host the live banner."""
    if os.environ.get("KVIST_NO_BANNER"):
        return False
    if not sys.stdout.isatty():
        return False

    term = os.environ.get("TERM", "")
    if term.startswith("linux"):
        return False
    if term == "dumb" or (not term and sys.platform != "win32"):
        return False
    if os.environ.get("COLORTERM", "") not in ("truecolor", "24bit"):
        return False
    if sys.platform == "win32" and not enable_windows_vt():
        return False
    if console_height < 31:
        return False

    width = min(MAX_WIDTH, console_width - 2)
    value_room = width - FIXED_WIDTH - INFO_LABEL_WIDTH - INFO_DECORATION_WIDTH
    return value_room >= MIN_VALUE_ROOM


def _banner_fields(config) -> list[tuple[str, str]]:
    return [
        ("Provider:", sanitize_display_text(provider_label(config.provider))),
        ("Fast:", sanitize_display_text(str(config.fast_model))),
        ("Smart:", sanitize_display_text(str(config.smart_model))),
        ("Planner:", sanitize_display_text(str(config.planner_model))),
        ("Role:", sanitize_display_text(get_saved_role() or "auto")),
        ("Commands:", "/commands"),
    ]


@dataclass(slots=True)
class LivePanelState:
    """Immutable snapshot of run telemetry rendered in the banner's right area.

    A plain snapshot is passed to the UI thread each frame so the renderer never
    reads the live ``RunState`` that the main thread mutates.
    """

    running: bool = False
    finished: bool = False
    state_label: str = "not started"
    action: str = ""
    step: int = 0
    progress: int = 0
    plan_step: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    files_changed: int = 0
    elapsed_seconds: float = 0.0
    tokens_per_second: float = 0.0
    model_tier: str = ""
    escalated: bool = False
    thinking: str = ""


def _format_tokens(count: int) -> str:
    if count < 1000:
        return str(count)
    return f"{count / 1000:.1f}k"


def _format_elapsed(seconds: float) -> str:
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    return f"{total // 60}m {total % 60:02d}s"


def _format_speed(tokens_per_second: float) -> str:
    return f"{tokens_per_second:.0f} tok/s"


def live_panel_fields(state: LivePanelState) -> list[tuple[str, str]]:
    """Left metrics column of the live panel as ``(label, value)`` rows.

    Cost is deliberately absent: providers never return a price, a local table
    would rot, so the freed rows hold Speed (tok/s) and the active model tier.

    Three phases: before the first run it is a zeroed "not started" scaffold;
    while running it is live; once a run finishes the accumulated totals stay on
    screen (State "done") instead of snapping back to zero.
    """
    if not state.running and not state.finished:
        return [
            ("State", "not started"),
            ("Action", "—"),
            ("Step", "—"),
            ("Tokens", "0"),
            ("Files", "0"),
            ("Elapsed", "—"),
            ("Speed", "—"),
            ("Model", "—"),
        ]

    total = state.tokens_in + state.tokens_out
    tier = state.model_tier or "—"
    if state.escalated:
        tier = f"{tier} ↑"

    return [
        ("State", state.state_label),
        ("Action", state.action or "—"),
        ("Step", f"{state.step} · {state.plan_step} · {state.progress}%"),
        ("Tokens", f"Σ {_format_tokens(total)}"),
        ("Files", f"{state.files_changed} edited"),
        ("Elapsed", _format_elapsed(state.elapsed_seconds)),
        ("Speed", _format_speed(state.tokens_per_second)),
        ("Model", tier),
    ]


def _thinking_source(state: LivePanelState) -> str:
    """Text for the thinking column: reasoning, a no-reasoning note, or hints."""
    if state.running:
        return state.thinking or NO_REASONING_NOTE
    return IDLE_HINT


def wrap_thinking(text: str, width: int, height: int) -> list[str]:
    """Word-wrap the reasoning stream and keep only its last ``height`` lines.

    The tail window is what keeps a long chain of thought from growing the
    fixed-height banner: old lines scroll out of the window, its size never
    changes.
    """
    if width < 1 or height < 1:
        return []
    cleaned = " ".join(sanitize_display_text(text).split())
    if not cleaned:
        return []
    lines = textwrap.wrap(cleaned, width=width)
    return lines[-height:]


def render_live_panel(
    state: LivePanelState,
    *,
    metrics_width: int,
    thinking_width: int,
    height: int,
) -> list[str]:
    """Render the two-column live panel (metrics | thinking) as plain rows.

    This pure text form is what the unit tests and a dry-run preview exercise;
    the grid renderer colorizes the same layout.
    """
    fields = live_panel_fields(state)[:height]
    label_room = max((len(label) for label, _ in fields), default=0)

    think_lines = wrap_thinking(_thinking_source(state), thinking_width, height)

    rows: list[str] = []
    for index in range(height):
        if index < len(fields):
            label, value = fields[index]
            left = f"{label:<{label_room}}  {value}"
        else:
            left = ""
        left = _truncate_head(left, metrics_width).ljust(metrics_width)

        think = think_lines[index] if index < len(think_lines) else ""
        prefix = "🤔 " if index == 0 and think else "   "
        right = (prefix + think) if think else ""
        rows.append(f"{left}  {right}".rstrip())

    return rows


def _live_banner_width(
    fields: list[tuple[str, str]],
    console_width: int,
    live: LivePanelState | None,
) -> int:
    """Widen the banner to host the live panel, or fall back to info-only."""
    if live is None:
        return _banner_width(fields, console_width)
    wide = min(LIVE_MAX_WIDTH, console_width - 2)
    if wide < LIVE_MIN_WIDTH:
        return _banner_width(fields, console_width)
    return wide


def make_banner_content(
    get_config: Callable[[], object],
    get_width: Callable[[], int],
    get_live: Callable[[], LivePanelState | None] | None = None,
) -> Callable[[], AnyFormattedText]:
    """Build a header-only callable for a persistent prompt_toolkit layout."""
    start: float | None = None
    cached_key: tuple[Any, ...] | None = None
    cached_result: AnyFormattedText | None = None

    def content() -> AnyFormattedText:
        nonlocal start, cached_key, cached_result

        now = time.monotonic()
        if start is None:
            start = now

        frame = frame_at(now - start, BANNER_TEXT)
        fields = _banner_fields(get_config())
        current_width = get_width()
        live = get_live() if get_live is not None else None
        key = (frame, tuple(fields), current_width, live)
        if cached_key == key and cached_result is not None:
            return cached_result

        width = _live_banner_width(fields, current_width, live)
        grid = build_grid(frame, width=width, fields=fields, live=live)
        result: AnyFormattedText = [] if grid is None else grid_to_fragments(grid)

        cached_key = key
        cached_result = result
        return result

    return content


def make_banner_message(
    config,
    base_prompt,
) -> Callable[[], AnyFormattedText]:
    """Build the callable prompt_toolkit calls on every refresh."""
    start: float | None = None
    cached_key: tuple[Frame, tuple[tuple[str, str], ...], int] | None = None
    cached_result: AnyFormattedText | None = None

    def message() -> AnyFormattedText:
        nonlocal start, cached_key, cached_result

        # Final render: drop the banner so only the entered line stays in the
        # scrollback. prompt_toolkit hides its own rprompt the same way.
        if get_app().is_done:
            return base_prompt

        now = time.monotonic()
        if start is None:
            start = now

        frame = frame_at(now - start, BANNER_TEXT)
        fields = _banner_fields(config)
        current_width = console.width
        key = (frame, tuple(fields), current_width)
        if cached_key == key and cached_result is not None:
            return cached_result

        width = _banner_width(fields, current_width)
        grid = build_grid(frame, width=width, fields=fields)
        if grid is None:
            result: AnyFormattedText = base_prompt
        else:
            result = grid_to_fragments(grid) + list(to_formatted_text(base_prompt))

        cached_key = key
        cached_result = result
        return result

    return message
