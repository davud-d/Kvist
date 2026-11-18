# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kernel.agent.agent_model import _create_completion_with_reasoning_fallback
from kernel.config.user_config import load_user_config
from kernel.llm import provider_headers
from kernel.llm.llm_clients import request_session_headers
from kernel.tests.conftest import FakeLLMClient


def test_a_refusal_names_the_missing_conversation_header():
    error = RuntimeError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'MissingSessionID', 'message': "
        "'Request is missing x-opencode-session and cannot be routed efficiently.'}}"
    )
    assert provider_headers.demanded_session_header(error) == "x-opencode-session"
    assert provider_headers.demanded_session_header(RuntimeError("x-Foo-Session header is required")) == "x-foo-session"


@pytest.mark.parametrize("text", ["missing x-api-version", "Error 400: bad request"])
def test_a_header_that_is_not_a_conversation_id_is_not_guessed(text):
    assert provider_headers.demanded_session_header(RuntimeError(text)) is None


def test_configured_headers_ride_on_the_request_and_session_expands():
    provider_headers.set_header("deepseek", "X-Conv", "{session}")
    provider_headers.set_header("deepseek", "X-Plain", "v")

    assert request_session_headers("deepseek", "abc") == {"X-Conv": "abc", "X-Plain": "v"}
    assert request_session_headers("anthropic", "abc") == {}


def test_a_configured_header_replaces_the_built_in_one_of_the_same_name():
    provider_headers.set_header("opencode-go", "X-OpenCode-Session", "mine")

    assert request_session_headers("opencode-go", "abc") == {"X-OpenCode-Session": "mine"}


def test_a_header_with_a_line_break_is_refused():
    with pytest.raises(ValueError):
        provider_headers.set_header("deepseek", "X-A", "one\r\nX-B: two")


class _Gateway(FakeLLMClient):
    """Refuses a request that lacks the header, like OpenCode Go."""

    def create(self, **kwargs):
        headers = kwargs.get("extra_headers") or {}
        if not any(name.lower() == "x-needed-session" for name in headers):
            self.requests.append(kwargs)
            raise RuntimeError("Error code: 400 - Request is missing x-needed-session and cannot be routed")
        return super().create(**kwargs)


def _agent(client):
    notices: list[str] = []
    ui = SimpleNamespace(print_notice=notices.append, debug_print=lambda *_: None, print_error=lambda *_: None)
    config = SimpleNamespace(provider="deepseek", model_temperature=0.0, api_timeout=30, reasoning_effort="medium")
    return SimpleNamespace(client=client, config=config, llm_session_id="sid-1", _ui=ui), notices


def test_the_first_refusal_is_repaired_and_remembered():
    agent, notices = _agent(_Gateway())
    request = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    response = _create_completion_with_reasoning_fallback(agent, request)

    assert response.choices[0].message.content
    assert agent.client.requests[-1]["extra_headers"] == {"x-needed-session": "sid-1"}
    assert load_user_config()["provider_headers"]["deepseek"] == {"x-needed-session": "{session}"}
    assert len(notices) == 1 and "x-needed-session" in notices[0]

    # The next request carries it from the start: no refusal, no second notice.
    _create_completion_with_reasoning_fallback(agent, request)
    assert len(agent.client.requests) == 3 and len(notices) == 1


def test_a_header_that_does_not_help_is_not_remembered():
    class Stubborn(FakeLLMClient):
        def create(self, **kwargs):
            raise RuntimeError("Error code: 400 - Request is missing x-needed-session")

    agent, _ = _agent(Stubborn())
    with pytest.raises(RuntimeError):
        _create_completion_with_reasoning_fallback(agent, {"model": "m", "messages": []})

    assert not load_user_config().get("provider_headers")
