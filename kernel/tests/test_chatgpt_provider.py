# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kernel import llm_clients as llm, user_config
from kernel.config_info import build_config_info
from kernel.providers import chatgpt_auth as auth, chatgpt_client as transport
from kernel.reasoning_effort import reasoning_request_kwargs

_verify_identity = auth._validate_identity


def tokens(**changes):
    return {"access_token": "access-fixture", "refresh_token": "refresh-fixture", "id_token": "id-fixture",
            "token_type": "Bearer", "expires_in": 3600, "scope": auth.SCOPES, **changes}


def saved_account(*, expired=False):
    record = auth._token_record(tokens(), {"client_id": "oaiapp_fixture", "subject": "subject", "email": "fixture@example.com"})
    if expired:
        record["expires_at"] = 0
    user_config.save_user_config({auth.STORE_KEY: {"active": "account", "host_id": "urn:uuid:fixture", "accounts": {"account": record}}})
    return record


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Unexpected network request")
    monkeypatch.setattr(auth, "_token_request", reject)
    monkeypatch.setattr(auth, "_validate_identity", lambda token, client, nonce=None: {"sub": "subject", "email": "fixture@example.com"})
    yield
    for attempt in auth._ATTEMPTS.values():
        attempt.cancel()
    auth._ATTEMPTS.clear()


def test_provider_uses_subscription_without_api_keys():
    assert llm.normalize_provider("ChatGPT Plus/Pro") == "chatgpt"
    assert llm.provider_label("chatgpt") == "ChatGPT Plus/Pro"
    assert llm.required_secret_keys_for_provider("chatgpt", []) == ()
    assert llm.provider_supports_streaming("chatgpt")
    assert llm.provider_supports_json_mode("chatgpt")
    assert llm.fallback_models_for_provider("chatgpt") == ()
    assert reasoning_request_kwargs("chatgpt", "max") == {"reasoning_effort": "max"}
    assert reasoning_request_kwargs("chatgpt", "false") == {"reasoning_effort": "low"}
    entry = next(p for p in build_config_info()["providers"] if p["id"] == "chatgpt")
    assert not entry["has_credentials"]
    with pytest.raises(auth.ChatGPTAuthError):
        llm.create_llm_client("chatgpt", {"openai_api_key": "must-not-be-used"})


def test_saved_credentials_do_not_open_browser(monkeypatch):
    saved_account()
    monkeypatch.setattr(auth, "login", lambda *a, **k: pytest.fail("login should not run"))
    assert user_config.ensure_provider_credentials("chatgpt", "gpt-6.1-sol") == {}
    assert user_config.get_saved_provider() == "chatgpt"
    assert auth.access_token() == "access-fixture"
    assert "access-fixture" not in json.dumps(build_config_info())
    assert "refresh-fixture" not in json.dumps(auth.account_status())
    if sys.platform != "win32":  # Windows has no POSIX permission bits to check
        assert user_config.get_user_config_path().stat().st_mode & 0o777 == 0o600


def test_refresh_is_serialized_and_rotating_credentials_saved_together(monkeypatch):
    record = saved_account(expired=True)
    calls = []
    def refresh(data):
        calls.append(data)
        time.sleep(.05)
        return tokens(access_token="new-access", refresh_token="new-refresh")
    monkeypatch.setattr(auth, "_token_request", refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auth.access_token(), range(2)))
    assert results == ["new-access", "new-access"]
    assert len(calls) == 1
    assert calls[0] == {"grant_type": "refresh_token", "client_id": record["client_id"], "refresh_token": record["refresh_token"]}
    assert auth._store()["accounts"]["account"]["refresh_token"] == "new-refresh"


@pytest.mark.parametrize("replacement", [tokens(scope="openid profile email"), tokens(refresh_token=""), tokens(token_type="Basic"), tokens(expires_in=0)])
def test_bad_refresh_cannot_replace_working_registration(monkeypatch, replacement):
    original = saved_account(expired=True)
    monkeypatch.setattr(auth, "_token_request", lambda _: replacement)
    with pytest.raises(auth.ChatGPTAuthError):
        auth.access_token()
    assert auth._store()["accounts"]["account"] == original


