from __future__ import annotations

from kernel.context.language_utils import choose_localized_text, detect_request_language, resolve_language


def test_detect_request_language_counts_cyrillic_and_latin():
    assert detect_request_language("hello world") == "en"
    assert detect_request_language("мир мир мир") == "ru"
    assert detect_request_language("123", fallback="ru") == "ru"


def test_resolve_language_accepts_known_hints_and_detects_unknown_text():
    assert resolve_language("ru") == "ru"
    assert resolve_language("EN") == "en"
    assert resolve_language("hello") == "en"


def test_choose_localized_text_uses_resolved_language():
    assert choose_localized_text("ru text", "en text", "ru") == "ru text"
    assert choose_localized_text("ru text", "en text", "hello") == "en text"
