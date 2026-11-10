# -*- coding: utf-8 -*-
"""The web surface by weakness class. Each test names the CWE it closes (the OWASP Top 10 / ASVS families):

sessions and cookies (CWE-287/384/613/614/1004), OAuth state (CWE-352/601), CSRF (CWE-352), CORS (CWE-942),
response headers (CWE-693/1021), path traversal (CWE-22), method tampering (CWE-650), host header injection
(CWE-644), information exposure (CWE-200/209/497), resource limits (CWE-770).

A literal "every CVE" cannot be enumerated: a CVE is a bug in somebody's version of somebody's code. What can be
checked is whether the *classes* behind them are closed here; the dependency side (known CVEs in the pinned
packages) is the job of pip-audit, run separately (see the report).
"""

from __future__ import annotations

import re
import time
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import dev_login, make_user
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import select

from kvist_cloud import auth
from kvist_cloud.config import load_settings
from kvist_cloud.db import session_scope
from kvist_cloud.models import User

SESSION = auth.SESSION_COOKIE


def signed(client, salt: str, payload) -> str:
    return URLSafeTimedSerializer(client.app.state.settings.session_secret, salt=salt).dumps(payload)


def sign_in(client) -> str:
    dev_login(client)
    return client.cookies.get(SESSION)


# -- sessions (CWE-287, CWE-384, CWE-613) ----------------------------------------------------------------


def test_the_session_cookie_is_httponly_lax_and_path_wide(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    response = client.get("/auth/github/login", follow_redirects=True)
    header = next(h for h in response.history[-1].headers.get_list("set-cookie") if h.startswith(SESSION))
    lowered = header.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/" in lowered
    assert "max-age=" in lowered and "domain=" not in lowered


def test_over_https_the_cookies_are_secure(env):
    from fastapi import Response

    settings = load_settings({**env, "PUBLIC_URL": "https://cloud.example.org"})
    response = Response()
    auth.set_session(response, settings, User(id=1, github_id=1, github_login="x", session_version=1))
    assert "secure" in response.headers["set-cookie"].lower()


def test_a_forged_oauth_cookie_cannot_be_used_as_a_session_and_vice_versa(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    as_session = signed(client, "oauth", {"u": 1, "v": 1})  # right secret, wrong purpose
    stranger = client_factory(AUTH_DEV_LOGIN="true")
    stranger.cookies.set(SESSION, as_session)
    assert stranger.get("/api/me").status_code == 401
    as_oauth = signed(client, "session", {"s": "x", "n": "/start"})
    stranger.cookies.clear()
    stranger.cookies.set(auth.OAUTH_COOKIE, as_oauth)
    assert stranger.get("/auth/github/callback", params={"code": "dev-login", "state": "x"}).status_code == 400


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x",
        "a.b.c",
        "null",
        ".",
        "..",
        "A" * 5000,
        "e30.e30.e30",
        "eyJ1IjoxfQ.AAAA.AAAA",
        "\x00",
        "'; DROP TABLE users; --",
        "../../etc/passwd",
    ],
)
def test_garbage_and_unsigned_session_cookies_are_nobody(client_factory, value):
    client = client_factory(AUTH_DEV_LOGIN="true")
    client.cookies.set(SESSION, value)
    assert client.get("/api/me").status_code == 401


def test_a_cookie_signed_with_another_secret_is_nobody(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    other = URLSafeTimedSerializer("another-secret-another-secret-another-secret", salt="session").dumps(
        {"u": 1, "v": 1}
    )
    client.cookies.set(SESSION, other)
    assert client.get("/api/me").status_code == 401


def test_an_expired_cookie_is_nobody(client_factory, monkeypatch):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: int(time.time()) - auth.SESSION_MAX_AGE - 60)
    old = signed(client, "session", {"u": 1, "v": 1})
    monkeypatch.undo()
    client.cookies.set(SESSION, old)
    assert client.get("/api/me").status_code == 401


