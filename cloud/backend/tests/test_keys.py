# -*- coding: utf-8 -*-
"""Vault, scrubbing and the one-request key check, with every provider mocked."""
from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from kvist_cloud import secrets as vaultmod
from kvist_cloud.keys import PROVIDERS, check_key, clean_headers, endpoint_problem, kvist_environment
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


def test_the_core_providers_are_all_offered_with_the_partner_first():
    from kernel.llm.provider_registry import MANUAL_OPENAI_PROVIDERS

    ids = list(PROVIDERS)
    assert ids[0] == "anymodel" and PROVIDERS["anymodel"].partner
    assert [p for p in PROVIDERS.values() if p.partner] == [PROVIDERS["anymodel"]]
    assert set(MANUAL_OPENAI_PROVIDERS) | {"deepseek", "openai", "anthropic", "chatgpt", "other"} == set(ids)
    assert len(ids) - 1 == 41  # the core's 40 and the ChatGPT sign-in, then the customer's own endpoint


def test_registry_provider_environment_uses_the_names_the_core_reads():
    env = kvist_environment(User(model_provider="qwen", model_name="qwen-max"), KEY)
    assert env == {"AGENT_LLM_PROVIDER": "qwen", "QWEN_API_KEY": KEY,
                   "AGENT_FAST_MODEL": "qwen-max", "AGENT_SMART_MODEL": "qwen-max"}
    # No fixed address (a workspace of the customer's own): the address is passed too.
    workspace = User(model_provider="databricks", model_name="m", model_base_url="https://w.example/v1")
    own = kvist_environment(workspace, KEY)
    assert own["DATABRICKS_BASE_URL"] == "https://w.example/v1"


async def test_registry_provider_is_checked_at_its_own_address_and_a_missing_model_list_is_accepted():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "models/gemini-x"}]})

    base = PROVIDERS["gemini"].default_base_url
    assert (await run_check(handler, "gemini", base_url=base, model="gemini-x")).ok
    assert seen == [base.rstrip("/") + "/models"]
    vendor = "https://x.example/v1"
    assert (await run_check(lambda r: httpx.Response(404, json={}), "perplexity", base_url=vendor)).ok
    refused = await run_check(lambda r: httpx.Response(401, json={}), "perplexity", base_url=vendor)
    assert refused.code == "invalid_key"


def test_registry_provider_needs_a_model_and_keeps_no_foreign_address(client_factory):
    from conftest import dev_login

    client = client_factory(lambda request: httpx.Response(200, json={"data": []}), AUTH_DEV_LOGIN="true")
    dev_login(client)
    body = {"provider": "qwen", "key": KEY, "base_url": "https://evil.example/v1"}
    assert client.post("/api/model-key", json=body).json() == {"ok": False, "code": "model_missing"}
    assert client.post("/api/model-key", json={**body, "model": "qwen-max"}).json()["ok"] is True
    assert client.get("/api/model-key").json()["base_url"] == ""


