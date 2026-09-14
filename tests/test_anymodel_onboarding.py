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
