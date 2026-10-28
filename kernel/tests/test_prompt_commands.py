# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.agent.prompt_commands import PROMPT_COMMANDS, expand_prompt_command
from kernel.agent.role_router import route_role
from kernel.context.language_policy import resolve_response_language
from kernel.plugins.bundled import BUNDLED_ROOT
from kernel.protocol.slash_commands import SLASH_COMMANDS


def test_a_request_is_framed_with_the_skill_to_attach():
    framed, typed = expand_prompt_command("/website сайт кофейни")

    assert framed.startswith("сайт кофейни\n\n")
    assert "skills attach website" in framed
    assert typed == "сайт кофейни"


def test_a_bare_command_asks_the_skill_to_interview():
    framed, typed = expand_prompt_command("/website")

    assert "no description yet" in framed
    assert typed == ""


def test_the_command_name_is_case_insensitive():
    framed, _ = expand_prompt_command("/Website landing")

    assert "skills attach website" in framed


def test_other_text_comes_back_unchanged():
    for goal in ("fix the bug", "/doctor", "/websites", "use /website later"):
        assert expand_prompt_command(goal) == (goal, goal)


def test_the_english_framing_does_not_decide_the_reply_language():
    framed, typed = expand_prompt_command("/website сайт кофейни с меню и ценами")

    assert resolve_response_language(typed) == "ru"
    assert resolve_response_language("", previous_language="ru") == "ru"
    assert framed != typed


def test_a_framed_request_still_reaches_a_changing_role():
    framed, _ = expand_prompt_command("/website сайт кофейни")

    assert route_role(framed) != "chat"


def test_every_prompt_command_has_a_skill_and_a_slash_entry():
    slash_names = {command.name for command in SLASH_COMMANDS}
    for name in PROMPT_COMMANDS:
        assert (BUNDLED_ROOT / name / "manifest.json").is_file()
        assert name in slash_names
