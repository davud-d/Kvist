# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

import main


@pytest.fixture(autouse=True)
def _clean_provider_env(monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANYMODEL_API_KEY", raising=False)


def test_blank_start_with_only_anymodel_key_resolves_to_anymodel(monkeypatch):
    """Fresh config, only ANYMODEL_API_KEY set: the agent must pick anymodel
    on its own, without prompting for any of the other five providers."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(
        main,
        "load_available_credentials",
        lambda keys: {"anymodel_api_key": "sk-test"} if "anymodel_api_key" in keys else {},
    )

    def _fail_choose_provider(*args, **kwargs):
        raise AssertionError("choose_provider must not run when AnyModel's key is already available")

    def _fail_prompt_provider_choice():
        raise AssertionError("prompt_provider_choice must not run when AnyModel's key is already available")

    monkeypatch.setattr(main, "choose_provider", _fail_choose_provider)
    monkeypatch.setattr(main, "prompt_provider_choice", _fail_prompt_provider_choice)

    assert main.resolve_startup_provider() == "anymodel"


def test_blank_start_without_any_key_still_falls_back_to_the_generic_picker(monkeypatch):
    """No saved provider and no AnyModel key: unchanged pre-existing
    behavior — the generic interactive picker (or its non-TTY fallback)
    still runs, this task does not remove that path."""
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)

    calls = []
    monkeypatch.setattr(main, "prompt_provider_choice", lambda: calls.append(True) or "openai")

    assert main.resolve_startup_provider() == "openai"
    assert calls == [True]


def test_an_explicit_override_still_wins_over_the_anymodel_default(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(
        main, "load_available_credentials",
        lambda keys: {"anymodel_api_key": "sk-test"} if "anymodel_api_key" in keys else {},
    )

    assert main.resolve_startup_provider(provider_override="anthropic") == "anthropic"


def test_a_previously_saved_provider_still_wins_over_the_anymodel_default(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(
        main, "load_available_credentials",
        lambda keys: {"anymodel_api_key": "sk-test"} if "anymodel_api_key" in keys else {},
    )

    assert main.resolve_startup_provider() == "openai"


def test_choose_provider_lists_anymodel_among_manual_tier_providers(monkeypatch):
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
    assert "anymodel" in values


def test_blank_start_on_a_tty_offers_anymodel_registration_first(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "TerminalBannerUI", lambda **kwargs: object())

    monkeypatch.setattr(main, "offer_anymodel_registration", lambda ui: "anymodel")

    def _fail_choose_provider(*args, **kwargs):
        raise AssertionError("choose_provider must not run once the registration screen succeeds")

    monkeypatch.setattr(main, "choose_provider", _fail_choose_provider)

    assert main.resolve_startup_provider() == "anymodel"


def test_esc_on_anymodel_registration_falls_back_to_choose_provider_without_anymodel(monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: None)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {})
    monkeypatch.setattr(main.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(main, "TerminalBannerUI", lambda **kwargs: object())

    monkeypatch.setattr(main, "offer_anymodel_registration", lambda ui: None)

    captured = {}

    def fake_choose_provider(current, verbose, exclude=None):
        captured["exclude"] = exclude
        return "openai"

    monkeypatch.setattr(main, "choose_provider", fake_choose_provider)

    assert main.resolve_startup_provider() == "openai"
    assert captured["exclude"] == frozenset({"anymodel"})


def test_choose_provider_exclude_filters_anymodel_out(monkeypatch):
    captured = {}

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, title, options, current_value="", **kwargs):
            captured["options"] = options
            return "openai"

    monkeypatch.setattr(main, "TerminalBannerUI", FakeUI)
    monkeypatch.setattr(main, "get_custom_providers", lambda: {})

    main.choose_provider("openai", False, exclude=frozenset({"anymodel"}))

    values = [option["value"] for option in captured["options"]]
    assert "anymodel" not in values
    assert "openai" in values
