# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import socket
import urllib.error

import pytest

from kernel.providers import anymodel_pricing


@pytest.fixture(autouse=True)
def _clear_cache():
    anymodel_pricing.clear_anymodel_pricing_cache()
    yield
    anymodel_pricing.clear_anymodel_pricing_cache()


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, _max_bytes=None):
        return self._body


def _catalog_payload():
    return {
        "data": [
            {"id": "cc/claude-opus-4-8", "billing": {"coefficient": 1.5}},
            {"id": "cc/claude-sonnet-5", "billing": {"coefficient": 0.8}},
            {"id": "cc/no-billing-field"},
        ]
    }


def test_fetch_model_coefficients_parses_billing_coefficient(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(_catalog_payload())

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    coefficients = anymodel_pricing.fetch_model_coefficients("test-key")

    assert coefficients == {"cc/claude-opus-4-8": 1.5, "cc/claude-sonnet-5": 0.8}
    assert len(calls) == 1


def test_second_call_within_ttl_is_served_from_cache(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(_catalog_payload())

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    anymodel_pricing.fetch_model_coefficients("test-key")
    anymodel_pricing.fetch_model_coefficients("test-key")

    assert len(calls) == 1


def test_force_refresh_bypasses_cache(monkeypatch):
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(_catalog_payload())

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    anymodel_pricing.fetch_model_coefficients("test-key")
    anymodel_pricing.fetch_model_coefficients("test-key", force_refresh=True)

    assert len(calls) == 2


def test_get_model_coefficient_returns_none_for_unknown_model(monkeypatch):
    monkeypatch.setattr(
        anymodel_pricing._catalog_opener,
        "open",
        lambda request, timeout=None: _FakeResponse(_catalog_payload()),
    )

    assert anymodel_pricing.get_model_coefficient("test-key", "does-not-exist") is None
    assert anymodel_pricing.get_model_coefficient("test-key", "cc/claude-opus-4-8") == 1.5


def test_401_raises_auth_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelAuthError):
        anymodel_pricing.fetch_model_coefficients("bad-key")


def test_timeout_raises_unavailable_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelUnavailableError):
        anymodel_pricing.fetch_model_coefficients("test-key")


def test_network_error_raises_unavailable_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", fake_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelUnavailableError):
        anymodel_pricing.fetch_model_coefficients("test-key")


def test_failed_fetch_is_not_cached(monkeypatch):
    attempts = {"count": 0}

    def flaky_urlopen(request, timeout=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.URLError("temporary failure")
        return _FakeResponse(_catalog_payload())

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", flaky_urlopen)

    with pytest.raises(anymodel_pricing.AnyModelUnavailableError):
        anymodel_pricing.fetch_model_coefficients("test-key")

    coefficients = anymodel_pricing.fetch_model_coefficients("test-key")
    assert coefficients["cc/claude-opus-4-8"] == 1.5
    assert attempts["count"] == 2


def test_compute_step_cost_is_pure_arithmetic():
    assert anymodel_pricing.compute_step_cost(100, 50, 2.0) == 300.0
    assert anymodel_pricing.compute_step_cost(0, 0, 5.0) == 0.0


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.com/models",
                                      "ftp://example.com/models", "https://user:secret@example.com/models"])
def test_catalog_rejects_unsafe_urls_before_network(monkeypatch, url):
    def unexpected_request(*args, **kwargs):
        pytest.fail("unsafe URL reached the network")

    monkeypatch.setattr(anymodel_pricing._catalog_opener, "open", unexpected_request)
    with pytest.raises(anymodel_pricing.AnyModelUnavailableError):
        anymodel_pricing.fetch_model_coefficients("test-key", base_url=url)


@pytest.mark.parametrize("destination", ["https://other.example/models", "http://anymodel.org/models",
                                             "file:///etc/passwd"])
def test_catalog_refuses_redirects_with_credentials(destination):
    request = anymodel_pricing.urllib.request.Request(
        anymodel_pricing.ANYMODEL_MODELS_URL, headers={"Authorization": "Bearer test-key"})
    handler = anymodel_pricing._NoCatalogRedirect()
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, destination)
