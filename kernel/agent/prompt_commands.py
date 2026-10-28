# -*- coding: utf-8 -*-
"""Slash commands that start a turn with a ready workflow, e.g. ``/website coffee shop``.

Each command is a bundled instructions skill of the same name. The command is
the person's shortcut for "attach that skill and follow it": the goal the model
receives names the skill, while the journal and the screen keep what the person
typed, so a replayed session shows ``/website coffee shop`` and not a framing
line. The workflow itself lives in the skill, which the model reads once and
reaches page by page; nothing long travels with every request.
"""
from __future__ import annotations

import re

PROMPT_COMMANDS = ("website",)

_COMMAND = re.compile(r"^/(?P<name>[\w-]+)(?:\s+(?P<request>.*))?$", re.DOTALL)


def expand_prompt_command(goal: str) -> tuple[str, str]:
    """The goal for the model and the text that decides the reply's language.

    The framing is English whatever the person writes, so it must not take part
    in language detection: a Russian request would otherwise be answered in
    English. Anything that is not a prompt command comes back unchanged.
    """
    match = _COMMAND.match(goal.strip())
    if match is None:
        return goal, goal
    name = match["name"].casefold()
    if name not in PROMPT_COMMANDS:
        return goal, goal
    typed = (match["request"] or "").strip()
    request = typed or "(no description yet; the skill's interview asks for it)"
    framed = f"{request}\n\nThe person started this with /{name}: attach the {name} skill (skills attach {name}) and follow it."
    return framed, typed
