# -*- coding: utf-8 -*-
"""The phone app's page: served at /m, one page at every address of a session, one third-party script allowed."""
from __future__ import annotations

import pytest
from stripe_fake import WALLET


@pytest.fixture
def dist(tmp_path):
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "mobile" / "js").mkdir(parents=True)
    for name in ("pricing.en", "pricing.ru", "app.en", "app.ru"):
        (folder / f"{name}.html").write_text(f"<!doctype html><title>{name}</title>")
    (folder / "mobile" / "index.html").write_text(
        '<!doctype html><title>phone</title><script type="module" src="/m/v/__BUILD__/js/main.js"></script>')
    (folder / "mobile" / "js" / "main.js").write_text("export {};")
    (folder / "mobile" / "manifest.webmanifest").write_text("{}")
    return folder


def test_the_app_is_served_at_m_and_every_session_address_is_the_same_page(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    assert "phone" in client.get("/m/").text
    assert "phone" in client.get("/m/s/Abc_123-xyz").text
    assert "phone" in client.get("/m/p/7").text
    assert client.get("/m/js/main.js").status_code == 200
    # A reload on a session address must not look for js/main.js beside it: the page asks for /m/js/ absolutely.
    assert client.get("/m/s/Abc_123-xyz/js/main.js").status_code == 404


def test_scripts_are_addressed_by_build_so_no_cache_can_show_an_old_app(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    page = client.get("/m/")
    assert "__BUILD__" not in page.text and page.headers["cache-control"] == "no-store"
    address = page.text.split('src="')[1].split('"')[0]
    assert address.startswith("/m/v/") and client.get(address).status_code == 200
    # Any build segment serves the current files (an old page that asked for it still works), and only the folder's.
    assert client.get("/m/v/whatever/js/main.js").status_code == 200
    assert client.get("/m/v/x/../../../index.html").status_code in (404, 200)
    assert client.get("/m/v/x/..%2f..%2fpricing.en.html").status_code == 404
    assert client.get("/m/v/x/js/nothing.js").status_code == 404
    # A changed file is a new address.
    (dist / "mobile" / "js" / "main.js").write_text("export const changed = 1;")
    assert client_factory(FRONTEND_DIST=str(dist)).get("/m/").text.split('src="')[1].split('"')[0] != address


def test_the_page_may_load_googles_button_only_with_the_wallet_on(client_factory, dist):
    off = client_factory(FRONTEND_DIST=str(dist)).get("/m/").headers["content-security-policy"]
    assert "pay.google.com" not in off and "frame-src" not in off
    client = client_factory(FRONTEND_DIST=str(dist), **WALLET)
    on = client.get("/m/").headers["content-security-policy"]
    assert "script-src 'self' https://pay.google.com" in on and "frame-src https://pay.google.com" in on
    # No other page of the site gets a third party's script, wallet or not.
    for path in ("/", "/pricing", "/assets/app.js"):
        policy = client.get(path).headers["content-security-policy"]
        assert "pay.google.com" not in policy, path
    assert "script-src 'self';" in client.get("/").headers["content-security-policy"]


def test_the_page_itself_is_not_cached_and_only_an_ide_panel_frames_it(client_factory, dist):
    client = client_factory(FRONTEND_DIST=str(dist))
    page = client.get("/m/")
    assert "frame-ancestors 'self' vscode-webview: vscode-file: file:" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    # The rest of the site is still never framed.
    for path in ("/", "/pricing", "/api/me"):
        assert "frame-ancestors 'none'" in client.get(path).headers["content-security-policy"], path
