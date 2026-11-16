"""The style ladder of best-of executors: which styles, in which order, and that each one really exists."""
from __future__ import annotations

import pytest

from kernel.agent.agent_model import _STYLES_DIR
from kernel.bestof.styles import (
    DEFAULT_EXECUTORS,
    MAX_EXECUTORS,
    MIN_EXECUTORS,
    STYLE_LADDER,
    style_name,
    styles_for,
)
from kernel.config.user_config import chat_style_for, get_style_options, style_slug


def test_two_executors_take_the_first_two_styles_and_four_the_first_four():
    assert styles_for(2) == ("senior-dev", "minimalist")
    assert styles_for(4) == ("senior-dev", "minimalist", "paranoid", "fast-and-dirty")


def test_six_executors_take_the_whole_ladder_and_four_is_the_default():
    assert styles_for(6) == STYLE_LADDER == (
        "senior-dev", "minimalist", "paranoid", "fast-and-dirty", "perfectionist", "architect",
    )
    assert DEFAULT_EXECUTORS == 4
    assert (MIN_EXECUTORS, MAX_EXECUTORS) == (2, 6)


@pytest.mark.parametrize("count", [0, 1, 7, -3, 4.0, "4", None, True])
def test_a_count_outside_two_to_six_is_refused_in_both_languages(count):
    with pytest.raises(ValueError, match=r"2 to 6 executors.* / .*от 2 до 6"):
        styles_for(count)


def test_the_ladder_has_no_repeats():
    assert len(set(STYLE_LADDER)) == len(STYLE_LADDER)


@pytest.mark.parametrize("slug", STYLE_LADDER)
def test_every_ladder_style_is_a_stock_agent_style_with_its_own_prompt_file(slug):
    path = _STYLES_DIR / "agents" / f"{slug}.txt"
    assert path.is_file()
    assert path.read_text(encoding="utf-8").startswith(f"WORKING STYLE: {slug}\n")

    name = style_name(slug)
    assert name in get_style_options()
    assert style_slug(name) == slug
    assert chat_style_for(name)  # the session stamps the chat style that goes with it


def test_an_unknown_slug_has_no_style_name():
    with pytest.raises(ValueError, match="No agent style"):
        style_name("friendly")
