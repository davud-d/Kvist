from __future__ import annotations

from types import SimpleNamespace

import pytest
from prompt_toolkit.formatted_text import FormattedText

import banner
from agent_state import AgentConfig
from banner_frames import BOOT_MS, frame_at


BANNER_WIDTH = 112
SHORT_MODEL = "gpt-5.4-mini"
LONG_MODEL = "openrouter/anthropic/claude-sonnet-4.5"


def _fields(model: str = SHORT_MODEL) -> list[tuple[str, str]]:
    return [
        ("Provider:", "OpenAI"),
        ("Fast:", model),
        ("Smart:", model),
        ("Role:", "auto"),
        ("Commands:", "/commands"),
    ]


def _animated_frame(text: str = banner.BANNER_TEXT):
    return frame_at(BOOT_MS / 1000 + 0.1, text)


def _plain_text(fragments: list[tuple[str, str]]) -> str:
    return "".join(fragment[1] for fragment in fragments)


def _set_supported_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KVIST_NO_BANNER", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.setattr(banner.sys, "platform", "linux")
    monkeypatch.setattr(banner.sys.stdout, "isatty", lambda: True)


def _set_message_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    width: int = 120,
) -> None:
    monkeypatch.setattr(
        banner,
        "get_app",
        lambda: SimpleNamespace(is_done=False),
    )
    monkeypatch.setattr(banner, "get_saved_role", lambda: None)
    monkeypatch.setattr(banner.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(banner.console, "width", width)


def test_validate_font_accepts_every_glyph():
    assert banner._validate_font() is None


@pytest.mark.parametrize("model", [SHORT_MODEL, LONG_MODEL])
def test_build_grid_has_fixed_height_and_equal_width_rows(model):
    grid = banner.build_grid(
        _animated_frame(),
        width=BANNER_WIDTH,
        fields=_fields(model),
    )

    assert grid is not None
    assert len(grid) == 17
    assert {len(row) for row in grid} == {BANNER_WIDTH}


def test_build_grid_truncates_long_values_from_the_head():
    width = 95
    value_room = (width - 61) - 12
    grid = banner.build_grid(
        _animated_frame(),
        width=width,
        fields=_fields(LONG_MODEL),
    )

    assert grid is not None
    value_start = 60 + 10 + 1
    rendered_value = "".join(
        cell[0] for cell in grid[5][value_start : value_start + value_room]
    )
    assert len(rendered_value) == value_room
    assert rendered_value == "…" + LONG_MODEL[-(value_room - 1) :]
    assert grid[5][value_start - 1][0] == "["
    assert grid[5][value_start + value_room][0] == "]"
    assert grid[5][width - 1][0] == "│"


def test_short_content_produces_a_narrower_banner(monkeypatch):
    _set_message_runtime(monkeypatch)
    base_prompt = FormattedText([("class:test", "\n❯ ")])
    short_config = AgentConfig(
        fast_model="x",
        smart_model="x",
    )
    long_config = AgentConfig(
        fast_model=LONG_MODEL,
        smart_model=LONG_MODEL,
    )

    short_text = _plain_text(
        banner.make_banner_message(short_config, base_prompt)()
    )
    long_text = _plain_text(
        banner.make_banner_message(long_config, base_prompt)()
    )
    short_width = len(short_text.split("\n", 1)[0])
    long_width = len(long_text.split("\n", 1)[0])

    assert short_width == 85
    assert long_width == 111
    assert short_width < long_width


def test_build_grid_places_borders_and_divider_at_fixed_coordinates():
    grid = banner.build_grid(
        _animated_frame(),
        width=BANNER_WIDTH,
        fields=_fields(),
    )

    assert grid is not None
    assert grid[0][0] == ("╭", banner.OUTER_BORDER, "")
    assert grid[16][BANNER_WIDTH - 1] == ("╯", banner.OUTER_BORDER, "")
    assert grid[8][55] == ("│", banner.DIVIDER, "")


def test_build_grid_renders_half_block_glyphs_in_prompt_area():
    grid = banner.build_grid(
        _animated_frame(),
        width=BANNER_WIDTH,
        fields=_fields(),
    )

    assert grid is not None
    assert any(
        cell[0] == "▀"
        for row in grid[6:10]
        for cell in row[4:49]
    )


def test_build_grid_rejects_a_prompt_wider_than_the_left_block(monkeypatch):
    long_text = "abcdefgh"
    monkeypatch.setattr(banner, "BANNER_TEXT", long_text)

    assert (
        banner.build_grid(
            _animated_frame(long_text),
            width=BANNER_WIDTH,
            fields=_fields(),
        )
        is None
    )


def test_grid_to_fragments_collapses_a_uniform_row():
    width = 100
    grid = [[(" ", "", "") for _ in range(width)]]

    fragments = banner.grid_to_fragments(grid)

    assert fragments == [("", " " * width)]
    assert len(fragments) < width // 10


def test_grid_to_fragments_does_not_merge_across_newlines():
    style_cell = ("", "#123456", "#abcdef")
    grid = [
        [("a", style_cell[1], style_cell[2]), ("b", style_cell[1], style_cell[2])],
        [("c", style_cell[1], style_cell[2]), ("d", style_cell[1], style_cell[2])],
    ]

    fragments = banner.grid_to_fragments(grid)

    assert _plain_text(fragments) == "ab\ncd"
    assert not any("b" in text and "c" in text for _, text in fragments)
    assert sum(
        style == "fg:#123456 bg:#abcdef"
        for style, _ in fragments
    ) == 2


def test_banner_available_in_a_supported_terminal(monkeypatch):
    _set_supported_terminal(monkeypatch)

    assert banner.banner_available(120, 31) is True


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("KVIST_NO_BANNER", "1"),
        ("COLORTERM", ""),
        ("TERM", "linux"),
        ("TERM", "linux-16color"),
        ("TERM", "dumb"),
    ],
)
def test_banner_available_rejects_unsupported_environment(
    monkeypatch,
    variable,
    value,
):
    _set_supported_terminal(monkeypatch)
    monkeypatch.setenv(variable, value)

    assert banner.banner_available(120, 31) is False


