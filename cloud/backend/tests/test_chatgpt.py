# -*- coding: utf-8 -*-
"""Sign in with ChatGPT on the server, against a stand-in for OpenAI."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import timedelta
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
from conftest import MASTER_KEY, dev_login, eventually, make_machine, make_subscription, make_task, make_user
from kernel.providers.chatgpt_endpoints import ISSUER, PLAN_SCOPE, TOKEN_ENDPOINT

from kvist_cloud import chatgpt, gateway, tasks
from kvist_cloud.db import make_session_factory, session_scope, utcnow
from kvist_cloud.errors import ApiError
from kvist_cloud.models import ChatGPTLogin, Task, TaskStatus, User
from kvist_cloud.secrets import Vault

CLIENT_ID = "client-made-for-this-host"


def _jwt(claims: dict) -> str:
    def part(value: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.x"


class FakeOpenAI:
    """The token endpoint, with the knobs the tests turn."""

    def __init__(self) -> None:
        self.nonce = ""
        self.scope = f"openid offline_access {PLAN_SCOPE}"
        self.refused = False
        self.down = False
        self.issued = 0
        self.valid_refresh = ""
        self.requests: list[dict[str, str]] = []
        self.revoked = False

    def tokens(self, nonce: str | None, subject: str = "sub-1") -> dict:
        self.issued += 1
        self.valid_refresh = f"cgpt-refresh-{self.issued}"
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": subject, "email": "me@example.com", "exp": time.time() + 600}
        if nonce is not None:
            claims["nonce"] = nonce
        return {
            "access_token": f"cgpt-access-{self.issued}", "refresh_token": self.valid_refresh, "token_type": "Bearer",
            "expires_in": 3600, "scope": self.scope, "id_token": _jwt(claims),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"revocation_endpoint": ISSUER + "/oauth/revoke"})
        if url == ISSUER + "/oauth/revoke":
            self.revoked = True
            return httpx.Response(200)
        assert url == TOKEN_ENDPOINT, url
        data = dict(parse_qsl(request.content.decode()))
        self.requests.append(data)
        if self.down:
            return httpx.Response(503)
        if self.refused:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if data["grant_type"] == "authorization_code":
            return httpx.Response(200, json=self.tokens(self.nonce))
        if data["refresh_token"] != self.valid_refresh:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=self.tokens(None))


@pytest.fixture
def openai() -> FakeOpenAI:
    return FakeOpenAI()


@pytest.fixture
def client(client_factory, openai):
    client = client_factory(openai, AUTH_DEV_LOGIN="true")
    dev_login(client)
    return client


def begin(client, openai: FakeOpenAI) -> dict[str, str]:
    url = client.post("/api/chatgpt/start", json={}).json()["url"]
    query = dict(parse_qsl(urlsplit(url).query))
    openai.nonce = query["nonce"]
    return query


def finish(client, state: str, **extra: str):
    params = {"code": "one-time-code", "state": state, "client_id": CLIENT_ID, **extra}
    return client.get("/auth/chatgpt/callback", params=params, follow_redirects=False)


def connect(client, openai: FakeOpenAI) -> None:
    response = finish(client, begin(client, openai)["state"])
    assert response.headers["location"] == "/onboarding?chatgpt=ok"


def run(client, function, *args, **kwargs):
    async def call():
        return await function(client.app, *args, **kwargs)

    return client.portal.call(call)


def user_row(client) -> User:
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        db.expunge(user)
        return user


def test_the_customer_is_sent_to_openai_with_a_callback_on_this_server(client, openai):
    query = begin(client, openai)
    assert query["redirect_uri"] == "http://127.0.0.1:8740/auth/chatgpt/callback"
    assert query["code_challenge_method"] == "S256" and query["response_type"] == "code"
    assert PLAN_SCOPE in query["scope"] and query["client_id"] == "dynamic_agent_client"


def test_signing_in_stores_the_tokens_encrypted_and_the_state_only_as_a_hash(client, openai):
    query = begin(client, openai)
    response = finish(client, query["state"])
    assert (response.status_code, response.headers["location"]) == (302, "/onboarding?chatgpt=ok")
    sent = openai.requests[0]
    assert sent["grant_type"] == "authorization_code" and sent["client_id"] == CLIENT_ID
    assert sent["redirect_uri"] == query["redirect_uri"] and sent["code_verifier"]
    status = client.get("/api/chatgpt").json()
    assert (status["state"], status["email"]) == ("connected", "me@example.com")
    user = user_row(client)
    assert "cgpt-access-1" not in user.chatgpt_enc and "cgpt-refresh-1" not in user.chatgpt_enc
    with session_scope(client.app.state.session_factory) as db:
        assert db.query(ChatGPTLogin).count() == 0


def test_an_attempt_works_once(client, openai):
    state = begin(client, openai)["state"]
    assert finish(client, state).headers["location"].endswith("chatgpt=ok")
    assert finish(client, state).headers["location"].endswith("chatgpt=expired")
    assert len(openai.requests) == 1


def test_an_unknown_state_is_refused_without_calling_openai(client, openai):
    begin(client, openai)
    assert finish(client, "made-up").headers["location"].endswith("chatgpt=expired")
    assert openai.requests == []


def test_an_attempt_older_than_three_minutes_is_refused(client, openai):
    state = begin(client, openai)["state"]
    with session_scope(client.app.state.session_factory) as db:
        db.query(ChatGPTLogin).one().expires_at = utcnow() - timedelta(seconds=1)
    assert finish(client, state).headers["location"].endswith("chatgpt=expired")
    assert openai.requests == []


def test_the_attempt_is_only_stored_as_a_hash(client, openai):
    state = begin(client, openai)["state"]
    with session_scope(client.app.state.session_factory) as db:
        row = db.query(ChatGPTLogin).one()
        assert state not in (row.state_hash, row.verifier_enc)
        assert len(row.state_hash) == 64


def test_a_declined_sign_in_connects_nothing(client, openai):
    state = begin(client, openai)["state"]
    response = client.get("/auth/chatgpt/callback", params={"error": "access_denied", "state": state},
                          follow_redirects=False)
    assert response.headers["location"].endswith("chatgpt=denied")
    assert client.get("/api/chatgpt").json()["state"] == ""


@pytest.mark.parametrize(
    ("change", "reason"),
    [("scope", "no_plan"), ("refused", "refused"), ("nonce", "identity")],
)
def test_a_sign_in_that_cannot_be_trusted_is_not_saved(client, openai, change, reason):
    state = begin(client, openai)["state"]
    if change == "scope":
        openai.scope = "openid offline_access"
    elif change == "refused":
        openai.refused = True
    else:
        openai.nonce = "someone-elses-nonce"
    assert finish(client, state).headers["location"].endswith(f"chatgpt={reason}")
    assert client.get("/api/chatgpt").json()["state"] == ""


def test_a_callback_without_a_session_goes_home(client_factory, openai):
    client = client_factory(openai, AUTH_DEV_LOGIN="true")
    response = client.get("/auth/chatgpt/callback", params={"state": "x", "code": "y"}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_a_token_with_time_left_is_handed_out_without_asking_openai(client, openai):
    connect(client, openai)
    assert run(client, chatgpt.fresh_access_token, 1) == "cgpt-access-1"
    assert len(openai.requests) == 1


def test_renewal_replaces_the_refresh_token_and_keeps_the_new_one(client, openai):
    connect(client, openai)
    assert run(client, chatgpt.fresh_access_token, 1, force=True) == "cgpt-access-2"
    assert openai.requests[-1]["refresh_token"] == "cgpt-refresh-1"
    assert run(client, chatgpt.fresh_access_token, 1, force=True) == "cgpt-access-3"
    assert openai.requests[-1]["refresh_token"] == "cgpt-refresh-2"
    assert client.get("/api/chatgpt").json()["state"] == "connected"


def test_a_token_about_to_run_out_is_renewed_before_it_is_handed_out(client, openai):
    connect(client, openai)
    vault = client.app.state.vault
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        record = json.loads(vault.decrypt(user.chatgpt_enc))
        record["expires_at"] = time.time() + 30
        user.chatgpt_enc = vault.encrypt(json.dumps(record))
    assert run(client, chatgpt.fresh_access_token, 1) == "cgpt-access-2"


def test_a_refused_renewal_asks_the_customer_to_sign_in_again(client, openai):
    connect(client, openai)
    openai.refused = True
    with pytest.raises(chatgpt.ChatGPTError) as error:
        run(client, chatgpt.fresh_access_token, 1, force=True)
    assert error.value.code == "refused"
    assert client.get("/api/chatgpt").json()["state"] == "needs_login"
    with pytest.raises(chatgpt.ChatGPTError) as again:
        run(client, chatgpt.fresh_access_token, 1)
    assert again.value.code == "needs_login"


def test_openai_being_down_does_not_sign_the_customer_out(client, openai):
    connect(client, openai)
    openai.down = True
    with pytest.raises(chatgpt.ChatGPTError) as error:
        run(client, chatgpt.fresh_access_token, 1, force=True)
    assert error.value.code == "unreachable"
    assert client.get("/api/chatgpt").json()["state"] == "connected"


def test_the_daily_check_uses_every_saved_sign_in_once(client, openai):
    connect(client, openai)
    before = len(openai.requests)
    run(client, chatgpt.check_all)
    assert len(openai.requests) == before + 1
    assert openai.requests[-1]["grant_type"] == "refresh_token"
    openai.refused = True
    run(client, chatgpt.check_all)
    assert client.get("/api/chatgpt").json()["state"] == "needs_login"
    # A sign-in that already needs the customer is not tried again.
    count = len(openai.requests)
    run(client, chatgpt.check_all)
    assert len(openai.requests) == count


def test_disconnecting_forgets_the_tokens_and_tells_openai(client, openai):
    connect(client, openai)
    result = client.delete("/api/chatgpt").json()
    assert (result["state"], result["revoked"]) == ("", True)
    assert openai.revoked
    assert user_row(client).chatgpt_enc == ""


# -- what the machine is given ---------------------------------------------------------------

TOKEN = "machine-token-for-tests-0123456789"


def seed_customer(engine, *, state: str = "connected", expires_in: float = 3600.0) -> int:
    """A ChatGPT customer with a machine and a task waiting to start; returns the task id."""
    record = {
        "client_id": CLIENT_ID, "subject": "sub-1", "email": "me@example.com", "access_token": "cgpt-access-0",
        "refresh_token": "cgpt-refresh-0", "expires_at": time.time() + expires_in, "scopes": ["openid", PLAN_SCOPE],
    }
    with session_scope(make_session_factory(engine)) as db:
        user = make_user(db)
        user.model_provider = "chatgpt"
        user.chatgpt_enc = Vault(MASTER_KEY).encrypt(json.dumps(record))
        user.chatgpt_state = state
        make_subscription(db, user, "trial")
        machine = make_machine(db, user)
        machine.token_hash = hashlib.sha256(TOKEN.encode()).hexdigest()
        return make_task(db, user, machine, status=TaskStatus.STARTING).id


def connect_runner(client):
    return client.websocket_connect("/gateway/ws", headers={"Authorization": f"Bearer {TOKEN}"})


def test_a_chatgpt_task_starts_with_the_access_token_and_no_key(client_factory, engine, openai):
    task_id = seed_customer(engine)
    client = client_factory(openai)
    with connect_runner(client) as ws:
        ws.send_json({"type": "hello", "runner_version": "test", "tasks": []})
        start = ws.receive_json()
    assert (start["type"], start["task_id"]) == ("start_task", task_id)
    assert start["env"] == {"AGENT_LLM_PROVIDER": "chatgpt"}
    assert start["chatgpt"]["access_token"] == "cgpt-access-0" and PLAN_SCOPE in start["chatgpt"]["scopes"]
    # The refresh token never leaves the server.
    assert "refresh" not in json.dumps(start)


def test_a_lost_sign_in_pauses_the_task_instead_of_failing_it(client_factory, engine, openai):
    task_id = seed_customer(engine, state="needs_login")
    client = client_factory(openai)
    with connect_runner(client) as ws:
        ws.send_json({"type": "hello", "runner_version": "test", "tasks": []})
        eventually(lambda: _status(engine, task_id) != TaskStatus.STARTING)
    assert _status(engine, task_id) == TaskStatus.PAUSED


def _status(engine, task_id: int) -> str:
    with session_scope(make_session_factory(engine)) as db:
        return db.get(Task, task_id).status


def test_a_renewed_token_is_pushed_to_the_machine_once(client_factory, engine, openai):
    seed_customer(engine, expires_in=60)  # about to run out
    openai.valid_refresh = "cgpt-refresh-0"
    client = client_factory(openai)
    with connect_runner(client) as ws:
        ws.send_json({"type": "hello", "runner_version": "test", "tasks": []})
        ws.receive_json()  # start_task
        eventually(lambda: client.app.state.gateway.connected(1))
        run(client, gateway.push_chatgpt_tokens)
        pushed = ws.receive_json()
        assert pushed["type"] == "chatgpt_access" and pushed["access_token"] == "cgpt-access-1"
        assert "refresh" not in json.dumps(pushed)
        before = len(openai.requests)
        run(client, gateway.push_chatgpt_tokens)
        # Nothing changed, nothing sent, and OpenAI was not asked either.
        assert client.app.state.gateway.lent[1] == "cgpt-access-1" and len(openai.requests) == before


def test_signing_in_makes_chatgpt_the_model_and_drops_the_key(client, openai):
    with session_scope(client.app.state.session_factory) as db:
        user = db.get(User, 1)
        user.model_provider, user.model_key_enc, user.model_key_last4 = "deepseek", "x", "abcd"
    connect(client, openai)
    summary = client.get("/api/model-key").json()
    assert (summary["provider"], summary["set"], summary["last4"]) == ("chatgpt", True, "")
    assert (summary["chatgpt_state"], summary["chatgpt_email"]) == ("connected", "me@example.com")
    client.delete("/api/chatgpt")
    assert client.get("/api/model-key").json()["provider"] == ""


def test_the_key_endpoint_does_not_take_chatgpt(client):
    response = client.post("/api/model-key", json={"provider": "chatgpt", "key": "cgpt-not-a-key-0123"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "sign_in_required"


@pytest.mark.parametrize(("state", "code"), [("connected", None), ("needs_login", "chatgpt_login")])
def test_a_task_needs_a_working_sign_in(engine, state, code):
    seed_customer(engine, state=state)
    with session_scope(make_session_factory(engine)) as db:
        user = db.get(User, 1)
        if code is None:
            tasks.can_run(db, user)
            return
        with pytest.raises(ApiError) as refused:
            tasks.can_run(db, user)
        assert refused.value.code == code