def test_bumping_the_session_version_kills_every_old_cookie(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    assert client.get("/api/me").status_code == 200
    with session_scope(client.app.state.session_factory) as db:
        db.scalar(select(User)).session_version += 1
    assert client.get("/api/me").status_code == 401


@pytest.mark.xfail(
    strict=True,
    reason="FINDING (CWE-613): sign-out only asks the browser to forget the cookie; "
    "session_version is never bumped, so a copy of the cookie (stolen, or kept by a proxy) stays valid for "
    "30 days after the person signed out. Fix: bump user.session_version in /auth/logout.",
)
def test_a_cookie_captured_before_sign_out_is_dead_after_it(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    captured = sign_in(client)
    assert client.post("/auth/logout").status_code == 204
    thief = client_factory(AUTH_DEV_LOGIN="true")
    thief.cookies.set(SESSION, captured)
    assert thief.get("/api/me").status_code == 401


def test_a_new_sign_in_does_not_reuse_a_session_an_attacker_planted(client_factory):
    """Session fixation (CWE-384): whatever cookie the browser held, sign-in issues a fresh signed one."""
    client = client_factory(AUTH_DEV_LOGIN="true")
    login = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    oauth = client.cookies.get(auth.OAUTH_COOKIE)
    client.cookies.clear()
    cookie = f"{auth.OAUTH_COOKIE}={oauth}; {SESSION}=attacker-chosen-value"
    done = client.get(
        "/auth/github/callback",
        params={"code": "dev-login", "state": state},
        headers={"Cookie": cookie},
        follow_redirects=False,
    )
    issued = next(h for h in done.headers.get_list("set-cookie") if h.startswith(SESSION))
    assert "attacker-chosen-value" not in issued and issued.split("=", 1)[1].count(".") >= 2


# -- OAuth (CWE-352, CWE-601, CWE-755) -------------------------------------------------------------------


def planted_state_cookie(client) -> None:
    """What any visitor holds after pressing the sign-in button, without following on to the callback."""
    client.cookies.clear()
    assert client.get("/auth/github/login", follow_redirects=False).status_code == 302


@pytest.mark.xfail(
    strict=True,
    reason="FINDING (low, CWE-755): secrets.compare_digest(str, str) raises TypeError on a "
    "non-ASCII `state`, so any visitor can make the callback answer 500 with one crafted URL. "
    "Fix: compare .encode() bytes (as billing.callback_authentic already does).",
)
@pytest.mark.parametrize("state", ["é", "ÿ", "😀", "日本"])
def test_a_non_ascii_state_is_a_clean_refusal(client_factory, state):
    client = client_factory(AUTH_DEV_LOGIN="true")
    planted_state_cookie(client)
    response = client.get("/auth/github/callback", params={"code": "dev-login", "state": state})
    assert response.status_code == 400


@pytest.mark.parametrize("state", ["\u0000", "a" * 10_000, "guess", ""])
def test_a_wrong_or_odd_state_is_a_clean_refusal_and_signs_nobody_in(client_factory, state):
    client = client_factory(AUTH_DEV_LOGIN="true")
    planted_state_cookie(client)
    response = client.get("/auth/github/callback", params={"code": "dev-login", "state": state})
    assert response.status_code == 400
    assert client.get("/api/me").status_code == 401


def test_the_callback_needs_the_code_as_well_as_the_state(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    login = client.get("/auth/github/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    assert client.get("/auth/github/callback", params={"state": state}).status_code == 400
    assert client.get("/auth/github/callback", params={"code": "", "state": state}).status_code == 400
    assert client.get("/api/me").status_code == 401


def test_a_used_state_cookie_is_gone_after_a_successful_sign_in(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.cookies.get(auth.OAUTH_COOKIE) is None


@pytest.mark.parametrize(
    "target",
    [
        "//evil.example",
        "///evil.example",
        "https://evil.example",
        "http://evil.example",
        "/\\evil.example",
        "\\\\evil.example",
        "/\t/evil.example",
        "/\n/evil.example",
        "javascript:alert(1)",
        "data:text/html,x",
        "evil.example",
        "",
        " /x",
        "\r\n/x",
        "/%0d%0aSet-Cookie:x=1",
        "//\\evil.example",
    ],
)
def test_next_never_leaves_the_site(client_factory, target):
    client = client_factory(AUTH_DEV_LOGIN="true")
    response = client.get("/auth/github/login", params={"next": target}, follow_redirects=False)
    final = client.get(response.headers["location"], follow_redirects=False)
    location = final.headers["location"]
    assert urlparse(location).netloc == "" and not location.startswith("//") and "\\" not in location
    assert "\n" not in location and "\r" not in location


def test_the_github_redirect_uses_the_configured_address_not_the_hosts_header(client_factory):
    client = client_factory(GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="csecret")
    for header in ({"host": "evil.example"}, {"x-forwarded-host": "evil.example"}, {"forwarded": "host=evil.example"}):
        response = client.get("/auth/github/login", headers=header, follow_redirects=False)
        location = response.headers["location"]
        assert "evil.example" not in location
        assert parse_qs(urlparse(location).query)["redirect_uri"] == ["http://127.0.0.1:8740/auth/github/callback"]


# -- CSRF and CORS (CWE-352, CWE-942) --------------------------------------------------------------------


def api_writes(app):
    found = [
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in item
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}
    ]
    assert len(found) >= 25, "the API routes were not found"
    return found


def test_every_state_changing_api_route_refuses_a_request_without_the_client_header(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    sign_in(client)
    client.headers.pop("X-Kvist-Client", None)
    for method, path in api_writes(client.app):
        concrete = re.sub(r"\{[a-z_]+\}", "1", path)
        for header in (
            {},
            {"X-Kvist-Client": ""},
            {"X-Kvist-Client": "0"},
            {"X-Kvist-Client": "true"},
            {"X-Kvist-Client": "11"},
        ):
            response = client.request(method, concrete, headers=header, json={})
            assert response.status_code == 403 and response.json()["error"]["code"] == "csrf", (method, path, header)


def test_a_form_post_from_another_site_cannot_ride_the_cookie(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    client.headers.pop("X-Kvist-Client")
    response = client.post(
        "/api/billing/cancel",
        data={"x": "1"},
        headers={"Origin": "https://evil.example", "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 403
    response = client.post(
        "/api/billing/cancel", content="{}", headers={"Origin": "https://evil.example", "Content-Type": "text/plain"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1.evil.example"])
def test_no_origin_is_ever_granted_cross_origin_access(client_factory, origin):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    get = client.get("/api/me", headers={"Origin": origin})
    preflight = client.options(
        "/api/billing/cancel",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-kvist-client",
        },
    )
    for response in (get, preflight):
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("method", ["TRACE", "CONNECT", "PROPFIND", "FOO"])
def test_unusual_methods_are_not_served(client_factory, method):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    assert client.request(method, "/api/me").status_code in {400, 404, 405, 501}


def test_a_get_never_changes_money_or_rights(client_factory):
    """Safe methods are exempt from the CSRF header, so none of them may write anything."""
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    sign_in(client)
    with session_scope(client.app.state.session_factory) as db:
        before = [(u.id, u.is_admin, u.trial_used_at, u.session_version) for u in db.scalars(select(User))]
    reads = [path for path, item in client.app.openapi()["paths"].items() if "get" in item and "{" not in path]
    for path in reads:
        client.get(path)
    with session_scope(client.app.state.session_factory) as db:
        assert before == [(u.id, u.is_admin, u.trial_used_at, u.session_version) for u in db.scalars(select(User))]
    from kvist_cloud.models import Subscription

    with session_scope(client.app.state.session_factory) as db:
        assert db.scalar(select(Subscription.id)) is None


# -- headers and exposure (CWE-693, CWE-1021, CWE-200, CWE-209) -----------------------------------------


def test_the_policy_headers_are_on_every_kind_of_answer(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    answers = [
        client.get("/api/me"),
        client.get("/api/nope"),
        client.get("/nope"),
        client.post("/api/me"),
        client.get("/api/admin/users"),
        client.get("/"),
    ]
    for response in answers:
        assert response.headers["x-content-type-options"] == "nosniff", response.url
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"], response.url
        assert response.headers["referrer-policy"] == "same-origin", response.url


def test_the_content_security_policy_has_no_loophole_for_scripts(client_factory):
    policy = client_factory().get("/").headers["content-security-policy"]
    directives = dict(part.strip().split(" ", 1) for part in policy.split(";") if " " in part.strip())
    assert directives["script-src"] == "'self'"
    assert "'unsafe-eval'" not in policy and "'unsafe-inline'" not in directives["script-src"]
    assert directives["default-src"] == "'self'" and directives["base-uri"] == "'none'"
    assert "*" not in policy.replace("*.", "x")


@pytest.mark.parametrize(
    "path",
    [
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
        "/.env",
        "/.git/config",
        "/api/openapi.json",
        "/server-status",
        "/metrics",
        "/debug",
        "/admin/api",
    ],
)
def test_nothing_that_maps_the_server_is_served(client_factory, path):
    response = client_factory(AUTH_DEV_LOGIN="true").get(path)
    assert response.status_code in {303, 302, 401, 403, 404}
    assert "openapi" not in response.text.lower() and "swagger" not in response.text.lower()


def test_errors_carry_a_code_to_quote_and_never_a_trace(app_factory, monkeypatch):
    from fastapi.testclient import TestClient

    from kvist_cloud import billing

    def boom(*_args, **_kwargs):
        raise RuntimeError("SENTINEL-internal-detail /srv/secret/path.py")

    monkeypatch.setattr(billing, "subscription_view", boom)
    with TestClient(
        app_factory(AUTH_DEV_LOGIN="true"), base_url="http://127.0.0.1:8740", raise_server_exceptions=False
    ) as client:
        client.headers["X-Kvist-Client"] = "1"
        dev_login(client)
        response = client.get("/api/billing/subscription")
    assert response.status_code == 500
    assert "SENTINEL" not in response.text and "Traceback" not in response.text and ".py" not in response.text
    assert response.json()["error"]["support_code"].startswith("K-")


def test_a_wrong_login_and_a_missing_user_look_the_same(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true", ADMIN_GITHUB_LOGINS="dev")
    sign_in(client)
    with session_scope(client.app.state.session_factory) as db:
        make_user(db, "alice", 5)
    plain = client_factory(AUTH_DEV_LOGIN="true")
    # An outsider learns nothing about which ids exist: the route answers by rights first, existence never.
    assert plain.get("/api/admin/users/2").status_code == plain.get("/api/admin/users/99999").status_code == 401


# -- path traversal and static files (CWE-22, CWE-23) ----------------------------------------------------


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "pixel").mkdir()
    (folder / "app.en.html").write_text("<!doctype html><title>app</title>")
    (folder / "pricing.en.html").write_text("<!doctype html><title>pricing</title>")
    (folder / "assets" / "app.js").write_text("export {};")
    (folder / "pixel" / "index.html").write_text("<!doctype html><title>pixel</title>")
    (tmp_path / "secret.txt").write_text("TOP-SECRET-OUTSIDE-DIST")
    (tmp_path / "data").mkdir(exist_ok=True)
    return folder


TRAVERSALS = [
    "/assets/../../secret.txt",
    "/assets/%2e%2e/%2e%2e/secret.txt",
    "/assets/%2e%2e%2f%2e%2e%2fsecret.txt",
    "/assets/..%2f..%2fsecret.txt",
    "/assets/..%5c..%5csecret.txt",
    "/assets/%252e%252e/secret.txt",
    "/assets/....//....//secret.txt",
    "/assets/%c0%ae%c0%ae/secret.txt",
    "/assets/\x00/../secret.txt",
    "/pixel/../../secret.txt",
    "/pixel/%2e%2e/%2e%2e/secret.txt",
    "/pixel/s/..%2f..%2f..%2fsecret.txt",
    "/pixel/p/%2e%2e%2f%2e%2e%2fsecret.txt",
    "/assets//etc/passwd",
    "/assets/%2fetc%2fpasswd",
    "/assets/..;/..;/secret.txt",
    "/assets/.%00./secret.txt",
]


@pytest.mark.parametrize("path", TRAVERSALS)
def test_no_path_reaches_a_file_outside_the_build(client_factory, dist, path):
    client = client_factory(FRONTEND_DIST=str(dist), AUTH_DEV_LOGIN="true")
    try:
        response = client.request("GET", path)
    except Exception as error:  # noqa: BLE001 - a client that cannot even build the URL is also safe
        pytest.skip(f"client refused the URL: {type(error).__name__}")
    assert "TOP-SECRET" not in response.text and "root:" not in response.text
    assert response.status_code < 500, response.status_code


def test_the_database_and_the_sources_are_not_under_the_web_root(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    for path in (
        "/assets/cloud.db",
        "/cloud.db",
        "/data/cloud.db",
        "/kvist_cloud/auth.py",
        "/pyproject.toml",
        "/assets/../cloud.db",
    ):
        response = client.get(path)
        assert response.status_code in {303, 302, 404}, path
        assert b"SQLite format" not in response.content


# -- resource limits (CWE-770, CWE-400) ------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="FINDING (CWE-770): only the payment callback limits its body. Every other JSON "
    "endpoint reads whatever size arrives, so one signed-in person can make the app buffer and parse "
    "tens of megabytes per request unless a reverse proxy caps it. Fix: a body-size middleware.",
)
def test_a_huge_body_is_refused_before_it_is_parsed(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    huge = b'{"locale": "' + b"a" * (12 * 1024 * 1024) + b'"}'
    assert client.patch("/api/me", content=huge, headers={"Content-Type": "application/json"}).status_code == 413


def test_a_deeply_nested_body_is_a_client_error_on_the_ordinary_endpoints(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    sign_in(client)
    nested = b"[" * 50_000 + b"]" * 50_000
    assert client.patch("/api/me", content=nested, headers={"Content-Type": "application/json"}).status_code in {
        400,
        413,
        422,
    }
