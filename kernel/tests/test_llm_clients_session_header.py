# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.llm.llm_clients import request_session_headers


def test_opencode_go_gets_the_conversation_header():
    assert request_session_headers("opencode-go", "abc") == {"x-opencode-session": "abc"}


def test_other_providers_get_no_header():
    assert request_session_headers("deepseek", "abc") == {}
    assert request_session_headers("opencode-zen", "abc") == {}
