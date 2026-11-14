# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"x-session": "{session}", "x-n": " 1 "}', {"x-session": "{session}", "x-n": "1"}),
        ('{"": "a", "b": "", "c": 3, "d": "ok"}', {"d": "ok"}),
        ("not json", {}),
        ("[1, 2]", {}),
        ("", {}),
        (None, {}),
    ],
)
def test_headers_from_json_keeps_only_clean_string_pairs(text, expected):
    assert llm_clients.headers_from_json(text) == expected


def test_omniroute_sends_the_headers_it_was_given(monkeypatch):
    built = {}

    def fake_build(api_key, base_url=None, api_timeout=60.0, headers=None):
        built.update(api_key=api_key, base_url=base_url, headers=headers)
        return object()

    monkeypatch.setattr(llm_clients, "_build_openai_client", fake_build)
    llm_clients.create_llm_client("omniroute", {
        "omniroute_api_key": "k", "omniroute_base_url": "https://gw.example/v1",
        "omniroute_headers": '{"x-opencode-session": "{session}"}',
    })
    assert built == {"api_key": "k", "base_url": "https://gw.example/v1", "headers": {"x-opencode-session": "{session}"}}
    llm_clients.create_llm_client("omniroute", {"omniroute_api_key": "k"})
    assert built["headers"] == {}


def test_the_headers_travel_in_the_environment_and_are_never_written_to_the_config(monkeypatch):
    from kernel.config import user_config

    monkeypatch.setenv("OMNIROUTE_HEADERS", '{"x-s": "{session}"}')
    assert user_config._resolve_secret({}, user_config.OMNIROUTE_HEADERS_KEY) == '{"x-s": "{session}"}'
