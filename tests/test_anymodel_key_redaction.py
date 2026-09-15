# -*- coding: utf-8 -*-
"""Explicit proof that an AnyModel API key never reaches logs, error
messages, or debug/verbose output — the same guarantee every other
provider's key already gets from redaction.py's suffix-based matching.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

import anymodel_onboarding
from providers import anymodel_pricing
from redaction import redact_text, redact_value

SYNTHETIC_KEY = "am-synthetic-not-a-real-secret-0123456789"


def test_anymodel_api_key_matches_the_generic_sensitive_key_suffix():
    redacted = redact_value({"anymodel_api_key": SYNTHETIC_KEY})
    assert redacted["anymodel_api_key"] == "[REDACTED]"


def test_anymodel_api_key_assignment_is_redacted_in_free_text():
    line = f"anymodel_api_key={SYNTHETIC_KEY}"
    redacted = redact_text(line)
    assert SYNTHETIC_KEY not in redacted


def test_anymodel_auth_error_message_never_contains_the_key(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(anymodel_pricing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelAuthError) as excinfo:
        anymodel_pricing.fetch_model_coefficients(SYNTHETIC_KEY)

    assert SYNTHETIC_KEY not in str(excinfo.value)


def test_anymodel_http_error_message_never_contains_the_key(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "Server Error", {}, io.BytesIO(b""))

    monkeypatch.setattr(anymodel_pricing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelUnavailableError) as excinfo:
        anymodel_pricing.fetch_model_coefficients(SYNTHETIC_KEY)

    assert SYNTHETIC_KEY not in str(excinfo.value)


def test_anymodel_unavailable_error_scrubs_a_key_that_leaks_into_the_local_reason(monkeypatch):
    """Pathological case: the low-level socket/OS reason string happens to
    echo something that looks like a credential assignment. redact_text
    must still catch it before it reaches the raised exception."""

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(f"connection reset, anymodel_api_key={SYNTHETIC_KEY}")

    monkeypatch.setattr(anymodel_pricing.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelUnavailableError) as excinfo:
        anymodel_pricing.fetch_model_coefficients(SYNTHETIC_KEY)

    assert SYNTHETIC_KEY not in str(excinfo.value)


def test_registration_screen_never_prints_the_raw_key_even_on_retry(monkeypatch, capsys):
    monkeypatch.setattr(anymodel_onboarding.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(anymodel_onboarding.webbrowser, "open", lambda url: None)

    bad_key = SYNTHETIC_KEY + "-bad"

    def fake_fetch(api_key, force_refresh=False):
        if api_key == bad_key:
            raise anymodel_onboarding.AnyModelAuthError("nope")
        return {"m": 1.0}

    monkeypatch.setattr(anymodel_onboarding, "fetch_model_coefficients", fake_fetch)
    monkeypatch.setattr(anymodel_onboarding, "set_provider_secret", lambda key, value, **kw: None)

    class _FakeUI:
        def __init__(self):
            self._secrets = [bad_key, SYNTHETIC_KEY]

        def choose_option(self, title, options, current_value="", help_text=""):
            return "register"

        def prompt_secret(self, title, current_value="", help_text=""):
            return self._secrets.pop(0)

    result = anymodel_onboarding.offer_anymodel_registration(_FakeUI())

    assert result == "anymodel"
    captured = capsys.readouterr()
    assert SYNTHETIC_KEY not in captured.out
    assert bad_key not in captured.out
