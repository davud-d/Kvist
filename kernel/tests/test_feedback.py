# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from kernel.agent import feedback
from kernel.agent.agent_tools import _feedback
from kernel.protocol.parser import CommandValidationError, validate_action_args


class FakeUI:
    def __init__(self, choice: str | None) -> None:
        self.choice = choice
        self.shown: list[str] = []
        self.options: list[dict] = []

    def print_user_markdown(self, message: str) -> None:
        self.shown.append(message)

    def choose_option(self, title, options, current_value="", help_text=""):
        self.options = options
        return self.choice


@pytest.fixture
def drafted(tmp_path):
    feedback.save_draft(tmp_path, "skills rejects page", "Steps:\n1. attach\n\nError: Extra fields: page")
    return tmp_path


def test_the_model_saves_a_draft_and_is_told_nothing_was_sent(tmp_path):
    agent = SimpleNamespace(executor=SimpleNamespace(workspace=tmp_path))
    data = _feedback(agent, {"title": "A  title", "body": "Body"})

    assert data["saved"] is True and "Nothing is sent" in data["next"] and "/feedback" in data["next"]
    assert feedback.load_draft(tmp_path) == {"title": "A title", "body": "Body"}


def test_a_second_call_replaces_the_draft_and_secrets_are_scrubbed(tmp_path):
    feedback.save_draft(tmp_path, "first", "one")
    feedback.save_draft(tmp_path, "second", "key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")

    draft = feedback.load_draft(tmp_path)
    assert draft["title"] == "second"
    assert "abcdefghijklmnopqrstuvwxyz" not in draft["body"]


@pytest.mark.parametrize("args", [
    {"title": "t"}, {"body": "b"}, {"title": " ", "body": "b"}, {"title": "t", "body": "b", "extra": "x"},
    {"title": "x" * 121, "body": "b"}, {"title": "t", "body": "x" * 6001},
])
def test_malformed_feedback_calls_are_refused(args):
    with pytest.raises(CommandValidationError):
        validate_action_args("feedback", args)


def test_a_report_in_another_language_is_refused_but_quoted_errors_pass():
    with pytest.raises(CommandValidationError, match="in English"):
        validate_action_args("feedback", {"title": "некоторые провайдеры ломают tool calling", "body": "Описание проблемы"})
    with pytest.raises(CommandValidationError, match="in English"):
        validate_action_args("feedback", {"title": "Tool calling breaks", "body": "Описание проблемы и шаги воспроизведения"})
    quoted = "Steps: attach a skill, then the call fails with the error below, which the runtime prints in Russian.\n```\nОшибка: лишнее поле page\n```"
    validate_action_args("feedback", {"title": "Extra field rejected", "body": quoted})


def test_the_person_sees_the_draft_as_a_letter_with_the_footer(drafted):
    ui = FakeUI("keep")
    ok, text = feedback.run_dialog(drafted, ui, russian=False)

    assert ok and text == "Draft kept."
    letter = ui.shown[0]
    assert letter.startswith(f"**To:** {feedback.ISSUES_URL}\n**Subject:** skills rejects page")
    assert "Extra fields: page" in letter and "Kvist " in letter.splitlines()[-1]
    assert feedback.load_draft(drafted) is not None
    assert [option["value"] for option in ui.options] == ["send", "keep", "discard"]


def test_discard_removes_the_draft(drafted):
    ok, _ = feedback.run_dialog(drafted, FakeUI("discard"), russian=True)

    assert ok and feedback.load_draft(drafted) is None


def test_cancelling_the_dialog_keeps_the_draft(drafted):
    ok, _ = feedback.run_dialog(drafted, FakeUI(None), russian=False)

    assert ok and feedback.load_draft(drafted) is not None


def test_without_a_draft_the_command_says_so(tmp_path):
    ok, text = feedback.run_dialog(tmp_path, FakeUI("send"), russian=False)

    assert not ok and "no draft" in text.lower()


def test_send_goes_through_gh_and_removes_the_draft(drafted, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="https://github.com/o/r/issues/7\n", stderr="")

    monkeypatch.setattr(feedback.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(feedback.subprocess, "run", fake_run)
    monkeypatch.setattr(feedback.webbrowser, "open", lambda url: pytest.fail("browser must not open"))

    ok, text = feedback.run_dialog(drafted, FakeUI("send"), russian=False)

    assert ok and text == "Sent: https://github.com/o/r/issues/7"
    command, kwargs = calls[0]
    assert command[1:5] == ["issue", "create", "--repo", feedback.RELEASE_REPO]
    assert "Extra fields: page" in kwargs["input"]
    assert feedback.load_draft(drafted) is None


@pytest.mark.parametrize("gh", [None, "/usr/bin/gh"])
def test_without_a_signed_in_gh_the_browser_form_opens_and_the_draft_stays(drafted, monkeypatch, gh):
    opened = []
    monkeypatch.setattr(feedback.shutil, "which", lambda name: gh)
    monkeypatch.setattr(
        feedback.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="not logged in"),
    )
    monkeypatch.setattr(feedback.webbrowser, "open", opened.append)

    ok, text = feedback.run_dialog(drafted, FakeUI("send"), russian=False)

    assert ok and "press Submit" in text
    assert opened and opened[0].startswith(f"{feedback.ISSUES_URL}/new?title=skills%20rejects%20page&body=")
    assert feedback.load_draft(drafted) is not None


def test_a_letter_too_long_for_the_form_is_refused_without_opening_a_browser(tmp_path, monkeypatch):
    feedback.save_draft(tmp_path, "t", "é" * 5000)
    monkeypatch.setattr(feedback.shutil, "which", lambda name: None)
    monkeypatch.setattr(feedback.webbrowser, "open", lambda url: pytest.fail("browser must not open"))

    ok, text = feedback.run_dialog(tmp_path, FakeUI("send"), russian=False)

    assert not ok and "too long" in text
    assert feedback.load_draft(tmp_path) is not None
