# -*- coding: utf-8 -*-
"""The native-tools probe: independent of effort, and asked again within a run when it got no answer."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kernel.agent import agent_model
from kernel.llm.llm_clients import AnthropicCompatibleClient
from kernel.tests.test_native_tools import _call, _response


@pytest.fixture(autouse=True)
def _fresh_probe_cache(monkeypatch):
    monkeypatch.setattr(agent_model, "_NATIVE_TOOL_SUPPORT", {})
    monkeypatch.delenv("AGENT_NATIVE_TOOLS", raising=False)


class _Provider:
    """Plays one scripted outcome per probe (an exception or a verdict), then native turns."""

    def __init__(self, probes: list, turns: list | None = None):
        self.probes = list(probes)
        self.turns = list(turns or [])
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    @property
    def probe_count(self) -> int:
        return sum(1 for request in self.requests if request["tools"][0]["function"]["name"] == "ping")

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if kwargs["tools"][0]["function"]["name"] == "ping":
            outcome = self.probes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return _response(calls=[_call("probe", "ping", ok=True)])
        return self.turns.pop(0)


def _text_answer(*actions: dict) -> str:
    return json.dumps({"thought": "t", "plan_step": "inspect", "user_message": "m", "actions": list(actions)})


def _read(path: str) -> dict:
    return {"type": "read_file", "args": {"path": path}}


def _final(message: str) -> dict:
    return {"type": "final_answer", "args": {"message": message, "outcome": "incomplete"}}


def _agent(agent_factory, provider: _Provider, text_steps: list[str]):
    agent = agent_factory(max_steps=10, stream=False)
    agent.client = provider
    agent._state.response_language = "en"
    prompts: list[str] = []
    answers = list(text_steps)

    def text_model(prompt: str) -> str:
        prompts.append(prompt)
        return answers.pop(0)

    agent._call_action_model = text_model
    agent.text_prompts = prompts
    return agent


# ── the request ────────────────────────────────────────────────────────────


def test_the_probe_carries_no_reasoning_controls_and_forces_the_tool(agent_factory):
    agent = _agent(agent_factory, _Provider([True]), [])
    agent.config.provider = "anthropic"
    agent.config.reasoning_effort = "high"

    assert agent_model.native_tools_supported(agent, "m") is True

    request = agent.client.requests[0]
    assert "reasoning_effort" not in request
    assert request["tool_choice"] == {"type": "function", "function": {"name": "ping"}}


def test_through_the_anthropic_adapter_the_probe_has_thinking_off_and_the_tool_forced(agent_factory):
    adapter = AnthropicCompatibleClient(api_key="test-key")
    sent: dict = {}

    def create(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", id="p", name="ping", input={"ok": True})],
            stop_reason="tool_use",
            usage=None,
        )

    adapter._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    agent = agent_factory(max_steps=1)
    agent.client = adapter
    agent.config.provider = "anthropic"
    agent.config.reasoning_effort = "high"

    assert agent_model.native_tools_supported(agent, "claude-sonnet-4-5") is True

    assert "thinking" not in sent and "output_config" not in sent
    assert sent["tool_choice"] == {"type": "tool", "name": "ping"}


# ── asking again within the run ────────────────────────────────────────────


def test_a_probe_with_no_answer_on_step_one_is_asked_again_on_step_two_and_history_crosses_over(agent_factory):
    provider = _Provider(
        [TimeoutError("Request timed out."), True],
        turns=[_response("", [_call("c1", "final_answer", message="Done natively.", outcome="incomplete")])],
    )
    agent = _agent(agent_factory, provider, [_text_answer(_read("a.py"))])
    (agent.executor.workspace / "a.py").write_text("MARKER_FROM_TEXT_STEP = 1\n", encoding="utf-8")

    result = agent._run_loop("read a.py", start_step=1)

    assert result == "Done natively."
    assert len(agent.text_prompts) == 1  # step 1 ran on text
    assert provider.probe_count == 2
    assert agent._state.native_tools is True
    native_request = provider.requests[-1]
    assert native_request["tools"][0]["function"]["name"] != "ping"
    # The result the text step produced reaches the native conversation.
    assert "MARKER_FROM_TEXT_STEP" in native_request["messages"][1]["content"]


def test_three_probes_in_a_row_without_an_answer_leave_the_rest_of_the_run_on_text(agent_factory):
    provider = _Provider([TimeoutError("timed out")] * 3 + [True])
    agent = _agent(
        agent_factory,
        provider,
        [_text_answer(_read("a.py")), _text_answer(_read("a.py")), _text_answer(_read("a.py")), _text_answer(_final("Text to the end."))],
    )
    (agent.executor.workspace / "a.py").write_text("A = 1\n", encoding="utf-8")

    result = agent._run_loop("read a.py", start_step=1)

    assert result == "Text to the end."
    assert provider.probe_count == 3
    assert len(agent.text_prompts) == 4
    assert agent._state.native_tools is False


def test_a_probe_the_provider_answered_is_not_repeated_within_the_run(agent_factory):
    provider = _Provider(
        [True],
        turns=[
            _response("", [_call("c1", "read_file", path="a.py")]),
            _response("", [_call("c2", "final_answer", message="Done.", outcome="incomplete")]),
        ],
    )
    agent = _agent(agent_factory, provider, [])
    (agent.executor.workspace / "a.py").write_text("A = 1\n", encoding="utf-8")

    agent._run_loop("read a.py", start_step=1)

    assert provider.probe_count == 1
