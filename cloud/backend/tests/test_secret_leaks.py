# -*- coding: utf-8 -*-
"""The model key must never reach logs, API responses, events or exceptions.

The canary is matched against everything the server says or writes. Events are covered in
test_gateway.py and the end-to-end test, where a runner and a hostile fake Kvist exist.
"""
from __future__ import annotations

import logging

import httpx
import pytest
from conftest import dev_login
from fastapi.testclient import TestClient
from sqlalchemy import text

from kvist_cloud import keys

CANARY = "sk-canary-LEAK-0123456789abcdefXYZ"
# The tail stays visible on purpose ("…fXYZ"); everything before it must not.
CANARY_HEAD = CANARY[:-4]


@pytest.fixture
def everything_logged(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def assert_clean(*texts: str) -> None:
    for chunk in texts:
        assert CANARY_HEAD not in chunk


def test_key_is_not_in_responses_or_logs(client_factory, everything_logged, engine):
    seen_headers: list[str] = []

    def provider(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers["authorization"])
        logging.getLogger("httpx").debug("outbound %s %s", request.url, request.headers)
        return httpx.Response(200, json={"data": []})

    client = client_factory(provider, AUTH_DEV_LOGIN="true")
    dev_login(client)
    saved = client.post("/api/model-key", json={"provider": "openai", "key": CANARY})
    assert saved.json()["ok"] is True
    # The key did reach the provider, which is the point of the check.
    assert seen_headers == [f"Bearer {CANARY}"]
    responses = [saved.text, client.get("/api/model-key").text, client.get("/api/me").text]
    assert_clean(*responses, everything_logged.text)
    with engine.connect() as connection:
        dump = "\n".join(str(row) for row in connection.execute(text("SELECT * FROM users")))
    assert_clean(dump)


def test_key_in_a_provider_error_does_not_leak(client_factory, everything_logged):
    def hostile(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot connect, header was {request.headers['authorization']}")

    client = client_factory(hostile, AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.post("/api/model-key", json={"provider": "openai", "key": CANARY})
    assert response.json() == {"ok": False, "code": "provider_down"}
    assert_clean(response.text, everything_logged.text)


def test_key_in_an_unhandled_exception_does_not_leak(app_factory, everything_logged, monkeypatch):
    async def exploding_check(_client, _provider, key, *_args, **_kwargs):
        raise RuntimeError(f"internal failure while checking {key}")

    monkeypatch.setattr(keys, "check_key", exploding_check)
    app = app_factory(AUTH_DEV_LOGIN="true")
    with TestClient(app, base_url="http://127.0.0.1:8740", raise_server_exceptions=False) as client:
        client.headers["X-Kvist-Client"] = "1"
        dev_login(client)
        response = client.post("/api/model-key", json={"provider": "openai", "key": CANARY})
    assert response.status_code == 500
    body = response.json()["error"]
    assert body["code"] == "internal" and body["support_code"].startswith("K-")
    assert "Traceback" not in response.text
    assert_clean(response.text, everything_logged.text)
    # The failure itself is logged, with the code the person can quote.
    assert body["support_code"] in everything_logged.text
    assert "internal failure while checking [redacted]" in everything_logged.text


def test_validation_error_does_not_echo_the_key(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.post("/api/model-key", json={"provider": "openai", "key": CANARY, "model": ["not", "a", "str"]})
    assert response.status_code == 422
    assert_clean(response.text)
    # An invalid key field itself: the default handler would echo it back.
    too_long = client.post("/api/model-key", json={"provider": "openai", "key": CANARY * 30})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["fields"] == ["key"]
    assert_clean(too_long.text)
