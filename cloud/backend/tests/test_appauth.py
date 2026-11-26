# -*- coding: utf-8 -*-
"""Signing in from the phone apps: the system browser signs in, the app's web view gets the session by a code."""
from __future__ import annotations

from urllib.parse import parse_qs, quote, urlparse

import pytest
from conftest import dev_login

from kvist_cloud.auth import IDE_SESSION_COOKIE, SESSION_COOKIE

NONCE = "n" * 40


def code_from(response) -> str:
    location = response.headers["location"]
    assert location.startswith("kvistcloud://signed-in?code=")
    return parse_qs(urlparse(location).query)["code"][0]


def test_a_browser_that_is_not_signed_in_goes_through_github_and_comes_back_here(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    response = client.get(f"/auth/app/start?nonce={NONCE}", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"/auth/github/login?next={quote(f'/auth/app/start?nonce={NONCE}', safe='')}"


def test_after_signing_in_the_app_is_handed_a_code_and_the_web_view_trades_it_for_a_session(client_factory):
    browser = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(browser)
    code = code_from(browser.get(f"/auth/app/start?nonce={NONCE}", follow_redirects=False))
    # The app's web view has no cookies of its own: another client, as it would be.
    web_view = client_factory(AUTH_DEV_LOGIN="true")
    assert web_view.get("/api/me").status_code == 401
    response = web_view.get(f"/auth/app/exchange?code={code}&nonce={NONCE}", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/m/"
    assert SESSION_COOKIE in response.cookies
    assert web_view.get("/api/me").json()["login"] == "dev"
    assert web_view.get("/api/me").json()["created_at"]


def test_a_code_without_its_nonce_is_worth_nothing(client_factory):
    browser = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(browser)
    code = code_from(browser.get(f"/auth/app/start?nonce={NONCE}", follow_redirects=False))
    web_view = client_factory(AUTH_DEV_LOGIN="true")
    for nonce in ("m" * 40, "short"):
        assert web_view.get(f"/auth/app/exchange?code={code}&nonce={nonce}").status_code == 400
    assert web_view.get("/api/me").status_code == 401


@pytest.mark.parametrize("code", ["", "garbage", "a.b.c"])
def test_a_forged_code_is_refused(client_factory, code):
    client = client_factory(AUTH_DEV_LOGIN="true")
    assert client.get(f"/auth/app/exchange?code={code}&nonce={NONCE}").status_code == 400


def test_an_expired_code_is_refused(client_factory, monkeypatch):
    from kvist_cloud import appauth

    browser = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(browser)
    code = code_from(browser.get(f"/auth/app/start?nonce={NONCE}", follow_redirects=False))
    monkeypatch.setattr(appauth, "CODE_MAX_AGE", -1)
    assert client_factory(AUTH_DEV_LOGIN="true").get(f"/auth/app/exchange?code={code}&nonce={NONCE}").status_code == 400


def test_the_return_address_stays_on_this_site(client_factory):
    browser = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(browser)
    code = code_from(browser.get(f"/auth/app/start?nonce={NONCE}", follow_redirects=False))
    web_view = client_factory(AUTH_DEV_LOGIN="true")
    response = web_view.get(f"/auth/app/exchange?code={code}&nonce={NONCE}&next=//evil.example", follow_redirects=False)
    assert response.headers["location"] == "/m/"


def test_a_request_that_is_not_the_apps_is_refused(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    assert client.get("/auth/app/start").status_code == 400
    assert client.get("/auth/app/start?nonce=%0d%0aSet-Cookie:x").status_code == 400


# -- the IDE panels: the code goes to the plugin's loopback listener, the framed page gets the IDE's cookie ----------


def test_an_ide_is_handed_the_code_on_its_loopback_port_even_after_github(client_factory):
    client = client_factory(AUTH_DEV_LOGIN="true")
    response = client.get(f"/auth/app/start?nonce={NONCE}&port=43210", follow_redirects=False)
    assert "port%3D43210" in response.headers["location"]
    dev_login(client)
    response = client.get(f"/auth/app/start?nonce={NONCE}&port=43210", follow_redirects=False)
    assert response.headers["location"].startswith("http://127.0.0.1:43210/signed-in?code=")


@pytest.mark.parametrize("port", ["80", "70000", "1e4", "-1", "43210/evil"])
def test_an_ide_port_outside_the_user_range_is_refused(client_factory, port):
    client = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(client)
    assert client.get(f"/auth/app/start?nonce={NONCE}&port={quote(port)}", follow_redirects=False).status_code == 400


def test_the_framed_page_gets_a_partitioned_cookie_that_the_api_accepts(client_factory):
    browser = client_factory(AUTH_DEV_LOGIN="true")
    dev_login(browser)
    location = browser.get(f"/auth/app/start?nonce={NONCE}&port=43210", follow_redirects=False).headers["location"]
    code = parse_qs(urlparse(location).query)["code"][0]
    frame = client_factory(AUTH_DEV_LOGIN="true")
    response = frame.get(f"/auth/app/exchange?code={code}&nonce={NONCE}", follow_redirects=False)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{IDE_SESSION_COOKIE}=") and SESSION_COOKIE + "=" not in cookie
    assert "SameSite=None" in cookie and "Secure" in cookie and "Partitioned" in cookie and "HttpOnly" in cookie
    # The test client is http, where a Secure cookie is not sent back by itself; a browser on https sends it.
    frame.cookies.set(IDE_SESSION_COOKIE, response.cookies[IDE_SESSION_COOKIE])
    assert frame.get("/api/me").json()["login"] == "dev"
    # Signing out ends it too.
    assert frame.post("/auth/logout", headers={"X-Kvist-Client": "1"}).headers.get_list("set-cookie")[1].startswith(
        f'{IDE_SESSION_COOKIE}=""')
