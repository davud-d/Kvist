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
            return httpx.Response(200, json={"id": 4242, "login": "octocat", "name": "Octo Cat", "avatar_url": "a",
                                             "created_at": "2020-01-01T00:00:00Z"})
        return httpx.Response(404)

    return handler


def start_login(client, next_path: str = "/start") -> str:
    response = client.get(f"/auth/github/login?next={next_path}", follow_redirects=False)
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.netloc == "github.com"
    query = parse_qs(location.query)
    assert query["scope"] == ["read:user repo"]
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
    assert client.get("/api/billing/eligibility").json()["trial_eligible"]
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


def test_public_and_private_repositories_are_listed(client_factory, engine):
    seen: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/repos":
            seen.append(request)
            return httpx.Response(200, json=[
                {"full_name": "octocat/hello", "clone_url": "https://github.com/octocat/hello.git", "private": False},
                {"full_name": "octocat/secret", "clone_url": "https://github.com/octocat/secret.git", "private": True},
            ])
        return fake_github([])(request)

    client = client_factory(github, **GITHUB)
    state = start_login(client)
    client.get(f"/auth/github/callback?code=good-code&state={state}", follow_redirects=False)
    body = client.get("/api/github/repos").json()
    assert body == {"repos": [{"name": "octocat/hello", "url": "https://github.com/octocat/hello.git",
                               "description": "", "private": False},
                              {"name": "octocat/secret", "url": "https://github.com/octocat/secret.git",
                               "description": "", "private": True}], "reason": ""}
    assert seen[0].url.params["visibility"] == "all"
    assert seen[0].headers["authorization"] == "Bearer gho_test_token"
    from sqlalchemy import text

    with engine.connect() as connection:
        stored = connection.execute(text("SELECT github_token_enc FROM users")).scalar_one()
    assert stored and "gho_test_token" not in stored


def test_dev_user_has_no_repositories_to_list(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get("/api/github/repos").json() == {"repos": [], "reason": "no_token"}


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


def test_real_github_dev_checkout_checks_account_age(client_factory):
    from datetime import timedelta

    from kvist_cloud.db import session_scope, utcnow
    from kvist_cloud.models import User

    client = client_factory(fake_github([]), AUTH_DEV_LOGIN="true", **GITHUB)
    state = start_login(client)
    client.get(f"/auth/github/callback?code=good-code&state={state}", follow_redirects=False)
    with session_scope(client.app.state.session_factory) as db:
        db.get(User, 1).github_created_at = utcnow() - timedelta(days=10)
    response = client.post("/api/billing/dev/skip-checkout")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "trial_ineligible"
    with session_scope(client.app.state.session_factory) as db:
        db.get(User, 1).github_created_at = utcnow() - timedelta(days=60)
    assert client.post("/api/billing/dev/skip-checkout").status_code == 200
