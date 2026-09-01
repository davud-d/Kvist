from __future__ import annotations

from types import SimpleNamespace

import pytest

import agent_model
from agent import LocalAgent
from agent_state import PlanInfo
from executor import SafeExecutor
from tests.conftest import FakeLLMClient, NullUI


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
        ui=NullUI(),
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


def _prime_pipeline_agent(agent_factory, content):
    from tests.conftest import make_plan

    agent = agent_factory(stream=False)
    agent.client = FakeLLMClient(content=content)
    agent._state.plan_info = make_plan()
    agent._state.selected_action_role = "fast"
    agent._state.selected_action_model = agent.config.fast_model
    agent._state.current_agent_role = "coder"
    return agent


def test_matching_prefetch_is_consumed_without_a_second_call(agent_factory):
    agent = _prime_pipeline_agent(agent_factory, '{"thought":"prefetched"}')
    agent_model._launch_action_prefetch(agent, "PROMPT-A")

    def _forbidden(**_kwargs):
        raise AssertionError("call_action_model made a duplicate model call")

    agent._call_model = _forbidden

    result = agent._call_action_model("PROMPT-A")

    assert result == '{"thought":"prefetched"}'
    assert agent._prefetch is None


def test_mispredicted_prefetch_falls_back_to_a_normal_call(agent_factory):
    agent = _prime_pipeline_agent(agent_factory, '{"thought":"live"}')
    agent_model._launch_action_prefetch(agent, "PROMPT-A")

    # A different prompt must drop the stale prefetch and call normally, never
    # reuse the wrong step's response.
    result = agent._call_action_model("PROMPT-B")

    assert result == '{"thought":"live"}'
    assert agent._prefetch is None


class _ReasoningClient:
    """Streams reasoning deltas before the answer, like OpenRouter/Kimi do."""

    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        deltas = [
            SimpleNamespace(content=None, reasoning="I should "),
            SimpleNamespace(content=None, reasoning="read the file."),
            SimpleNamespace(content='{"thought":"ok"}', reasoning=None),
        ]
        return [SimpleNamespace(choices=[SimpleNamespace(delta=delta)]) for delta in deltas]


def test_streaming_reasoning_reaches_the_live_thinking_panel(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = _ReasoningClient()
    pushed: list[str] = []
    agent._ui.set_thinking = pushed.append

    content = agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert content == '{"thought":"ok"}'
    # Cleared for the new call, then filled, then sealed by the first answer token.
    assert pushed[0] == ""
    assert pushed[-1] == "I should read the file."


def test_action_call_clears_thinking_when_provider_streams_no_reasoning(agent_factory):
    agent = agent_factory(stream=True, verbose=False)
    agent.client = FakeLLMClient(stream_chunks=['{"thought"', ':"ok"}'])
    pushed: list[str] = []
    agent._ui.set_thinking = pushed.append

    agent._call_model_streaming("gpt-test", [{"role": "user", "content": "x"}])

    assert pushed == [""]