def test_pkce_callback_and_issued_client_id(monkeypatch):
    _, attempt = auth.start_login()
    query = parse_qs(urlsplit(attempt.url).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["agent_name_hint"] == ["Kvist"]
    assert query["redirect_uri"] == [attempt.redirect_uri]
    expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected]
    assert urlsplit(attempt.redirect_uri).hostname == "127.0.0.1"
    called = []
    monkeypatch.setattr(auth, "_token_request", lambda data: called.append(data) or tokens())
    with httpx.Client(trust_env=False) as client:
        invalid = client.get(attempt.redirect_uri, params={"state": "wrong", "code": "secret-code"})
        assert invalid.status_code == 400
        assert not attempt.done.is_set()
        response = client.get(attempt.redirect_uri, params={"state": attempt.state, "code": "secret-code", "client_id": "oaiapp_issued"})
    assert response.status_code == 200
    assert attempt.result()["status"] == "connected"
    assert called[0]["client_id"] == "oaiapp_issued"
    assert called[0]["redirect_uri"] == attempt.redirect_uri
    assert called[0]["code_verifier"] == attempt.verifier
    assert auth.has_credentials()
    assert auth._store()["host_id"] == query["ext_agent_host_id"][0]


@pytest.mark.parametrize("params", [{"error": ["access_denied"]}, {"code": ["x"]}, {"code": ["x"], "client_id": ["dynamic_agent_client"]}])
def test_registration_failure_keeps_active_account(monkeypatch, params):
    saved_account()
    _, attempt = auth.start_login()
    with pytest.raises(auth.ChatGPTAuthError):
        attempt.complete(params)
    assert auth._store()["active"] == "account"


def test_returning_account_rejects_changed_client_and_identity(monkeypatch):
    saved_account()
    _, attempt = auth.start_login(account_id="account")
    query = parse_qs(urlsplit(attempt.url).query)
    assert query["client_id"] == ["oaiapp_fixture"]
    assert "agent_name_hint" not in query
    with pytest.raises(auth.ChatGPTAuthError, match="registration mismatch"):
        attempt.complete({"code": ["code"], "client_id": ["other"]})
    monkeypatch.setattr(auth, "_token_request", lambda _: tokens())
    monkeypatch.setattr(auth, "_validate_identity", lambda *a: {"sub": "wrong-account"})
    with pytest.raises(auth.ChatGPTAuthError, match="account mismatch"):
        attempt.complete({"code": ["code"]})
    assert auth._store()["active"] == "account"


def test_cancelling_during_exchange_does_not_save_tokens(monkeypatch):
    _, attempt = auth.start_login()
    def exchange(data):
        attempt.cancel()
        return tokens()
    monkeypatch.setattr(auth, "_token_request", exchange)
    with pytest.raises(auth.ChatGPTAuthError, match="cancelled"):
        attempt.complete({"code": ["code"], "client_id": ["oaiapp_fixture"]})
    assert not auth.has_credentials()


def test_real_signed_identity_rejects_bad_claims_and_signature(monkeypatch):
    # Exercise verification, not only the mocked OAuth exchange.
    monkeypatch.setattr(auth, "_validate_identity", _verify_identity)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    class Keys:
        def __init__(self, *a, **k): pass
        def get_signing_key_from_jwt(self, token): return NS(key=key.public_key())
    monkeypatch.setattr(jwt, "PyJWKClient", Keys)
    claims = {"sub": "subject", "aud": "oaiapp_fixture", "iss": auth.ISSUER, "exp": time.time() + 60, "nonce": "nonce"}
    signed = jwt.encode(claims, key, algorithm="RS256")
    assert auth._validate_identity(signed, "oaiapp_fixture", "nonce")["sub"] == "subject"
    for change in ({"aud": "wrong"}, {"iss": "wrong"}, {"exp": 1}, {"nonce": "wrong"}):
        invalid = jwt.encode({**claims, **change}, key, algorithm="RS256")
        with pytest.raises(auth.ChatGPTAuthError):
            auth._validate_identity(invalid, "oaiapp_fixture", "nonce")
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(auth.ChatGPTAuthError):
        auth._validate_identity(jwt.encode(claims, other, algorithm="RS256"), "oaiapp_fixture", "nonce")


def test_signout_clears_tokens_even_when_revocation_unavailable(monkeypatch):
    saved_account()
    def offline(*args, **kwargs): raise httpx.ConnectError("offline")
    monkeypatch.setattr(auth.httpx, "get", offline)
    result = auth.sign_out()
    assert not result["revocation_confirmed"]
    assert "not confirmed" in result["message"]
    record = auth._store()["accounts"]["account"]
    assert record["client_id"] == "oaiapp_fixture"
    assert not any(record.get(key) for key in ("access_token", "refresh_token", "id_token"))
    assert not auth.has_credentials()


