from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_model
from agent_state import PlanInfo
from tests.conftest import FakeLLMClient


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


def test_trim_prompt_to_token_limit_keeps_head_and_tail(agent_factory):
    agent = agent_factory(max_context_tokens=20)
    agent._count_tokens = lambda text: len(text)
    prompt = "a" * 1000 + "TAIL"

    trimmed = agent._trim_prompt_to_token_limit("system", prompt)

    assert "PROMPT MIDDLE TRUNCATED" in trimmed
    assert "TAIL" in trimmed
    assert trimmed.endswith("[CONTEXT TRUNCATED]")


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


def test_call_model_raises_after_non_retryable_error(agent_factory, monkeypatch):
    agent = agent_factory(stream=False, max_retries=2)
    monkeypatch.setattr(agent_model.time, "sleep", lambda _: None)

    def fail(**kwargs):
        raise ValueError("bad request")

    agent.client.chat.completions.create = fail
    with pytest.raises(RuntimeError, match="Failed to get model response"):
        agent._call_model("model", "system", "user")
