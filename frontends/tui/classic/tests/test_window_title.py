# -*- coding: utf-8 -*-
from __future__ import annotations

import window_title


def test_untitled_session_is_just_the_product_name():
    assert window_title.format_window_title(None) == "Kvist"
    assert window_title.format_window_title("   ") == "Kvist"


def test_title_follows_the_product_name():
    assert window_title.format_window_title("Fix the parser") == "Kvist | Fix the parser"


def test_control_characters_cannot_end_the_sequence():
    title = window_title.format_window_title("a\x07b\x1b]0;evil\x07")
    assert "\x07" not in title and "\x1b" not in title


def test_same_title_is_written_once(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(window_title, "_write", written.append)
    monkeypatch.setattr(window_title, "_last_written", None)

    window_title.set_window_title("One")
    window_title.set_window_title("One")
    window_title.set_window_title("Two")

    assert written == ["\x1b]2;Kvist | One\x07", "\x1b]2;Kvist | Two\x07"]