def test_responses_request_preserves_tools_history_and_images():
    request = transport._request({"model": "test", "messages": [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call1", "function": {"name": "read_file", "arguments": '{"path":"a"}'}}]},
        {"role": "tool", "tool_call_id": "call1", "content": "file text"}],
        "tools": [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}],
        "tool_choice": {"type": "function", "function": {"name": "read_file"}},
        "reasoning_effort": "max", "temperature": 0.1, "max_tokens": 400, "stream": False})
    assert request["stream"] is True and request["store"] is False
    assert request["instructions"] == "system instructions"
    assert "temperature" not in request and "max_output_tokens" not in request
    assert request["input"][0]["content"][1]["type"] == "input_image"
    assert request["input"][1]["namespace"] == "kvist"
    assert request["input"][2]["type"] == "function_call_output"
    assert request["tools"][0]["type"] == "namespace"
    assert request["tool_choice"]["namespace"] == "kvist"


def fake_responses(monkeypatch, events):
    saved_account()
    calls, closed = [], []
    class Stream:
        def __enter__(self): return iter(events)
        def __exit__(self, *a): closed.append("stream")
    class Client:
        def __init__(self, **kwargs): self.responses = self
        def __enter__(self): return self
        def __exit__(self, *a): closed.append("client")
        def create(self, **kwargs): calls.append(kwargs); return Stream()
    monkeypatch.setattr(transport, "OpenAI", Client)
    return calls, closed


def test_complete_stream_returns_text_tools_and_usage(monkeypatch):
    item = NS(type="function_call", namespace="kvist", call_id="call1", name="read_file", arguments='{"path":"a"}')
    calls, closed = fake_responses(monkeypatch, [NS(type="response.output_text.delta", delta="hello"),
        NS(type="response.output_item.done", item=item),
        NS(type="response.completed", response=NS(usage=NS(input_tokens=10, output_tokens=20, total_tokens=30)))])
    response = transport.ChatGPTClient().chat.completions.create(model="test", messages=[{"role": "user", "content": "hi"}])
    assert response.choices[0].message.content == "hello"
    assert response.choices[0].message.tool_calls[0].function.arguments == '{"path":"a"}'
    assert response.usage.prompt_tokens == 10
    assert closed == ["stream", "client"]
    assert calls[0]["stream"] is True


