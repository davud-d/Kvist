# -*- coding: utf-8 -*-
from __future__ import annotations


def detect_request_language(text: str, fallback: str = "en") -> str:
    source = str(text or "")
    cyrillic = sum(1 for ch in source if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in source if "a" <= ch.lower() <= "z")
    if cyrillic == latin:
        return fallback
    return "ru" if cyrillic > latin else "en"


def resolve_language(language_hint: str | None, fallback: str = "en") -> str:
    hint = str(language_hint or "").strip().lower()
    if hint in {"ru", "en"}:
        return hint
    return detect_request_language(str(language_hint or ""), fallback=fallback)


def choose_localized_text(
    ru_text: str,
    en_text: str,
    language_hint: str | None = None,
    fallback: str = "en",
) -> str:
    return ru_text if resolve_language(language_hint, fallback=fallback) == "ru" else en_text
