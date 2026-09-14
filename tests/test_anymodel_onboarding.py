# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

import anymodel_onboarding


@pytest.fixture(autouse=True)
def _clean_oauth_flag(monkeypatch):
    monkeypatch.delenv("AGENT_ANYMODEL_OAUTH", raising=False)


def test_oauth_stub_disabled_by_default_returns_none(monkeypatch):
    assert anymodel_onboarding.try_oauth_register() is None


def test_oauth_stub_makes_no_network_call_when_flag_is_off(monkeypatch):
    def _fail_urlopen(*args, **kwargs):
        raise AssertionError("try_oauth_register must not touch the network with the flag off")

    monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

    assert anymodel_onboarding.try_oauth_register() is None


def test_oauth_stub_still_makes_no_network_call_when_flag_is_on(monkeypatch):
    """The flag only gates whether the stub is consulted at all — the real
    exchange endpoint is not implemented yet, so even 'enabled' must not
    reach the network until a confirmed endpoint lands."""
    monkeypatch.setenv("AGENT_ANYMODEL_OAUTH", "1")

    def _fail_urlopen(*args, **kwargs):
        raise AssertionError("try_oauth_register must not touch the network yet, flag or not")

    monkeypatch.setattr("urllib.request.urlopen", _fail_urlopen)

    assert anymodel_onboarding.try_oauth_register() is None


def test_oauth_flag_requires_exact_value(monkeypatch):
    monkeypatch.setenv("AGENT_ANYMODEL_OAUTH", "true")
    assert anymodel_onboarding._oauth_flag_enabled() is False

    monkeypatch.setenv("AGENT_ANYMODEL_OAUTH", "1")
    assert anymodel_onboarding._oauth_flag_enabled() is True


class _FakeUI:
    def __init__(self, choose_result="register", secrets=None):
        self.choose_result = choose_result
        self.secrets = list(secrets or [])
        self.choose_calls = []
        self.secret_calls = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.choose_calls.append((title, options, help_text))
        return self.choose_result

    def prompt_secret(self, title, current_value="", help_text=""):
        self.secret_calls.append(title)
        if not self.secrets:
            return None
        return self.secrets.pop(0)


def test_format_hyperlink_wraps_osc8_and_keeps_visible_text():
    result = anymodel_onboarding.format_hyperlink("click me", "https://example.test")
    assert "click me" in result
    assert "https://example.test" in result
    assert result.startswith("\x1b]8;;https://example.test\x1b\\")


def test_offer_registration_returns_none_without_a_tty(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: False)
    assert anymodel_onboarding.offer_anymodel_registration(_FakeUI()) is None


def test_esc_on_registration_screen_returns_none_without_opening_browser(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: opened.append(url))

    ui = _FakeUI(choose_result=None)
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result is None
    assert opened == []


def test_enter_opens_browser_and_prints_link_both_ways(monkeypatch, capsys):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    opened = []
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(
        anymodel_onboarding, "fetch_model_coefficients",
        lambda api_key, force_refresh=False: {"cc/claude-opus-4-8": 1.0},
    )
    saved = {}
    monkeypatch.setattr(
        anymodel_onboarding, "set_provider_secret",
        lambda key, value, **kw: saved.__setitem__(key, value),
    )

    ui = _FakeUI(choose_result="register", secrets=["sk-good"])
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"
    assert opened == [anymodel_onboarding.ANYMODEL_REGISTER_URL]
    assert saved == {"anymodel_api_key": "sk-good"}

    captured = capsys.readouterr()
    # Printed both as an OSC 8 hyperlink and as a plain text line under it.
    assert captured.out.count(anymodel_onboarding.ANYMODEL_REGISTER_URL) >= 2
    assert "\x1b]8;;" in captured.out


def test_empty_key_entry_aborts_without_saving(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: None)
    saved = {}
    monkeypatch.setattr(
        anymodel_onboarding, "set_provider_secret",
        lambda key, value, **kw: saved.__setitem__(key, value),
    )

    ui = _FakeUI(choose_result="register", secrets=[""])
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result is None
    assert saved == {}


def test_401_retries_key_entry_until_a_good_key_is_given(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: None)

    attempts = {"count": 0}

    def fake_fetch(api_key, force_refresh=False):
        attempts["count"] += 1
        if api_key == "bad-key":
            raise anymodel_onboarding.AnyModelAuthError("nope")
        return {"m": 1.0}

    monkeypatch.setattr(anymodel_onboarding, "fetch_model_coefficients", fake_fetch)
    saved = {}
    monkeypatch.setattr(
        anymodel_onboarding, "set_provider_secret",
        lambda key, value, **kw: saved.__setitem__(key, value),
    )

    ui = _FakeUI(choose_result="register", secrets=["bad-key", "bad-key", "good-key"])
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"
    assert attempts["count"] == 3
    assert saved == {"anymodel_api_key": "good-key"}
    assert len(ui.secret_calls) == 3


def test_timeout_during_key_validation_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: None)

    def fake_fetch(api_key, force_refresh=False):
        raise anymodel_onboarding.AnyModelUnavailableError("AnyModel request timed out")

    monkeypatch.setattr(anymodel_onboarding, "fetch_model_coefficients", fake_fetch)

    ui = _FakeUI(choose_result="register", secrets=["some-key"])
    with pytest.raises(RuntimeError, match="unreachable"):
        anymodel_onboarding.offer_anymodel_registration(ui)


def test_oauth_result_short_circuits_the_manual_screen(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding, "try_oauth_register", lambda: "anymodel")

    ui = _FakeUI()
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"
    assert ui.choose_calls == []
