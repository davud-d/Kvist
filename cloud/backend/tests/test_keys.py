# -*- coding: utf-8 -*-
"""Vault, scrubbing and the one-request key check, with every provider mocked."""
from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from kvist_cloud import secrets as vaultmod
from kvist_cloud.keys import check_key, endpoint_problem, kvist_environment
from kvist_cloud.models import User

KEY = "sk-test-0123456789abcdef"


def test_vault_round_trip_and_rotation():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    token = vaultmod.Vault(old).encrypt(KEY)
    assert KEY not in token
    assert vaultmod.Vault(f"{new},{old}").decrypt(token) == KEY
    with pytest.raises(vaultmod.SecretError):
        vaultmod.Vault(new).decrypt(token)


def test_bad_master_key_is_a_clear_error():
    with pytest.raises(vaultmod.SecretError):
        vaultmod.Vault("not-a-fernet-key")


def test_log_records_without_secrets_keep_their_arguments(caplog):
    import logging

    vaultmod.install_log_scrubber()
    logger = logging.getLogger("uvicorn.access.test")
    with caplog.at_level(logging.INFO, logger="uvicorn.access.test"):
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:1", "GET", "/", "1.1", 200)
        logger.info("key %s", KEY)
    clean, dirty = caplog.records[-2:]
    assert clean.args == ("127.0.0.1:1", "GET", "/", "1.1", 200)
    assert dirty.getMessage() == "key [redacted]"


def test_redact_known_and_unregistered_shapes():
    assert vaultmod.redact(f"key={KEY}!") == "key=[redacted]!"
    assert vaultmod.redact("custom-secret-value here", ["custom-secret-value"]) == "[redacted] here"
    assert vaultmod.redact("Authorization: Bearer abcdefghijklmnopqrstuv") == "Authorization: [redacted]"
    assert vaultmod.redact_obj({"a": [f"x {KEY}"], "n": 3}) == {"a": ["x [redacted]"], "n": 3}
    assert vaultmod.last4(KEY) == "cdef"


async def run_check(handler, provider, **kw):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await check_key(client, provider, KEY, **kw)


@pytest.mark.parametrize(
    ("provider", "status", "body", "code"),
    [
        ("openai", 200, {"data": []}, ""),
        ("openai", 401, {"error": {"code": "invalid_api_key"}}, "invalid_key"),
        ("openai", 429, {"error": {"code": "insufficient_quota"}}, "no_funds"),
        ("openai", 429, {"error": {"code": "rate_limit_exceeded"}}, "rate_limited"),
        ("anthropic", 401, {"type": "error"}, "invalid_key"),
        ("anthropic", 400, {"error": {"message": "Your credit balance is too low"}}, "no_funds"),
        ("anthropic", 503, {}, "provider_down"),
        ("deepseek", 200, {"is_available": False, "balance_infos": []}, "no_funds"),
        ("deepseek", 200, {"is_available": True, "balance_infos": []}, ""),
        ("deepseek", 402, {}, "no_funds"),
    ],
)
async def test_provider_answers_become_plain_codes(provider, status, body, code):
    result = await run_check(lambda request: httpx.Response(status, json=body), provider)
    assert (result.ok, result.code) == (code == "", code)


async def test_other_provider_checks_the_model():
    def handler(request):
        assert str(request.url) == "https://models.example/v1/models"
        return httpx.Response(200, json={"data": [{"id": "good-model"}]})

    assert (await run_check(handler, "other", base_url="https://models.example/v1", model="good-model")).ok
    result = await run_check(handler, "other", base_url="https://models.example/v1", model="nope")
    assert result.code == "model_not_found"


async def test_unreachable_provider():
    def down(_request):
        raise httpx.ConnectError("refused")

    assert (await run_check(down, "openai")).code == "provider_down"


@pytest.mark.parametrize(
    ("url", "allow_local", "problem"),
    [
        ("https://models.example/v1", False, ""),
        ("http://models.example/v1", False, "bad_endpoint"),
        ("https://127.0.0.1/v1", False, "bad_endpoint"),
        ("https://10.0.0.5/v1", False, "bad_endpoint"),
        ("https://localhost/v1", False, "bad_endpoint"),
        ("http://127.0.0.1:9999/v1", True, ""),
        ("ftp://x/v1", True, "bad_endpoint"),
    ],
)
def test_endpoint_rules(url, allow_local, problem):
    assert endpoint_problem(url, allow_local) == problem


def test_kvist_environment_per_provider():
    user = User(model_provider="deepseek")
    assert kvist_environment(user, KEY) == {"AGENT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": KEY}
    other = User(model_provider="other", model_base_url="https://m.example/v1", model_name="m1")
    env = kvist_environment(other, KEY)
    assert env["AGENT_LLM_PROVIDER"] == "omniroute"
    assert env["OMNIROUTE_API_KEY"] == KEY
    assert (env["OMNIROUTE_BASE_URL"], env["AGENT_SMART_MODEL"]) == (other.model_base_url, "m1")


def test_saved_key_is_encrypted_and_shown_by_last4(client_factory, engine):
    from conftest import dev_login

    client = client_factory(lambda request: httpx.Response(200, json={"is_available": True}), AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.post("/api/model-key", json={"provider": "deepseek", "key": KEY})
    assert response.json()["ok"] is True
    assert client.get("/api/model-key").json() == {
        "provider": "deepseek", "last4": "cdef", "base_url": "", "model": "", "set": True,
    }
    from kvist_cloud.db import make_session_factory

    with make_session_factory(engine)() as session:
        stored = session.scalars(select(User)).one().model_key_enc
    assert stored and KEY not in stored


def test_invalid_key_is_not_saved(client_factory):
    from conftest import dev_login

    client = client_factory(lambda request: httpx.Response(401, json={}), AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/model-key", json={"provider": "openai", "key": KEY}).json() == {
        "ok": False, "code": "invalid_key",
    }
    assert client.get("/api/model-key").json()["set"] is False
