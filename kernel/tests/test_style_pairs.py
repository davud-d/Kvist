# -*- coding: utf-8 -*-
"""One style choice: the agent style is picked, the chat style is derived from it by one table."""
from __future__ import annotations

from pathlib import Path

import pytest

import kernel
from kernel.agent_logging import session_styles
from kernel.user_config import (
    chat_style_for,
    get_saved_style,
    get_style_options,
    save_style_configuration,
    save_user_config,
    style_slug,
)

_STYLES_DIR = Path(kernel.__file__).parent / "prompts" / "styles"

_PAIRS = {
    "Default": "Default",
    "Architect": "Default",
    "Senior-dev": "Honest",
    "Paranoid": "Honest",
    "Perfectionist": "Honest",
    "Friendly Developer": "Friendly",
    "Explainer": "Friendly",
    "Minimalist": "No-Friendly",
    "Fast-and-dirty": "Energetic",
}


@pytest.mark.parametrize(("agent_style", "chat_style"), _PAIRS.items())
def test_the_chat_style_is_derived_from_the_agent_style(agent_style, chat_style):
    assert chat_style_for(agent_style) == chat_style


def test_every_pick_has_an_agent_file_and_a_chat_file():
    assert set(get_style_options()) == set(_PAIRS)
    for agent_style in get_style_options():
        assert (_STYLES_DIR / "agents" / f"{style_slug(agent_style)}.txt").read_text(encoding="utf-8").strip()
        assert (_STYLES_DIR / "chat" / f"{style_slug(chat_style_for(agent_style))}.txt").read_text(
            encoding="utf-8"
        ).strip()


def test_every_chat_style_file_is_reachable_from_some_pick():
    reachable = {style_slug(chat_style_for(name)) for name in get_style_options()}
    on_disk = {path.stem for path in (_STYLES_DIR / "chat").glob("*.txt")}
    assert reachable == on_disk


def test_the_picker_line_names_the_chat_style_it_brings():
    assert get_style_options()["Minimalist"].endswith("Chat: No-Friendly.")


def test_a_saved_agent_style_sets_both_styles():
    assert save_style_configuration("Fast-and-dirty") is True
    assert get_saved_style("agents") == "Fast-and-dirty"
    assert get_saved_style("chat") == "Energetic"


def test_an_unknown_style_is_not_saved():
    assert save_style_configuration("No-Such-Style") is False
    assert get_saved_style("agents") == "Default"


def test_a_chat_key_left_by_an_older_build_is_ignored():
    save_user_config({"styles": {"agents": "Minimalist", "chat": "Energetic"}})

    assert get_saved_style("agents") == "Minimalist"
    assert get_saved_style("chat") == "No-Friendly"


def test_a_config_with_only_the_old_chat_key_reads_as_the_default_pair():
    save_user_config({"styles": {"chat": "Honest"}})

    assert get_saved_style("agents") == "Default"
    assert get_saved_style("chat") == "Default"


def test_saving_a_style_leaves_the_old_chat_key_alone_and_unused():
    save_user_config({"styles": {"chat": "Honest"}})
    save_style_configuration("Explainer")

    assert get_saved_style("chat") == "Friendly"


def test_a_new_session_is_stamped_with_both_names_of_the_saved_pair():
    save_style_configuration("Paranoid")

    assert session_styles({}) == {"agents": "Paranoid", "chat": "Honest"}


def test_an_old_session_stamp_is_kept_whatever_the_table_says():
    save_style_configuration("Minimalist")
    stored = {"styles": {"agents": "Paranoid", "chat": "Energetic"}}

    assert session_styles(stored) == {"agents": "Paranoid", "chat": "Energetic"}


def test_an_older_config_does_not_stop_a_session_from_starting(agent_factory):
    save_user_config({"styles": {"chat": "Honest"}})
    agent = agent_factory()

    agent._restore_session_document({})

    assert agent._session_styles == {"agents": "Default", "chat": "Default"}
