# -*- coding: utf-8 -*-
from __future__ import annotations

from paste_labels import PasteStore, is_large_paste, label_length_before


def _lines(count: int) -> str:
    return "\n".join(f"line {i}" for i in range(count))


def test_small_pastes_stay_and_tall_or_long_ones_collapse():
    assert not is_large_paste("one\ntwo\nthree")
    assert is_large_paste(_lines(4))
    assert is_large_paste("x" * 800)


def test_collapsed_paste_expands_back_and_ids_count_up():
    store = PasteStore()
    label = store.collapse(_lines(34))
    assert label == "[Pasted text #1 +33 lines]"
    assert store.expand(f"fix: {label} now") == f"fix: {_lines(34)} now"
    assert store.collapse("y" * 900) == "[Pasted text #2 900 chars]"


def test_unknown_label_stays_as_written():
    assert PasteStore().expand("[Pasted text #7 +3 lines]") == "[Pasted text #7 +3 lines]"


def test_label_length_before_needs_label_at_cursor():
    text = "go [Pasted text #1 +33 lines] now"
    assert label_length_before(text, 29) == len("[Pasted text #1 +33 lines]")
    assert label_length_before(text, 30) == 0


def test_repeated_paste_finds_its_label():
    store = PasteStore()
    label = store.collapse(_lines(6))
    line = f"a {label} b"
    assert store.find_repeated_label(line, _lines(6)) == (2, 2 + len(label))
    assert store.find_repeated_label(line, _lines(7)) is None
