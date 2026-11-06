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


def test_app_shell_carries_the_signed_in_person(client_factory, dist):
    (dist / "app.en.html").write_text(
        '<!doctype html><script id="boot" type="application/json">null</script>' + "x" * 600)
    client = client_factory(FRONTEND_DIST=str(dist), AUTH_DEV_LOGIN="true")
    dev_login(client)
    page = client.get("/dashboard")
    assert '"login": "dev"' in page.text and "null</script>" not in page.text
    for part in ('"subscription": {"status": "none"', '"providers": [', '"has_tasks": false', '"enabled": false'):
        assert part in page.text
    assert page.headers["content-encoding"] == "gzip"


def test_security_headers_and_caching(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    page = client.get("/")
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-cache"
    font = client.get("/assets/fonts/f.woff2")
    assert font.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_the_pixel_window_is_served_and_may_show_pictures_it_fetched(client_factory, dist):
    (dist / "pixel" / "js").mkdir(parents=True)
    (dist / "pixel" / "index.html").write_text("<!doctype html><title>pixel</title>")
    (dist / "pixel" / "js" / "main.js").write_text("export {};")
    client = client_factory(FRONTEND_DIST=str(dist))
    page = client.get("/pixel/")
    assert "pixel" in page.text and page.headers["cache-control"] == "no-cache"
    assert client.get("/pixel/js/main.js").status_code == 200
    # The agent's screen is drawn from a blob the page fetched itself; nothing else widens what pages may load.
    policy = page.headers["content-security-policy"]
    assert "img-src 'self' data: blob: https://avatars.githubusercontent.com" in policy
    assert "script-src 'self' https://cdn.paddle.com" in policy


def test_the_old_new_task_page_leads_to_the_workspace(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    response = client.get("/new", follow_redirects=False)
    assert response.status_code == 307 and response.headers["location"] == "/pixel/"


def test_missing_build_is_a_clear_message(client_factory, tmp_path):
    client = client_factory(FRONTEND_DIST=str(tmp_path / "nothing"))
    response = client.get("/")
    assert response.status_code == 503 and "npm run build" in response.text
