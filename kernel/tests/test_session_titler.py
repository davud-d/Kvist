# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.agent_state import AgentConfig
from kernel.session_titler import (
    clean_generated_title,
    fallback_session_title,
    generate_session_title,
    session_display_title,
)
from kernel.tests.conftest import FakeLLMClient


def _config(**overrides) -> AgentConfig:
    values = {"provider": "openai", "fast_model": "gpt-5-mini"}
    values.update(overrides)
    return AgentConfig(**values)


# -- clean_generated_title -----------------------------------------------------


def test_clean_generated_title_strips_quotes_and_period():
    assert clean_generated_title('"Fix the login bug."') == "Fix the login bug"


def test_clean_generated_title_strips_russian_prefix():
    assert clean_generated_title("Название: Починить баг входа") == "Починить баг входа"


def test_clean_generated_title_strips_title_prefix_case_insensitive():
    assert clean_generated_title("TITLE: Add dark mode") == "Add dark mode"


def test_clean_generated_title_strips_smart_quotes():
    assert clean_generated_title("“Refactor the parser”") == "Refactor the parser"


def test_clean_generated_title_truncates_to_six_words():
    raw = "one two three four five six seven eight"
    assert clean_generated_title(raw) == "one two three four five six"


def test_clean_generated_title_truncates_to_fifty_chars_at_word_boundary():
    raw = "a" * 20 + " " + "b" * 40
    cleaned = clean_generated_title(raw)
    assert len(cleaned) <= 50
    assert not cleaned.endswith("b" * 40)


def test_clean_generated_title_empty_after_cleaning_is_failure():
    assert clean_generated_title('""') == ""
    assert clean_generated_title("   ") == ""
    assert clean_generated_title("Title:") == ""


def test_clean_generated_title_only_first_line():
    assert clean_generated_title("Add caching\nSecond line ignored") == "Add caching"


# -- fallback_session_title -----------------------------------------------------


def test_fallback_session_title_short_text_unchanged():
    assert fallback_session_title("fix bug") == "fix bug"


def test_fallback_session_title_cuts_at_word_boundary_within_40_chars():
    text = "Please refactor the authentication module to use the new session store"
    title = fallback_session_title(text)
    assert len(title) <= 40
    assert text.startswith(title)
    # The cut lands on a whole word: the next source character is a space,
    # never mid-word.
    assert text[len(title) : len(title) + 1] in (" ", "")


def test_fallback_session_title_collapses_whitespace():
    assert fallback_session_title("  fix   the   bug  ") == "fix the bug"


def test_fallback_session_title_hard_cuts_single_long_word():
    text = "a" * 60
    title = fallback_session_title(text)
    assert title == "a" * 40


# -- generate_session_title (FakeLLMClient, no real API calls) -----------------


def test_generate_session_title_success_cleans_model_output():
    client = FakeLLMClient(content='"Add dark mode toggle."')
    title = generate_session_title(client, _config(), "add a dark mode toggle please", "Done.")
    assert title == "Add dark mode toggle"


def test_generate_session_title_falls_back_on_empty_model_output():
    client = FakeLLMClient(content='""')
    title = generate_session_title(client, _config(), "fix the login bug", "Fixed it.")
    assert title == fallback_session_title("fix the login bug")


def test_generate_session_title_falls_back_on_request_failure():
    class RaisingClient:
        class _Completions:
            def create(self, **kwargs):
                raise TimeoutError("simulated provider timeout")

        chat = type("Chat", (), {"completions": _Completions()})()

    reports: list[str] = []
    title = generate_session_title(
        RaisingClient(),
        _config(),
        "refactor the payment flow",
        "",
        report=reports.append,
    )
    assert title == fallback_session_title("refactor the payment flow")
    assert reports  # the failure was logged, not swallowed silently


def test_generate_session_title_uses_title_model_when_set():
    client = FakeLLMClient(content="Add caching")
    generate_session_title(
        client, _config(title_model="ds-cheap"), "speed up the api", "Done."
    )
    assert client.requests[-1]["model"] == "ds-cheap"


def test_generate_session_title_defaults_to_fast_model():
    client = FakeLLMClient(content="Add caching")
    generate_session_title(client, _config(fast_model="ds-fast"), "speed up the api", "Done.")
    assert client.requests[-1]["model"] == "ds-fast"


def test_generate_session_title_request_uses_short_timeout():
    client = FakeLLMClient(content="Add caching")
    generate_session_title(client, _config(api_timeout=60.0), "speed up the api", "Done.")
    assert client.requests[-1]["timeout"] == 5.0


def test_generate_session_title_prompt_carries_only_first_20_reply_lines():
    client = FakeLLMClient(content="Add caching")
    long_reply = "\n".join(f"line {i}" for i in range(40))
    generate_session_title(client, _config(), "speed up the api", long_reply)
    prompt = client.requests[-1]["messages"][-1]["content"]
    assert "line 19" in prompt
    assert "line 20" not in prompt


# -- session_display_title -------------------------------------------------------


def test_session_display_title_prefers_stored_title():
    document = {"title": "Add caching", "session_history": [{"goal": "something else"}]}
    assert session_display_title(document, "chat-1") == "Add caching"


def test_session_display_title_falls_back_to_first_history_goal():
    document = {"title": None, "session_history": [{"goal": "fix the login bug now please"}]}
    assert session_display_title(document, "chat-1") == fallback_session_title(
        "fix the login bug now please"
    )


def test_session_display_title_falls_back_to_first_chat_message():
    document = {
        "title": None,
        "session_history": [],
        "chat_history": [{"role": "user", "content": "what does this repo do"}],
    }
    assert session_display_title(document, "chat-1") == "what does this repo do"


def test_session_display_title_falls_back_to_session_name():
    document = {"title": None, "session_history": [], "chat_history": []}
    assert session_display_title(document, "chat-1") == "chat-1"