def test_prompt_cache_key_survives_growing_history_and_client_recreation(monkeypatch):
    calls, _ = fake_responses(monkeypatch, [
        NS(type="response.output_text.delta", delta="hello"),
        NS(type="response.completed", response=NS(usage=None)),
    ])
    messages = [{"role": "system", "content": "stable instructions"}, {"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]
    transport.ChatGPTClient().create(model="test", messages=messages, tools=tools)
    messages += [{"role": "assistant", "content": "hello"}, {"role": "user", "content": "next question"}]
    list(transport.ChatGPTClient().create(model="test", messages=messages, tools=tools, stream=True))
    assert calls[0]["prompt_cache_key"] == calls[1]["prompt_cache_key"]
    assert len(calls[0]["prompt_cache_key"]) <= 64
    assert "stable instructions" not in calls[0]["prompt_cache_key"]
    assert calls[1]["input"][-1]["content"] == "next question"


@pytest.mark.parametrize("change", ["account", "model", "instructions", "tools"])
def test_prompt_cache_key_separates_accounts_and_prefixes(change):
    request = {"model": "test", "instructions": "instructions", "tools": []}
    original = transport._prompt_cache_key("account", request)
    account = "other-account" if change == "account" else "account"
    if change != "account":
        request[change] = [{"type": "function", "name": "read_file"}] if change == "tools" else "changed"
    assert transport._prompt_cache_key(account, request) != original


def test_explicit_prompt_cache_key_reaches_transport(monkeypatch):
    calls, _ = fake_responses(monkeypatch, [NS(type="response.completed", response=NS(usage=None))])
    transport.ChatGPTClient().create(model="test", messages=[], prompt_cache_key="chosen-key")
    assert calls[0]["prompt_cache_key"] == "chosen-key"


@pytest.mark.parametrize("streaming", [False, True])
def test_cached_and_reasoning_usage_survives_adapter(monkeypatch, streaming):
    fake_responses(monkeypatch, [NS(type="response.completed", response=NS(usage=NS(
        input_tokens=2048, output_tokens=120, total_tokens=2168,
        input_tokens_details=NS(cached_tokens=1024), output_tokens_details=NS(reasoning_tokens=100))))])
    response = transport.ChatGPTClient().create(model="test", messages=[], stream=streaming)
    usage = list(response)[-1].usage if streaming else response.usage
    assert usage.prompt_tokens == 2048
    assert usage.prompt_tokens_details.cached_tokens == 1024
    assert usage.completion_tokens_details.reasoning_tokens == 100
    assert usage.total_tokens == 2168


@pytest.mark.parametrize("terminal", [None, NS(type="response.incomplete", response=NS(error=None)),
    NS(type="response.failed", response=NS(error=NS(code="subscription_sharing_usage_limit_exceeded")))])
def test_partial_answer_is_not_reported_as_success(monkeypatch, terminal):
    events = [NS(type="response.output_text.delta", delta="partial")]
    if terminal: events.append(terminal)
    calls, closed = fake_responses(monkeypatch, events)
    stream = transport.ChatGPTClient().create(model="test", messages=[{"role": "user", "content": "hi"}], stream=True)
    assert next(stream).choices[0].delta.content == "partial"
    with pytest.raises(RuntimeError): list(stream)
    assert closed == ["stream", "client"]


@pytest.mark.parametrize("body", [{"error": {"code": "usage_limit_reached"}}, {"error": {"type": "usage_limit_reached"}},
    {"code": "subscription_sharing_usage_limit_exceeded"}])
def test_a_spent_subscription_allowance_over_http_names_the_usage_page(monkeypatch, body):
    saved_account()
    response = httpx.Response(429, request=httpx.Request("POST", "https://example.test/responses"))
    class Client:
        def __init__(self, **kwargs): self.responses = self
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def create(self, **kwargs): raise transport.APIStatusError("limit", response=response, body=body)
    monkeypatch.setattr(transport, "OpenAI", Client)
    with pytest.raises(RuntimeError, match="chatgpt.com/settings/usage"):
        transport.ChatGPTClient().create(model="test", messages=[{"role": "user", "content": "hi"}])


def test_another_http_error_passes_through_unchanged(monkeypatch):
    saved_account()
    response = httpx.Response(500, request=httpx.Request("POST", "https://example.test/responses"))
    class Client:
        def __init__(self, **kwargs): self.responses = self
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def create(self, **kwargs): raise transport.APIStatusError("boom", response=response, body=None)
    monkeypatch.setattr(transport, "OpenAI", Client)
    with pytest.raises(transport.APIStatusError):
        transport.ChatGPTClient().create(model="test", messages=[{"role": "user", "content": "hi"}])


def test_account_specific_catalog_does_not_reuse_another_accounts_models(monkeypatch):
    saved_account()
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs["headers"]["Authorization"])
        return httpx.Response(200, request=httpx.Request("GET", url), json={"models": [
            {"slug": "available", "visibility": "list"}, {"slug": "hidden", "visibility": "hidden"}]})
    monkeypatch.setattr(transport.httpx, "get", get)
    assert llm.fetch_available_models("chatgpt", {}) == (["available"], None)
    assert seen == ["Bearer access-fixture"]


def test_sdk_wire_uses_oauth_responses_and_refreshes_once_on_401(monkeypatch):
    from openai import OpenAI as RealOpenAI
    saved_account()
    monkeypatch.setattr(auth, '_token_request', lambda data: tokens(access_token='renewed-access'))
    requests = []
    def serve(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, json={'error': {'message': 'expired', 'type': 'authentication_error'}})
        events = [
            {'type': 'response.output_text.delta', 'delta': 'complete', 'item_id': 'msg1', 'output_index': 0, 'content_index': 0, 'sequence_number': 1},
            {'type': 'response.completed', 'sequence_number': 2, 'response': {'id': 'response1', 'object': 'response',
                'status': 'completed', 'model': 'gpt-6.1-sol', 'output': [], 'created_at': 1,
                'usage': {'input_tokens': 4, 'output_tokens': 2, 'total_tokens': 6}}},
        ]
        body = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=body)
    def factory(**kwargs):
        return RealOpenAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(serve)))
    monkeypatch.setattr(transport, 'OpenAI', factory)
    reply = transport.ChatGPTClient().create(model='gpt-6.1-sol', messages=[{'role':'system', 'content':'instructions'}, {'role':'user', 'content':'hello'}])
    assert reply.choices[0].message.content == 'complete'
    assert reply.usage.total_tokens == 6
    assert len(requests) == 2
    assert str(requests[0].url) == 'https://api.openai.com/v1/responses'
    assert requests[0].headers['authorization'] == 'Bearer access-fixture'
    assert requests[1].headers['authorization'] == 'Bearer renewed-access'
    body = json.loads(requests[1].content)
    assert body['stream'] is True and body['store'] is False
    assert body['instructions'] == 'instructions'
    assert body['prompt_cache_key'].startswith('kvist-')
    assert body['prompt_cache_key'] == json.loads(requests[0].content)['prompt_cache_key']
