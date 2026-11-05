# -*- coding: utf-8 -*-
"""GitHub OAuth with GitHub mocked, the session cookie, dev sign-in and CSRF."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from conftest import dev_login

from kvist_cloud.auth import safe_next
from kvist_cloud.config import ConfigError, load_settings

GITHUB = {"GITHUB_CLIENT_ID": "client-id", "GITHUB_CLIENT_SECRET": "client-secret-value"}


def fake_github(seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/login/oauth/access_token":
            if b"code=good-code" not in request.content:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(200, json={"access_token": "gho_test_token", "token_type": "bearer"})
        if request.url.path == "/user":
            assert request.headers["authorization"] == "Bearer gho_test_token"
            return httpx.Response(200, json={"id": 4242, "login": "octocat", "name": "Octo Cat", "avatar_url": "a"})
        return httpx.Response(404)

    return handler


def start_login(client, next_path: str = "/start") -> str:
    response = client.get(f"/auth/github/login?next={next_path}", follow_redirects=False)
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.netloc == "github.com"
    query = parse_qs(location.query)
    assert query["scope"] == ["read:user"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8740/auth/github/callback"]
    return query["state"][0]


def test_github_login_round_trip(client_factory):
    seen: list[httpx.Request] = []
    client = client_factory(fake_github(seen), **GITHUB)
    state = start_login(client, "/start")
    response = client.get(f"/auth/github/callback?code=good-code&state={state}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/start"
    assert "kc_session" in response.cookies
    me = client.get("/api/me").json()
    assert (me["login"], me["name"]) == ("octocat", "Octo Cat")
    assert [request.url.path for request in seen] == ["/login/oauth/access_token", "/user"]


def test_second_login_updates_the_same_user(client_factory):
    client = client_factory(fake_github([]), **GITHUB)
    for _ in range(2):
        state = start_login(client)
        client.get(f"/auth/github/callback?code=good-code&state={state}", follow_redirects=False)
    assert client.get("/api/me").json()["id"] == 1


def test_wrong_state_is_refused(client_factory):
    client = client_factory(fake_github([]), **GITHUB)
    start_login(client)
    response = client.get("/auth/github/callback?code=good-code&state=forged", follow_redirects=False)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "oauth_state"
    assert client.get("/api/me").status_code == 401


def test_callback_without_the_state_cookie_is_refused(client_factory):
    client = client_factory(fake_github([]), **GITHUB)
    response = client.get("/auth/github/callback?code=good-code&state=x", follow_redirects=False)
    assert response.status_code == 400


def test_rejected_code_is_a_readable_error(client_factory):
    client = client_factory(fake_github([]), **GITHUB)
    state = start_login(client)
    response = client.get(f"/auth/github/callback?code=bad-code&state={state}", follow_redirects=False)
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "oauth_failed"
    assert "Traceback" not in response.text


def test_github_down_is_a_readable_error(client_factory):
    def down(_request):
        raise httpx.ConnectError("no route")

    client = client_factory(down, **GITHUB)
    state = start_login(client)
    response = client.get(f"/auth/github/callback?code=good-code&state={state}", follow_redirects=False)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "github_unreachable"


def test_tampered_session_cookie_is_ignored(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    client.cookies.set("kc_session", client.cookies.get("kc_session") + "x")
    assert client.get("/api/me").status_code == 401


def test_logout_clears_the_session(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/me").status_code == 200
    client.post("/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_dev_login_needs_no_network(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/me").json()["login"] == "dev"


def test_locale_is_saved(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.patch("/api/me", json={"locale": "ru"}).json()["locale"] == "ru"
    assert client.patch("/api/me", json={"locale": "de"}).status_code == 422


def test_dev_login_is_refused_off_loopback(env):
    with pytest.raises(ConfigError):
        load_settings({**env, "AUTH_DEV_LOGIN": "true", "PUBLIC_URL": "https://cloud.example"})


def test_no_github_and_no_dev_login_is_a_clear_error(client_factory):
    client = client_factory()
    response = client.get("/auth/github/login", follow_redirects=False)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_not_configured"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("/tasks/1", "/tasks/1"), ("//evil.example", "/start"), ("https://evil.example", "/start"),
     ("/\\evil", "/start"), (None, "/start")],
)
def test_next_is_same_site_only(raw, expected):
    assert safe_next(raw) == expected


def test_api_writes_need_the_client_header(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    response = client.post("/api/anything", headers={"X-Kvist-Client": ""})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf"
