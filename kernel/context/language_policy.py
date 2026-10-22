# -*- coding: utf-8 -*-
from __future__ import annotations

"""Runtime policy for the user-facing response language.

The action protocol (JSON keys, action types, tool schemas) is always English.
This module decides only the language of user-facing text: final_answer,
report_to_user output, plain chat replies, and user-visible status/error
messages. The decision is made by the runtime layer before any model call —
no LLM involved, plain character statistics.
"""

SUPPORTED_LANGUAGES = {"ru", "en"}
DEFAULT_LANGUAGE = "en"

# A mostly-Russian request routinely mixes in Latin identifiers (file names,
# commands, APIs), so a raw cyrillic > latin comparison misclassifies requests
# like "создай hello.txt". Cyrillic is a deliberate signal — nobody types it
# by accident — so it wins unless Latin text heavily dominates.
_CYRILLIC_MIN = 2
_CYRILLIC_WEIGHT = 3


def _count_letters(text: str) -> tuple[int, int]:
    cyrillic = 0
    latin = 0
    for ch in text:
        low = ch.lower()
        if "а" <= low <= "я" or low == "ё":
            cyrillic += 1
        elif "a" <= low <= "z":
            latin += 1
    return cyrillic, latin


def detect_response_language(text: str) -> str | None:
    """Detect the response language of *text*: ``"ru"``, ``"en"`` or ``None``.

    Returns ``None`` when the text carries no usable signal (no letters),
    so the caller can fall back to the previous session language.
    """
    cyrillic, latin = _count_letters(str(text or ""))
    if cyrillic == 0 and latin == 0:
        return None
    if cyrillic >= _CYRILLIC_MIN and cyrillic * _CYRILLIC_WEIGHT >= latin:
        return "ru"
    if cyrillic > latin:
        return "ru"
    return "en"


def resolve_response_language(
    user_text: str,
    previous_language: str | None = None,
) -> str:
    """Resolve the response language for the current request.

    Detection from *user_text* wins; an ambiguous text falls back to
    *previous_language* (the session's last resolved language); with no
    history the default is English.
    """
    detected = detect_response_language(user_text)
    if detected in SUPPORTED_LANGUAGES:
        return detected
    if previous_language in SUPPORTED_LANGUAGES:
        return previous_language
    return DEFAULT_LANGUAGE


def response_language_name(language: str) -> str:
    return "Russian" if language == "ru" else "English"
