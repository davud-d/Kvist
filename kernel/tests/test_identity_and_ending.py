# -*- coding: utf-8 -*-
"""Runtime facts that keep the model's account of itself and the end of its replies in line."""
from __future__ import annotations

from kernel.tests.conftest import FakeLLMClient

_IDENTITY = (
    "you are Kvist, an open-source coding agent. The language model running you this turn "
    "is provider = {provider}, model = {model}; it is a separate thing and it changes. "
    "Asked what you are, you are Kvist. Asked which model runs you, name this one."
)


def _chat_message_sent(agent_factory, monkeypatch, goal: str = "что ты такое?") -> str:
    agent = agent_factory(stream=False, provider="anymodel", fast_model="ds/some-model")
    monkeypatch.setattr("kernel.agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr("kernel.llm_clients.fallback_candidates_for_model", lambda *args, **kwargs: [])
    agent.client = FakeLLMClient(content="ok")

    agent._run_chat(goal)

    return agent.client.requests[0]["messages"][-1]["content"]


def test_a_chat_turn_says_who_the_model_is_and_which_model_runs_it(agent_factory, monkeypatch):
    sent = _chat_message_sent(agent_factory, monkeypatch)

    fact = _IDENTITY.format(provider="anymodel", model="ds/some-model")
    assert f"(Runtime fact: {fact})\n\n" in sent


def test_the_action_prompt_opens_its_environment_with_the_same_fact(agent_factory):
    agent = agent_factory(provider="anymodel", fast_model="ds/fast", smart_model="ds/smart")
    agent._state.selected_action_model = "ds/fast"

    prompt = agent._build_runtime_prompt("goal", 1)

    fact = _IDENTITY.format(provider="anymodel", model="ds/fast")
    assert prompt.startswith(f"System environment:\n- Identity: {fact}\n- OS:")


def test_the_action_prompt_follows_the_model_after_an_escalation(agent_factory):
    agent = agent_factory(provider="anymodel", fast_model="ds/fast", smart_model="ds/smart")
    agent._state.selected_action_model = "ds/fast"
    agent._build_runtime_prompt("goal", 1)

    agent._state.selected_action_model = "ds/smart"
    prompt = agent._build_runtime_prompt("goal", 2)

    assert "model = ds/smart;" in prompt and "model = ds/fast;" not in prompt


_ENDING = (
    "(Runtime: end the reply where its content ends. A concrete next step is content; "
    "a general offer of help is not.)\n\n"
)


def test_a_chat_turn_puts_the_ending_rule_last_before_the_persons_message(agent_factory, monkeypatch):
    sent = _chat_message_sent(agent_factory, monkeypatch, goal="спасибо")

    assert sent.endswith(f"{_ENDING}спасибо")
    assert "response_language" not in sent


def test_the_ending_rule_follows_the_session_history_too(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    monkeypatch.setattr("kernel.agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr("kernel.llm_clients.fallback_candidates_for_model", lambda *args, **kwargs: [])
    agent.client = FakeLLMClient(content="ok")
    agent._session_history = [{"role": "reader", "goal": "прочитай a.py", "result": "готово"}]

    agent._run_chat("спасибо")

    sent = agent.client.requests[0]["messages"][-1]["content"]
    assert sent.endswith(f"{_ENDING}спасибо")
    assert sent.index("прочитай a.py") < sent.index(_ENDING)


def test_the_stored_chat_turn_does_not_carry_the_ending_rule(agent_factory, monkeypatch):
    agent = agent_factory(stream=False)
    monkeypatch.setattr("kernel.agent_model._load_role_prompt", lambda role: "chat prompt")
    monkeypatch.setattr("kernel.llm_clients.fallback_candidates_for_model", lambda *args, **kwargs: [])
    agent.client = FakeLLMClient(content="ok")

    agent._run_chat("спасибо")

    assert agent._chat_history[-2]["content"] == "спасибо"


def test_the_action_roles_always_receive_the_plain_ending_rule(agent_factory):
    agent = agent_factory()
    assert "End the reply where its content ends." in agent._build_runtime_prompt("goal", 1)
    agent._state.completion_ready = True
    assert "End the reply where its content ends." in agent._build_runtime_prompt("goal", 2)
