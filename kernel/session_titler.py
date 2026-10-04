# -*- coding: utf-8 -*-
"""Automatic session titles.

A session's title is generated once, in the background, from its first
completed turn: the user's own message plus the first lines of the agent's
reply — never the running context, and never something the turn itself waits
on (the caller spawns this off a background thread; nothing here blocks the
agent loop). The model is a separate, configurable ``AgentConfig.title_model``,
defaulting to the provider's fast/cheap tier. A model that does not answer
within ``GENERATION_TIMEOUT_SECONDS``, or answers with nothing usable, yields
to ``fallback_session_title`` instead — a session is never left untitled, and
the failure is only ever reported through the caller's log, never shown to
the user as an error.
"""
from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable

from kernel.agent_model import (
    _create_json_completion,
    _format_llm_error,
    safe_extract_completion_content,
)
from kernel.agent_state import AgentConfig

# How much of the turn feeds the prompt: the user's own words plus a preview
# of what the agent said, never the run's full tool output or context.
REPLY_PREVIEW_LINES = 20
# A hard cap independent of the caller's own (possibly much larger) API
# timeout: this call must never make the caller wait for it.
GENERATION_TIMEOUT_SECONDS = 5.0

TITLE_MAX_WORDS = 6
TITLE_MAX_CHARS = 50
FALLBACK_MAX_CHARS = 40

_SYSTEM_PROMPT = (
    "You title chat sessions. Read the user's request and the assistant's "
    "reply, then reply with ONLY a short title for this conversation: at "
    "most 6 words, no quotes, no trailing period, no prefix like 'Title:'. "
    "Write the title in the same language as the user's request."
)

_QUOTE_CHARS = "\"'«»“”‘’`"
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:название|заголовок|тема|title|caption)\s*[:\-—]\s*",
    re.IGNORECASE,
)
_TRAILING_PUNCT_RE = re.compile(r"[.\s]+$")


class _TitleUI:
    """The minimal ``_ui`` surface ``_create_json_completion`` reads.

    Errors and rejections are reported through the caller's own log function,
    never printed — title generation is a background nicety, not something
    the user should see fail.
    """

    def __init__(self, report: Callable[[str], None]) -> None:
        self._report = report

    def debug_print(self, message: str) -> None:
        self._report(message)

    def print_error(self, message: str) -> None:
        self._report(message)


@dataclasses.dataclass(slots=True)
class _TitleCaller:
    """The three attributes ``_create_json_completion`` reads from an agent."""

    client: Any
    config: AgentConfig
    _ui: Any


def fallback_session_title(text: str) -> str:
    """Cut *text* to ``FALLBACK_MAX_CHARS`` at a word boundary.

    This is what a session is called when nothing smarter is available: the
    async-generation fallback, and the title shown for a session that predates
    this feature or has no generated title yet.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= FALLBACK_MAX_CHARS:
        return collapsed
    cut = collapsed[:FALLBACK_MAX_CHARS]
    boundary = cut.rfind(" ")
    return (cut[:boundary] if boundary > 0 else cut).strip()


def clean_generated_title(raw: str) -> str:
    """Model output -> a short title, or "" when nothing usable survives.

    Never trusted to have followed the prompt on its own: quotes, a trailing
    period, and a "Title:"/"Название:" prefix are stripped here, in code, not
    left to the model's discipline. An empty result after cleaning is a
    generation failure, handled by the caller's fallback.
    """
    text = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    text = _TITLE_PREFIX_RE.sub("", text).strip()
    while len(text) >= 2 and text[0] in _QUOTE_CHARS and text[-1] in _QUOTE_CHARS:
        text = text[1:-1].strip()
    text = _TRAILING_PUNCT_RE.sub("", text).strip()
    if not text:
        return ""
    words = text.split()
    if len(words) > TITLE_MAX_WORDS:
        text = " ".join(words[:TITLE_MAX_WORDS])
    if len(text) > TITLE_MAX_CHARS:
        cut = text[:TITLE_MAX_CHARS]
        boundary = cut.rfind(" ")
        text = cut[:boundary] if boundary > 0 else cut
    return text.strip()


def session_display_title(document: dict[str, Any], session_name: str) -> str:
    """The title to show for a session document: stored, derived, or the name.

    A session saved before this feature existed, or whose background
    generation has not landed yet, carries no ``title`` — its first recorded
    message stands in, exactly like the async-generation fallback, so a
    listing never shows a session as untitled while it already holds content.
    A run interrupted before its first turn was recorded may only have a
    checkpoint, whose goal is the final fallback before the storage name.
    """
    title = document.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    history = document.get("session_history")
    if isinstance(history, list) and history:
        first = history[0]
        if isinstance(first, dict):
            goal = first.get("goal")
            if isinstance(goal, str) and goal.strip():
                return fallback_session_title(goal)
    chat = document.get("chat_history")
    if isinstance(chat, list):
        for message in chat:
            if (
                isinstance(message, dict)
                and message.get("role") == "user"
                and isinstance(message.get("content"), str)
                and message["content"].strip()
            ):
                return fallback_session_title(message["content"])
    checkpoint = document.get("checkpoint")
    if isinstance(checkpoint, dict):
        goal = checkpoint.get("goal")
        if isinstance(goal, str) and goal.strip():
            return fallback_session_title(goal)
    return session_name


def build_title_prompt(user_goal: str, agent_reply: str) -> str:
    preview = "\n".join(agent_reply.splitlines()[:REPLY_PREVIEW_LINES])
    return (
        f"User request:\n{user_goal}\n\n"
        f"Assistant reply (first {REPLY_PREVIEW_LINES} lines):\n{preview}"
    )


def generate_session_title(
    client: Any,
    config: AgentConfig,
    user_goal: str,
    agent_reply: str,
    *,
    report: Callable[[str], None] = lambda message: None,
) -> str:
    """Ask ``config.title_model`` (or ``config.fast_model``) for a title.

    Never raises: a rejected, empty, or slow (past ``GENERATION_TIMEOUT_SECONDS``)
    reply falls back to ``fallback_session_title(user_goal)``, so a session is
    always left with *some* title once this returns.
    """
    fallback = fallback_session_title(user_goal)
    model = config.title_model or config.fast_model
    if not model:
        return fallback
    title_config = dataclasses.replace(
        config,
        api_timeout=GENERATION_TIMEOUT_SECONDS,
        stream=False,
    )
    caller = _TitleCaller(client=client, config=title_config, _ui=_TitleUI(report))
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": build_title_prompt(user_goal, agent_reply)},
    ]
    try:
        response = _create_json_completion(
            caller, model, messages, stream=False, json_mode=False
        )
        raw = safe_extract_completion_content(response, config.provider, model)
    except Exception as error:  # noqa: BLE001 — provider SDKs share no base class
        report(f"Session title generation failed: {_format_llm_error(error, config.provider)}")
        return fallback
    return clean_generated_title(raw) or fallback
