# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel import anymodel_onboarding


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


@pytest.fixture(autouse=True)
def _assume_gui_browser_available(monkeypatch):
    # Whether a real GUI session exists depends on the machine running the
    # tests (DISPLAY/WAYLAND_DISPLAY, CI container, ...); pin a DISPLAY value
    # so _gui_browser_available()'s *real* logic is deterministic without
    # replacing the function itself (tests for the actual detection logic,
    # and for the headless fallback, override this env var themselves).
    monkeypatch.setenv("DISPLAY", ":0")


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


def test_gui_browser_available_reflects_display_env_on_linux(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert anymodel_onboarding._gui_browser_available() is False

    monkeypatch.setenv("DISPLAY", ":0")
    assert anymodel_onboarding._gui_browser_available() is True


def test_gui_browser_always_available_on_macos_and_windows(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(anymodel_onboarding.sys, "platform", "darwin")
    assert anymodel_onboarding._gui_browser_available() is True
    monkeypatch.setattr(anymodel_onboarding.sys, "platform", "win32")
    assert anymodel_onboarding._gui_browser_available() is True


def test_headless_container_does_not_call_webbrowser_open_and_does_not_hang(monkeypatch):
    """No DISPLAY, no GUI session: webbrowser would otherwise happily launch
    a console browser (lynx/w3m/links) as a blocking subprocess in this same
    terminal — the flow must skip straight to the key prompt instead."""
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def _fail_open(*args, **kwargs):
        raise AssertionError("webbrowser.open must not run without a GUI session")

    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", _fail_open)
    monkeypatch.setattr(
        anymodel_onboarding, "fetch_model_coefficients",
        lambda api_key, force_refresh=False: {"m": 1.0},
    )
    monkeypatch.setattr(anymodel_onboarding, "set_provider_secret", lambda key, value, **kw: None)

    ui = _FakeUI(choose_result="register", secrets=["sk-good"])
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"


def test_webbrowser_error_falls_back_to_the_key_prompt_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)

    def _raise_webbrowser_error(*args, **kwargs):
        raise anymodel_onboarding.webbrowser.Error("no runnable browser")

    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", _raise_webbrowser_error)
    monkeypatch.setattr(
        anymodel_onboarding, "fetch_model_coefficients",
        lambda api_key, force_refresh=False: {"m": 1.0},
    )
    monkeypatch.setattr(anymodel_onboarding, "set_provider_secret", lambda key, value, **kw: None)

    ui = _FakeUI(choose_result="register", secrets=["sk-good"])
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"


def test_oauth_result_short_circuits_the_manual_screen(monkeypatch):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(anymodel_onboarding, "try_oauth_register", lambda: "anymodel")

    ui = _FakeUI()
    result = anymodel_onboarding.offer_anymodel_registration(ui)

    assert result == "anymodel"
    assert ui.choose_calls == []