def test_banner_available_rejects_an_empty_term_off_windows(monkeypatch):
    _set_supported_terminal(monkeypatch)
    monkeypatch.setenv("TERM", "")

    assert banner.banner_available(120, 31) is False


def test_banner_available_rejects_non_tty_stdout(monkeypatch):
    _set_supported_terminal(monkeypatch)
    monkeypatch.setattr(banner.sys.stdout, "isatty", lambda: False)

    assert banner.banner_available(120, 31) is False


def test_banner_available_rejects_a_short_terminal(monkeypatch):
    _set_supported_terminal(monkeypatch)

    assert banner.banner_available(120, 30) is False


def test_banner_available_rejects_a_narrow_terminal(monkeypatch):
    _set_supported_terminal(monkeypatch)

    assert banner.banner_available(86, 31) is False
    assert banner.banner_available(87, 31) is True


def test_banner_available_rejects_windows_without_vt(monkeypatch):
    _set_supported_terminal(monkeypatch)
    monkeypatch.setattr(banner.sys, "platform", "win32")
    monkeypatch.setenv("TERM", "")
    monkeypatch.setattr(banner, "enable_windows_vt", lambda: False)

    assert banner.banner_available(120, 31) is False


def test_make_banner_message_reuses_grid_for_an_unchanged_frame(monkeypatch):
    _set_message_runtime(monkeypatch)
    real_build_grid = banner.build_grid
    calls = 0

    def counted_build_grid(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_build_grid(*args, **kwargs)

    monkeypatch.setattr(banner, "build_grid", counted_build_grid)
    message = banner.make_banner_message(
        AgentConfig(),
        FormattedText([("class:test", "\n❯ ")]),
    )

    first = message()
    second = message()

    assert calls == 1
    assert first == second


def test_banner_hides_on_final_render(monkeypatch):
    """The final render must not leave the banner in terminal scrollback."""
    base_prompt = FormattedText([("class:test", "\n❯ ")])
    monkeypatch.setattr(
        banner,
        "get_app",
        lambda: SimpleNamespace(is_done=True),
    )
    monkeypatch.setattr(
        banner,
        "frame_at",
        lambda *_: pytest.fail("final render must not build an animation frame"),
    )
    message = banner.make_banner_message(AgentConfig(), base_prompt)

    assert message() is base_prompt
