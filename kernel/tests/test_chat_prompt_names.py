# -*- coding: utf-8 -*-
"""The chat prompt gives the model no role or tool names to retell to the person."""
from __future__ import annotations

import re
from pathlib import Path

_CHAT_PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "system" / "chat_prompt.txt"
_NAMES = re.compile(r"\b(reader|coder|devops|universal|web_search|fetch_url|run_shell|end_session)\b", re.IGNORECASE)


def test_the_chat_prompt_names_no_role_and_no_tool():
    assert _NAMES.findall(_CHAT_PROMPT.read_text(encoding="utf-8")) == []


def test_the_chat_prompt_still_tells_the_model_what_to_say_when_it_cannot_do_the_work():
    text = _CHAT_PROMPT.read_text(encoding="utf-8")

    assert "Kvist switches to working mode by itself" in text
    assert "прочитай файл X" in text