def test_saved_key_is_encrypted_and_shown_by_last4(client_factory, engine):
    from conftest import dev_login

    client = client_factory(lambda request: httpx.Response(200, json={"is_available": True}), AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.post("/api/model-key", json={"provider": "deepseek", "key": KEY})
    assert response.json()["ok"] is True
    assert client.get("/api/model-key").json() == {
        "provider": "deepseek", "label": "DeepSeek", "last4": "cdef", "base_url": "", "model": "", "headers": [],
        "set": True, "chatgpt_state": "", "chatgpt_email": "",
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


# -- extra headers for the customer's own gateway ------------------------------------------------------------

GATEWAY = "https://gw.example/v1"


def gateway_needing_a_session(seen: list):
    """Like OpenCode Go: refuses a request that carries no session header."""
    def handler(request):
        seen.append(dict(request.headers))
        if "x-opencode-session" not in request.headers:
            return httpx.Response(400, json={"error": "Request is missing x-opencode-session"})
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    return handler


async def test_a_gateway_that_wants_a_session_header_is_checked_with_it():
    seen: list = []
    refused = await run_check(gateway_needing_a_session(seen), "other", base_url=GATEWAY, model="m1")
    assert not refused.ok
    accepted = await run_check(gateway_needing_a_session(seen), "other", base_url=GATEWAY, model="m1",
                               headers={"x-opencode-session": "{session}"})
    assert accepted.ok
    sent = seen[-1]
    # {session} became a real id for this call, and the key's own header is not the customer's to change.
    assert sent["x-opencode-session"] not in ("", "{session}") and len(sent["x-opencode-session"]) == 36
    assert sent["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("headers", [
    {"Authorization": "Bearer x"}, {"host": "evil.example"}, {"Content-Length": "5"}, {"cookie": "a=b"},
    {"proxy-authorization": "x"}, {"bad name": "x"}, {"x:y": "x"}, {"x-ok": ""}, {"x-ok": "line\nbreak"},
    {"x-ok": "é"}, {"x-ok": "v" * 501}, {"x-a": "1", "X-A": "2"}, {f"x-{i}": "v" for i in range(9)}, {"": "v"},
])
def test_header_rules(headers):
    from kvist_cloud.errors import ApiError

    with pytest.raises(ApiError) as refused:
        clean_headers(headers)
    assert refused.value.code == "bad_headers"


def test_good_headers_are_kept_as_given():
    assert clean_headers({" x-opencode-session ": " {session} ", "X-Team": "kvist-1"}) == {
        "x-opencode-session": "{session}", "X-Team": "kvist-1"}


def test_headers_are_saved_encrypted_listed_by_name_only_and_handed_to_kvist(client_factory, engine):
    from conftest import dev_login

    from kvist_cloud.db import make_session_factory

    seen: list = []
    client = client_factory(gateway_needing_a_session(seen), AUTH_DEV_LOGIN="true")
    dev_login(client)
    body = {"provider": "other", "key": KEY, "base_url": GATEWAY, "model": "m1"}
    assert client.post("/api/model-key", json=body).json() == {"ok": False, "code": "bad_endpoint"}
    secret_value = "team-secret-value-123"
    answer = client.post("/api/model-key", json={
        **body, "headers": {"x-opencode-session": "{session}", "x-team-token": secret_value}}).json()
    assert answer["ok"] is True and answer["key"]["headers"] == ["x-opencode-session", "x-team-token"]
    assert secret_value not in client.get("/api/model-key").text

    with make_session_factory(engine)() as session:
        user = session.scalars(select(User)).one()
        assert user.model_headers_enc and secret_value not in user.model_headers_enc
        assert user.model_header_names == "x-opencode-session,x-team-token"
        import json as jsonmod

        headers = jsonmod.loads(client.app.state.vault.decrypt(user.model_headers_enc))
    env = kvist_environment(user, KEY, headers)
    import json

    assert json.loads(env["OMNIROUTE_HEADERS"]) == {"x-opencode-session": "{session}", "x-team-token": secret_value}
    # Without headers nothing extra is handed over.
    assert "OMNIROUTE_HEADERS" not in kvist_environment(user, KEY)


def test_other_providers_take_no_headers_and_deleting_the_key_drops_them(client_factory, engine):
    from conftest import dev_login

    from kvist_cloud.db import make_session_factory

    client = client_factory(lambda request: httpx.Response(200, json={"is_available": True, "data": [{"id": "m1"}]}),
                            AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.post("/api/model-key", json={"provider": "deepseek", "key": KEY,
                                               "headers": {"x-a": "1"}}).json()["key"]["headers"] == []
    client.post("/api/model-key", json={"provider": "other", "key": KEY, "base_url": GATEWAY, "model": "m1",
                                        "headers": {"x-a": "1"}})
    assert client.get("/api/model-key").json()["headers"] == ["x-a"]
    client.delete("/api/model-key")
    assert client.get("/api/model-key").json()["headers"] == []
    with make_session_factory(engine)() as session:
        user = session.scalars(select(User)).one()
        assert (user.model_headers_enc, user.model_header_names) == ("", "")


def test_bad_headers_are_refused_before_anything_is_called(client_factory):
    from conftest import dev_login

    called: list = []
    client = client_factory(lambda request: called.append(1) or httpx.Response(200, json={"data": []}),
                            AUTH_DEV_LOGIN="true")
    dev_login(client)
    answer = client.post("/api/model-key", json={"provider": "other", "key": KEY, "base_url": GATEWAY, "model": "m1",
                                                 "headers": {"Authorization": "Bearer stolen"}})
    assert answer.status_code == 400 and answer.json()["error"]["code"] == "bad_headers"
    assert called == []
