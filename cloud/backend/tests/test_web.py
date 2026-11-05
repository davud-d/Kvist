# -*- coding: utf-8 -*-
"""Serving the built frontend: locale choice, sign-in redirects, security headers."""
from __future__ import annotations

import pytest
from conftest import dev_login


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    (folder / "assets" / "fonts").mkdir(parents=True)
    for name in ("pricing.en", "pricing.ru", "app.en", "app.ru"):
        (folder / f"{name}.html").write_text(f"<!doctype html><title>{name}</title>")
    (folder / "assets" / "app.js").write_text("export {};")
    (folder / "assets" / "fonts" / "f.woff2").write_bytes(b"font")
    return folder


def test_pricing_in_the_browsers_language(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    assert "pricing.en" in client.get("/").text
    assert "pricing.ru" in client.get("/", headers={"Accept-Language": "ru-RU,ru;q=0.9"}).text


def test_language_link_is_remembered(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    response = client.get("/?lang=ru", follow_redirects=False)
    assert response.status_code == 303 and response.cookies.get("kc_lang") == "ru"
    assert "pricing.ru" in client.get("/").text


def test_app_screens_need_a_session(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist), AUTH_DEV_LOGIN="true")
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/"
    dev_login(client)
    assert "app.en" in client.get("/tasks/12").text
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard"
    assert "pricing.en" in client.get("/pricing").text


def test_security_headers_and_caching(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    page = client.get("/")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-cache"
    font = client.get("/assets/fonts/f.woff2")
    assert font.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_missing_build_is_a_clear_message(client_factory, tmp_path):
    client = client_factory(FRONTEND_DIST=str(tmp_path / "nothing"))
    response = client.get("/")
    assert response.status_code == 503 and "npm run build" in response.text
