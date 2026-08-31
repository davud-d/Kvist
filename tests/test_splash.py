# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.output import DummyOutput

import splash
import theme

_ROOT = Path(splash.__file__).resolve().parent
_BLOCK = set("█▀▄ ")


# ── headless rendering helper ───────────────────────────────────────────
#
# asyncio.run is not optional: BufferControl.create_content calls
# Buffer.load_history_if_not_yet_loaded() -> app.create_background_task() ->
# get_running_loop(), which raises "no running event loop" outside a loop.


def _fixed_size(width: int, height: int):
    return lambda: Size(rows=height, columns=width)


def _build(width: int, height: int, **overrides) -> splash.Splash:
    params = dict(
        theme=theme.load_theme("kvist"),
        mode="dark",
        workspace="/tmp/kvist_test",
        model="gpt-5.2",
        provider="openai",
        version="dev",
        get_size=_fixed_size(width, height),
    )
    params.update(overrides)
    return splash.build_splash(**params)


async def _draw(built: splash.Splash, width: int, height: int) -> Screen:
    app: Application = Application(
        layout=Layout(built.container, focused_element=built.buffer),
        style=built.style,
        output=DummyOutput(),
        input=DummyInput(),
    )
    screen = Screen(default_char=None, initial_width=width, initial_height=height)
    with set_app(app):
        built.container.write_to_screen(
            screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None
        )
        screen.draw_all_floats()
    return screen


def render(width: int, height: int, **overrides) -> list[str]:
    built = _build(width, height, **overrides)
    screen = asyncio.run(_draw(built, width, height))
    return [
        "".join(screen.data_buffer[y][x].char for x in range(width)).rstrip()
        for y in range(height)
    ]


def _accent_column(lines: list[str]) -> int | None:
    cols = {line.index(splash.ACCENT_BAR_CHAR) for line in lines if splash.ACCENT_BAR_CHAR in line}
    return min(cols) if cols else None


def _draw_attrs(built: splash.Splash, width: int, height: int):
    screen = asyncio.run(_draw(built, width, height))
    style = built.style

    def at(y: int, x: int):
        cell = screen.data_buffer[y][x]
        return cell.char, style.get_attrs_for_style_str(cell.style)

    return at


def render_attrs(width: int, height: int, **overrides):
    """Like ``render`` but yields (char, Attrs) per cell so tests can read the
    resolved fg/bg instead of the class string."""
    built = _build(width, height, **overrides)
    return _draw_attrs(built, width, height)


def _has_block_logo(lines: list[str]) -> bool:
    return any(line.strip() and set(line) <= _BLOCK for line in lines)


def _has_text_logo(lines: list[str]) -> bool:
    return any(line.strip() == "Kvist" for line in lines)


# ── 1. full geometry: every element present ─────────────────────────────


def test_full_layout_has_every_element():
    lines = render(100, 30)
    text = "\n".join(lines)
    assert sum(splash.ACCENT_BAR_CHAR in line for line in lines) == 4  # accent bar spans all block rows
    assert _has_block_logo(lines)
    assert "Build · gpt-5.2 · openai" in text
    assert "/help" in text  # hint line
    assert lines[-1].startswith("/tmp/kvist_test")
    assert lines[-1].rstrip().endswith("vdev")

    bar_rows = [i for i, line in enumerate(lines) if splash.ACCENT_BAR_CHAR in line]
    hint_row = next(i for i, line in enumerate(lines) if "/help" in line)
    assert lines[bar_rows[-1] + 1].strip() == ""  # one blank row below the block
    assert hint_row == bar_rows[-1] + 2


# ── 2. vertical centring at arbitrary height, native (no manual math) ───


def test_block_stays_vertically_centred():
    for height in (18, 20, 24, 31, 40):
        body = render(100, height)[:-1]  # status bar is pinned to the last row
        filled = [i for i, line in enumerate(body) if line.strip()]
        block_mid = (filled[0] + filled[-1]) / 2
        assert abs(block_mid - (len(body) - 1) / 2) <= 1.5, height


# ── 3. horizontal centring of the width-capped column ──────────────────


def test_column_is_horizontally_centred():
    for width in (90, 100, 120):
        lines = render(width, 30)
        assert _accent_column(lines) == (width - splash.COLUMN_WIDTH + 1) // 2, width
        # The logo rows differ in fill (the top row is just K and i's dot), so
        # centre the union box, not any single line.
        logo_rows = [line for line in lines if line.strip() and set(line) <= _BLOCK]
        left = min(len(row) - len(row.lstrip()) for row in logo_rows)
        right = max(len(row.rstrip()) for row in logo_rows)
        logo_mid = (left + right) / 2
        assert abs(logo_mid - width / 2) <= 2, width


# ── 3b. the block logo is shaded: Kvi in secondary, st in primary ──────


