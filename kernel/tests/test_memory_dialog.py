# -*- coding: utf-8 -*-
"""The ``/memory`` dialog: what it shows about a record and what its actions store."""
from __future__ import annotations

import pytest

from kernel.memory import MemoryService
from kernel.memory.dialog import run_dialog
from kernel.memory.store import PERSON


class ScriptedUI:
    """Answers ``choose_option`` and ``prompt_text`` from queues and keeps what each dialog showed."""

    def __init__(self, choices: list, texts: list | None = None) -> None:
        self.choices = list(choices)
        self.texts = list(texts or [])
        self.shown: list[dict] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.shown.append({"title": title, "options": options, "help": help_text})
        return self.choices.pop(0)

    def prompt_text(self, title, current_value="", help_text=""):
        return self.texts.pop(0)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "config"))
    return MemoryService(tmp_path / "project", with_user_scope=True)


def _dialog(tmp_path, ui, confirm=lambda *_: True):
    return run_dialog(tmp_path / "project", ui, confirm, ru=False, model=None, session="")


def test_the_card_says_where_who_and_when_a_record_was_kept(tmp_path, memory):
    record = memory.remember("Run tests with -x", kind="decision", trust=PERSON, actor=PERSON)["record"]
    ui = ScriptedUI(["all", record["id"], "back", None, None])

    _dialog(tmp_path, ui)

    card = ui.shown[2]["help"]
    assert "this project only" in card
    assert "Written by: you" in card
    assert "Created " in card and "Sources:" in card


def test_a_preference_for_every_project_is_labelled_everywhere(tmp_path, memory):
    record = memory.remember("Answer in Russian", kind="preference", trust=PERSON, actor=PERSON, scope="user")["record"]
    ui = ScriptedUI(["user", record["id"], None, None, None])

    _dialog(tmp_path, ui)

    assert "everywhere" in ui.shown[2]["help"]
    assert ui.shown[1]["options"][0]["description"].count("everywhere") == 1


def test_the_background_list_holds_recaps_and_what_the_extractor_wrote(tmp_path, memory):
    manual = memory.remember("A fact I stated", kind="project_fact", trust=PERSON, actor=PERSON)["record"]
    store = memory.workspace
    store.remember("A fact it found", kind="project_fact", trust="inferred", actor="extractor")
    ui = ScriptedUI(["background", None, None])

    _dialog(tmp_path, ui)

    listed = [option["value"] for option in ui.shown[1]["options"]]
    assert manual["id"] not in listed and len(listed) == 1


def test_editing_through_the_dialog_makes_the_record_the_persons(tmp_path, memory):
    record = memory.workspace.remember("Uses pytest", kind="project_fact", trust="inferred", actor="extractor")[1]
    ui = ScriptedUI(["all", record.id, "edit", None, None, None], texts=["Uses pytest -x"])

    _dialog(tmp_path, ui)

    stored = memory.get(record.id)
    assert stored["content"] == "Uses pytest -x" and stored["trust"] == "person" and stored["version"] == 2


def test_forgetting_asks_and_a_no_keeps_the_record(tmp_path, memory):
    record = memory.remember("Keep me", kind="decision", trust=PERSON, actor=PERSON)["record"]
    ui = ScriptedUI(["all", record["id"], "forget", None, None, None])

    _dialog(tmp_path, ui, confirm=lambda *_: False)

    assert memory.get(record["id"])["content"] == "Keep me"


def test_marking_stale_needs_a_reason(tmp_path, memory):
    record = memory.remember("Old build command", kind="decision", trust=PERSON, actor=PERSON)["record"]
    ui = ScriptedUI(["all", record["id"], "stale", None, None, None], texts=["   "])

    _dialog(tmp_path, ui)

    assert memory.get(record["id"])["status"] == "active"


def test_adding_a_record_lands_in_the_chosen_scope(tmp_path, memory):
    ui = ScriptedUI(["add", "user", None], texts=["Prefer short answers"])

    _dialog(tmp_path, ui)

    assert [r.content for r in memory.user.list()] == ["Prefer short answers"]
    assert memory.workspace.list() == []


def test_leaving_the_dialog_prints_nothing(tmp_path, memory):
    assert _dialog(tmp_path, ScriptedUI([None])) == ""


def test_show_everything_returns_the_full_explanation(tmp_path, memory):
    record = memory.remember("Explain me", kind="decision", trust=PERSON, actor=PERSON)["record"]
    ui = ScriptedUI(["all", record["id"], "full"])

    text = _dialog(tmp_path, ui)

    assert record["id"] in text and "History:" in text
