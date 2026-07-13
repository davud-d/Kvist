# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from tests.conftest import make_command, make_plan

from language_policy import (
    detect_response_language,
    resolve_response_language,
    response_language_name,
)


# ── detection ────────────────────────────────────────────────────────────────

def test_detect_russian_request():
    assert detect_response_language("создай hello.txt") == "ru"


def test_detect_english_request():
    assert detect_response_language("create hello.txt") == "en"


def test_detect_russian_with_many_latin_identifiers():
    assert detect_response_language(
        "исправь функцию parse_agent_command в файле parser.py"
    ) == "ru"


def test_detect_ambiguous_returns_none():
    assert detect_response_language("123 ???") is None
    assert detect_response_language("") is None


# ── resolution ───────────────────────────────────────────────────────────────

def test_resolve_uses_previous_language_for_ambiguous_text():
    assert resolve_response_language("ок", previous_language="ru") == "ru"
    assert resolve_response_language("123", previous_language="ru") == "ru"


def test_resolve_defaults_to_english_without_history():
    assert resolve_response_language("123") == "en"
    assert resolve_response_language("", previous_language=None) == "en"


def test_resolve_detection_wins_over_previous():
    assert resolve_response_language("create hello.txt", previous_language="ru") == "en"
    assert resolve_response_language("создай hello.txt", previous_language="en") == "ru"


def test_response_language_name():
    assert response_language_name("ru") == "Russian"
    assert response_language_name("en") == "English"


# ── runtime prompt integration ───────────────────────────────────────────────

def _build_prompt(agent_factory, language: str) -> str:
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.response_language = language
    return agent._build_runtime_prompt("создай hello.txt", step=1)


def test_runtime_prompt_carries_russian_response_language(agent_factory):
    prompt = _build_prompt(agent_factory, "ru")
    assert "USER-FACING LANGUAGE:" in prompt
    assert "Current response_language: ru" in prompt
    assert "Task language: Russian" in prompt


def test_runtime_prompt_carries_english_response_language(agent_factory):
    prompt = _build_prompt(agent_factory, "en")
    assert "Current response_language: en" in prompt
    assert "Task language: English" in prompt


def test_runtime_prompt_has_no_english_only_directive(agent_factory):
    prompt = _build_prompt(agent_factory, "ru")
    assert "only in English" not in prompt


def test_runtime_prompt_keeps_protocol_english(agent_factory):
    prompt = _build_prompt(agent_factory, "ru")
    assert "JSON action keys, action types, and tool schemas are always English." in prompt


def test_runtime_prompt_does_not_require_template_phrase(agent_factory):
    prompt = _build_prompt(agent_factory, "ru")
    assert "Task completed successfully" not in prompt


def test_role_prompts_have_no_conflicting_english_only_directive():
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
    for name in ("coder_prompt.txt", "devops_prompt.txt", "chat_prompt.txt", "report_prompt.txt"):
        content = (prompts_dir / name).read_text(encoding="utf-8")
        assert "only in English" not in content, name


# ── final answer enforcement ─────────────────────────────────────────────────

def _final_answer_command(message: str):
    return make_command("final_answer", {"message": message}, progress=100, plan_step="done")


def test_english_final_rejected_for_russian_request(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "ru"
    error = agent._command_language_error(
        _final_answer_command("Task completed successfully. All checks passed.")
    )
    assert error is not None
    assert "response_language: ru" in error


def test_russian_final_accepted_for_russian_request(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "ru"
    command = _final_answer_command("Готово, создал `hello.txt` с текстом `Hello`.")
    assert agent._command_language_error(command) is None


def test_english_final_accepted_for_english_request(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "en"
    command = _final_answer_command("Done, created `hello.txt` with the text `Hello`.")
    assert agent._command_language_error(command) is None


def test_protocol_actions_are_not_language_checked(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "ru"
    # Intermediate protocol steps stay English and must never be rejected.
    command = make_command("read_file", {"path": "hello.txt"})
    assert agent._command_language_error(command) is None
    assert command.action.type == "read_file"
    assert json.dumps({"type": command.action.type}) == '{"type": "read_file"}'


def test_auto_completion_message_localized(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "ru"
    agent._state.files_changed = ["hello.txt"]
    message = agent._build_auto_completion_message()
    assert "Task completed successfully" not in message
    assert "hello.txt" in message
    assert "Готово" in message

    agent._state.response_language = "en"
    assert "Done" in agent._build_auto_completion_message()


def test_report_context_carries_response_language(agent_factory):
    from agent_runtime import _build_report_context

    agent = agent_factory()
    agent._state.response_language = "ru"
    context = json.loads(_build_report_context(agent, "создай hello.txt", "s", "r", ""))
    assert context["response_language"] == "ru"