def test_block_logo_has_two_shade_classes():
    width, height = 120, 40
    lines = render(width, height)
    at = render_attrs(width, height)

    loaded = theme.load_theme("kvist")
    secondary = loaded.prompt_toolkit_color("secondary", "dark").lstrip("#")
    primary = loaded.prompt_toolkit_color("primary", "dark").lstrip("#")
    assert secondary != primary

    logo_rows = [i for i, line in enumerate(lines) if line.strip() and set(line) <= _BLOCK]
    assert logo_rows

    cells = [
        (x, at(y, x)[1].color)
        for y in logo_rows
        for x in range(width)
        if lines[y][x:x + 1].strip()
    ]
    by_col = sorted(cells)
    assert by_col[0][1] == secondary  # leftmost pixel: the K stem
    assert by_col[-1][1] == primary  # rightmost pixel: the t crossbar
    assert {colour for _, colour in cells} == {secondary, primary}
    # Every shaded pixel sits left of every primary pixel: a clean vertical seam.
    shade_max = max(x for x, colour in cells if colour == secondary)
    primary_min = min(x for x, colour in cells if colour == primary)
    assert shade_max < primary_min


# ── 4. narrow terminal: block logo gives way to plain text ─────────────


def test_narrow_terminal_drops_to_text_logo():
    lines = render(splash.LOGO_MIN_WIDTH - 1, 30)
    assert not _has_block_logo(lines)
    assert _has_text_logo(lines)
    assert "Build · gpt-5.2 · openai" in "\n".join(lines)
    assert _accent_column(lines) is not None


# ── 5. the degradation ladder: each step through its own constant ──────


def test_degradation_ladder_drops_elements_in_order():
    steps = [
        (splash.PAD_MIN_HEIGHT, splash.PAD_MIN_HEIGHT - 1,
         lambda ls: sum(splash.ACCENT_BAR_CHAR in line for line in ls) == 4),
        (splash.GAP_MIN_HEIGHT, splash.GAP_MIN_HEIGHT - 1, _gap_present),
        (splash.HINTS_MIN_HEIGHT, splash.HINTS_MIN_HEIGHT - 1,
         lambda ls: "/help" in "\n".join(ls)),
        (splash.LOGO_MIN_HEIGHT, splash.LOGO_MIN_HEIGHT - 1, _has_block_logo),
        (splash.TEXT_LOGO_MIN_HEIGHT, splash.TEXT_LOGO_MIN_HEIGHT - 1, _has_text_logo),
    ]
    for at, below, present in steps:
        assert present(render(120, at)), at
        assert not present(render(120, below)), below

    # whatever the height, the input frame and the status line hold
    for height in range(4, splash.GAP_MIN_HEIGHT + 2):
        text = "\n".join(render(120, height))
        assert splash.ACCENT_BAR_CHAR in text and "Build · gpt-5.2 · openai" in text, height
        assert "/tmp/kvist_test" in text, height
        assert "too small" not in text.lower(), height


def _gap_present(lines: list[str]) -> bool:
    frame_row = next(i for i, line in enumerate(lines) if splash.ACCENT_BAR_CHAR in line)
    logo_rows = [i for i, line in enumerate(lines[:frame_row]) if line.strip()]
    return bool(logo_rows) and frame_row - logo_rows[-1] - 1 > 0


# ── 6. the ladder is monotone in height (catches drifting constants) ───


def test_visibility_is_monotone_in_height():
    previous = splash.visible_elements(120, 3)
    for height in range(4, splash.PAD_MIN_HEIGHT + 4):
        current = splash.visible_elements(120, height)
        for key in ("logo", "hints", "gap", "pad"):
            assert current[key] >= previous[key], (height, key)
        previous = current


# ── 7. degenerate geometry renders instead of raising ─────────────────


def test_degenerate_geometry_does_not_raise():
    for size in ((20, 3), (5, 4), (1, 1)):
        render(*size)


# ── 8. colours come from the theme, not from the module ───────────────


def test_style_classes_resolve_to_theme_values(tmp_path, monkeypatch):
    loaded = theme.load_theme("kvist")
    for mode in ("dark", "light"):
        rules = dict(splash.build_style(loaded, mode).style_rules)
        for klass, slots in splash._STYLE_SLOTS.items():
            assert loaded.prompt_toolkit_color(slots[0], mode) in rules[klass]
            if len(slots) > 1:
                bg = loaded.prompt_toolkit_color(slots[1], mode)
                assert f"bg:{bg}" in rules[klass]

    home = tmp_path / "home"
    (home / ".kvist" / "themes").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    (home / ".kvist" / "themes" / "flat.json").write_text(
        json.dumps({"theme": {name: "#abcdef" for name in theme.SLOT_NAMES}}),
        encoding="utf-8",
    )
    flat = splash.build_style(theme.load_theme("flat", cwd=tmp_path), "dark")
    assert flat.style_rules != splash.build_style(loaded, "dark").style_rules
    for _, value in flat.style_rules:
        assert set(value.split()) <= {"#abcdef", "bg:#abcdef"}


# ── 9. no hard-coded colours anywhere in splash.py ───────────────────


def test_no_hex_colour_literals_in_source():
    source = (_ROOT / "splash.py").read_text(encoding="utf-8")
    assert re.search(r"#[0-9a-fA-F]{6}\b", source) is None


# ── 10. the meta line truncates instead of overflowing ──────────────


