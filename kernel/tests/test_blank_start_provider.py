# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel import main


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)


def test_blank_start_without_a_tty_uses_the_plain_prompt(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)

    calls = []
    monkeypatch.setattr(main, "prompt_provider_choice", lambda: calls.append(True) or "openai")

    assert main.resolve_startup_provider() == "openai"
    assert calls == [True]


def test_blank_start_on_a_tty_opens_the_picker(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "choose_provider", lambda current, verbose, **kwargs: "deepseek")

    assert main.resolve_startup_provider() == "deepseek"


def test_an_explicit_override_wins(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)

    assert main.resolve_startup_provider(provider_override="anthropic") == "anthropic"


def test_a_saved_anymodel_provider_still_resolves(monkeypatch):
    """A config written when AnyModel was the default keeps working."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "anymodel")

    assert main.resolve_startup_provider() == "anymodel"


def test_the_picker_lists_anymodel_in_the_middle_not_first(monkeypatch):
    captured = {}

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, title, options, current_value="", **kwargs):
            captured["options"] = options
            return "openai"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)
    monkeypatch.setattr(main, "get_custom_providers", lambda: {})

    main.choose_provider("openai", False)

    values = [option["value"] for option in captured["options"]]
    assert 0 < values.index("anymodel") < len(values) - 1
    assert values.count("anymodel") == 1
