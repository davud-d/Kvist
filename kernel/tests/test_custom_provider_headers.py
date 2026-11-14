# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.config.user_config import get_custom_providers, save_custom_provider
from kernel.llm import llm_clients


def test_headers_round_trip_and_junk_is_dropped(tmp_path):
    path = tmp_path / "config.json"
    save_custom_provider(
        "gw",
        {"base_url": "https://gw.example/v1", "headers": {"x-session": "{session}", "": "x", "bad": 3}},
        path,
    )
    assert get_custom_providers(path)["gw"]["headers"] == {"x-session": "{session}"}


def test_provider_without_headers_has_no_headers_key(tmp_path):
    path = tmp_path / "config.json"
    save_custom_provider("plain", {"base_url": "https://gw.example/v1"}, path)
    assert "headers" not in get_custom_providers(path)["plain"]


def test_session_placeholder_is_stable_within_one_client():
    headers = llm_clients._expand_session_headers({"a": "{session}", "b": "s-{session}", "c": "fixed"})
    assert headers["a"] != "{session}"
    assert headers["b"] == f"s-{headers['a']}"
    assert headers["c"] == "fixed"