def test_meta_line_truncates_at_a_narrow_column():
    lines = render(
        48, 20, model="an-extremely-long-model-identifier", provider="a-provider"
    )
    assert all(len(line) <= 48 for line in lines)
    assert "…" in "\n".join(lines)


# ── 11. the input block sits on its own background slab ───────────────


def _block_rows(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if splash.ACCENT_BAR_CHAR in line]


def test_input_block_has_background_slab():
    width, height = 120, 40
    lines = render(width, height)
    at = render_attrs(width, height)

    loaded = theme.load_theme("kvist")
    slab = loaded.prompt_toolkit_color("backgroundPanel", "dark").lstrip("#")

    block_rows = _block_rows(lines)
    assert len(block_rows) == 4
    bar_col = _accent_column(lines)

    for y in block_rows:
        assert at(y, bar_col)[1].bgcolor != slab  # accent bar keeps screen bg
        for x in range(bar_col + 1, bar_col + splash.COLUMN_WIDTH):
            assert at(y, x)[1].bgcolor == slab, (y, x)

    above = block_rows[0] - 1
    below = block_rows[-1] + 1
    for x in range(bar_col, bar_col + splash.COLUMN_WIDTH):
        assert at(above, x)[1].bgcolor != slab
        assert at(below, x)[1].bgcolor != slab


# ── 12. the meta line carries three distinct brightness levels ───────


def test_meta_line_has_three_brightness_levels():
    width, height = 120, 40
    lines = render(width, height)
    at = render_attrs(width, height)

    meta_row = next(i for i, line in enumerate(lines) if "Build ·" in line)
    fg_by_char: dict[str, str] = {}
    for x in range(width):
        char, attrs = at(meta_row, x)
        if char.strip():
            fg_by_char[char] = attrs.color

    loaded = theme.load_theme("kvist")
    accent = loaded.prompt_toolkit_color("accent", "dark").lstrip("#")
    text = loaded.prompt_toolkit_color("text", "dark").lstrip("#")
    muted = loaded.prompt_toolkit_color("textMuted", "dark").lstrip("#")

    assert len({accent, text, muted}) == 3
    assert fg_by_char["B"] == accent  # Build
    assert fg_by_char["g"] == text  # gpt-5.2
    assert fg_by_char["n"] == muted  # openai
    assert set(fg_by_char.values()) == {accent, text, muted}


# ── 13. block columns match the opencode measurements ───────────────


def test_block_columns_match_measurements():
    width, height = 120, 40
    lines = render(width, height)
    bar_col = _accent_column(lines)
    assert bar_col == 23  # measurement: accent bar at column 23

    input_row = next(i for i, line in enumerate(lines) if "Ask Kvist" in line)
    assert lines[input_row].index("Ask Kvist") == bar_col + splash.BLOCK_TEXT_INDENT

    hint_row = next(i for i, line in enumerate(lines) if "/help" in line)
    assert len(lines[hint_row]) - len(lines[hint_row].lstrip()) == bar_col

    block_rows = _block_rows(lines)
    assert lines[block_rows[0]].strip() == splash.ACCENT_BAR_CHAR  # blank
    assert "Ask Kvist" in lines[block_rows[1]]
    assert lines[block_rows[2]].strip() == splash.ACCENT_BAR_CHAR  # blank
    assert "Build ·" in lines[block_rows[3]]


def test_long_model_name_keeps_right_pad_inside_slab():
    width, height = 120, 40
    long_model = "an-extremely-long-model-identifier-that-overruns-the-slab-x" * 2
    lines = render(width, height, model=long_model)
    at = render_attrs(width, height, model=long_model)

    loaded = theme.load_theme("kvist")
    slab = loaded.prompt_toolkit_color("backgroundPanel", "dark").lstrip("#")
    bar_col = _accent_column(lines)
    meta_row = next(i for i, line in enumerate(lines) if "Build" in line)

    for x in range(bar_col + splash.COLUMN_WIDTH - splash.BLOCK_INNER_PAD,
                   bar_col + splash.COLUMN_WIDTH):
        char, attrs = at(meta_row, x)
        assert attrs.bgcolor == slab  # slab still painted
        assert not char.strip()  # but no text in the right pad
    assert "…" in lines[meta_row]


# ── 14. the hint line is quiet: two brightness levels, no separators ──


def test_hint_line_form_matches_opencode():
    width, height = 120, 40
    lines = render(width, height)
    at = render_attrs(width, height)

    hint_row = next(i for i, line in enumerate(lines) if "/help" in line)
    assert lines[hint_row].strip() == "enter send  ctrl+c exit  /help commands"
    assert "·" not in lines[hint_row]

    loaded = theme.load_theme("kvist")
    text = loaded.prompt_toolkit_color("text", "dark").lstrip("#")
    muted = loaded.prompt_toolkit_color("textMuted", "dark").lstrip("#")

    fg_by_char: dict[str, str] = {}
    for x in range(width):
        char, attrs = at(hint_row, x)
        if char.strip():
            fg_by_char[char] = attrs.color

    assert fg_by_char["e"] == text  # enter / a key
    assert fg_by_char["s"] == muted  # send / an action
    assert set(fg_by_char.values()) == {text, muted}
