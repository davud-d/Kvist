from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_model
from agent import LocalAgent
from agent_state import PlanInfo
from executor import SafeExecutor
from tests.conftest import FakeLLMClient


@pytest.mark.parametrize(
    "message",
    [
        "sk-proj-" + "a" * 24,
        "gsk_" + "b" * 24,
        "Authorization: Bearer synthetic-token-123456",
        "OPENAI_API_KEY=synthetic-value-123456",
        "https://user:synthetic-password@example.test/path",
        "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12,
        "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----",
    ],
)
def test_error_sanitizer_redacts_credential_forms(message):
    sanitized = agent_model.sanitize_error_message(message)

    assert "synthetic" not in sanitized
    assert "[REDACTED]" in sanitized


def test_select_action_model_prefers_smart_for_complex_or_error(agent_factory):
    agent = agent_factory(fast_model="fast", smart_model="smart")
    agent._state.plan_info = PlanInfo("g", "complex", ["x"], ["y"], [])
    assert agent._select_action_model_for_run() == "smart"

    agent._state.selected_action_role = "fast"
    assert agent._select_action_model_for_run() == "fast"


def test_count_tokens_falls_back_without_encoding(agent_factory):
    agent = agent_factory()
    agent._encoding = None
    assert agent._count_tokens("x" * 40) == 10


def test_token_encoding_initializes_once_on_first_count(
    agent_factory,
    monkeypatch,
):
    agent = agent_factory()
    calls = 0

    class Encoding:
        def encode(self, text, disallowed_special):
            assert disallowed_special == ()
            return text.split()

    def initialize():
        nonlocal calls
        calls += 1
        return Encoding()

    monkeypatch.setattr(agent, "_init_tiktoken_encoding", initialize)

    assert calls == 0
    assert agent._count_tokens("one two three") == 3
    assert agent._count_tokens("four five") == 2
    assert calls == 1


def test_agent_construction_does_not_load_token_encoding(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        LocalAgent,
        "_init_tiktoken_encoding",
        staticmethod(
            lambda: (_ for _ in ()).throw(
                AssertionError("token encoding loaded eagerly")
            )
        ),
    )

    agent = LocalAgent(
        FakeLLMClient(),
        SafeExecutor(tmp_path),
    )

    assert agent._encoding is agent_model._TOKEN_ENCODING_UNINITIALIZED


def test_trim_prompt_to_token_limit_keeps_head_and_tail(agent_factory):
    agent = agent_factory(max_context_tokens=20)
    agent._count_tokens = lambda text: len(text)
    prompt = "HEAD" + "b" * 1000

    trimmed = agent._trim_prompt_to_token_limit("system", prompt)

    assert "CONTEXT TRIMMED BY PRIORITY" in trimmed
    assert "HEAD" in trimmed
    assert trimmed.endswith("[CONTEXT TRIMMED BY PRIORITY]")


def test_call_model_uses_cache_and_non_streaming_completion(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, cache_enabled=True, max_retries=1)
    client = FakeLLMClient(content='{"thought":"ok"}')
    agent.client = client
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)

    first = agent._call_model("gpt-test", "system", "user")
    second = agent._call_model("gpt-test", "system", "user")

    assert first == '{"thought":"ok"}'
    assert second == first
    assert len(client.requests) == 1
    assert client.requests[0]["response_format"] == {"type": "json_object"}


def test_call_model_streaming_collects_chunks(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = FakeLLMClient(stream_chunks=['{"thought"', ':"ok"}'])

    content = agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert content == '{"thought":"ok"}'
    assert agent._last_chunk_count == 2
    assert agent._last_avg_chunk_size > 0


def test_create_json_completion_falls_back_without_response_format(agent_factory):
    agent = agent_factory()
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format is not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    agent.client.chat.completions.create = create
    response = agent._create_json_completion("model", [{"role": "user", "content": "x"}])

    assert response.choices[0].message.content == "ok"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_create_json_completion_redacts_messages_before_provider(agent_factory):
    agent = agent_factory()
    synthetic = "sk-proj-" + "m" * 24
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="ok"))
            ]
        )

    agent.client.chat.completions.create = create
    agent._create_json_completion(
        "model",
        [{"role": "user", "content": f"use api_key={synthetic}"}],
    )

    assert synthetic not in str(calls)
    assert "[REDACTED]" in calls[0]["messages"][0]["content"]


def test_call_model_raises_after_non_retryable_error(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, max_retries=2)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)
    synthetic = "sk-proj-" + "e" * 24

    def fail(**kwargs):
        raise ValueError(f"bad request Authorization: Bearer {synthetic}")

    agent.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match="Failed to get model response") as caught:
        agent._call_model("model", "system", "user")
    assert synthetic not in str(caught.value)
