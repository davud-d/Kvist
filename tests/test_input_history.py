# -*- coding: utf-8 -*-
from __future__ import annotations

from input_history import MAX_ENTRIES, InputHistoryStore
from prompt_history import WorkspaceInputHistory


def test_entries_survive_a_new_store_and_repeats_are_skipped(tmp_path):
    store = InputHistoryStore(tmp_path)
    for text in ("one", "one", "two\nlines", "  "):
        store.add(text)

    assert InputHistoryStore(tmp_path).entries() == ["one", "two\nlines"]


def test_only_the_last_hundred_are_kept(tmp_path):
    store = InputHistoryStore(tmp_path)
    for number in range(MAX_ENTRIES + 5):
        store.add(f"entry {number}")

    entries = InputHistoryStore(tmp_path).entries()
    assert len(entries) == MAX_ENTRIES
    assert entries[0] == "entry 5"
    assert entries[-1] == f"entry {MAX_ENTRIES + 4}"


def test_a_damaged_file_reads_as_empty_history(tmp_path):
    (tmp_path / ".kvist").mkdir()
    (tmp_path / ".kvist" / "input_history.json").write_text("{not json")

    assert InputHistoryStore(tmp_path).entries() == []


def test_prompt_toolkit_history_loads_newest_first_and_stores(tmp_path):
    InputHistoryStore(tmp_path).add("old")
    history = WorkspaceInputHistory(tmp_path)
    history.append_string("new")

    assert list(WorkspaceInputHistory(tmp_path).load_history_strings()) == ["new", "old"]
