# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_state import AgentConfig
from llm_clients import AnthropicCompatibleClient
from tests.conftest import FakeLLMClient
from reasoning_effort import (
    DEFAULT_EFFORT,
    EFFORT_LEVELS,
    anthropic_thinking_config,
    effort_advisory,
    is_reasoning_error,
    merge_reasoning_kwargs,
    normalize_effort,
    reasoning_request_kwargs,
    strip_reasoning_kwargs,
)
from user_config import get_saved_reasoning_effort, save_reasoning_effort


# ── scale / normalization ───────────────────────────────────────────────────


def test_default_effort_is_medium_and_in_scale():
    assert DEFAULT_EFFORT == "medium"
    assert DEFAULT_EFFORT in EFFORT_LEVELS


@pytest.mark.parametrize("bad", ["", None, "turbo", "  ", "LOW "])
def test_normalize_effort_rejects_unknown_but_trims_known(bad):
    result = normalize_effort(bad)
    if str(bad).strip().casefold() == "low":
        assert result == "low"
    else:
        assert result == DEFAULT_EFFORT


# ── per-provider mapping ────────────────────────────────────────────────────


def test_openai_folds_max_into_xhigh_and_false_into_minimal():
    assert reasoning_request_kwargs("openai", "max") == {"reasoning_effort": "xhigh"}
    assert reasoning_request_kwargs("openai", "false") == {"reasoning_effort": "minimal"}
    assert reasoning_request_kwargs("openai", "high") == {"reasoning_effort": "high"}


def test_deepseek_disables_thinking_and_maps_supported_effort_levels():
    assert reasoning_request_kwargs("deepseek", "false") == {
        "extra_body": {"thinking": {"type": "disabled"}}
    }
    assert reasoning_request_kwargs("deepseek", "low") == {
        "reasoning_effort": "low",
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    assert reasoning_request_kwargs("deepseek", "max") == {
        "reasoning_effort": "max",
        "extra_body": {"thinking": {"type": "enabled"}},
    }


def test_openrouter_uses_nested_reasoning_effort():
    assert reasoning_request_kwargs("openrouter", "xhigh") == {
        "extra_body": {"reasoning": {"effort": "xhigh"}}
    }
    assert reasoning_request_kwargs("openrouter", "false") == {
        "extra_body": {"reasoning": {"effort": "none"}}
    }


def test_anthropic_passes_the_neutral_marker_through():
    assert reasoning_request_kwargs("anthropic", "high") == {"reasoning_effort": "high"}


def test_freeflow_premaps_to_groq_values():
    assert reasoning_request_kwargs("freeflow", "max") == {"reasoning_effort": "high"}
    assert reasoning_request_kwargs("freeflow", "false") == {"reasoning_effort": "low"}


def test_every_level_maps_without_error_for_every_known_provider():
    for provider in (
        "openai",
        "omniroute",
        "openrouter",
        "deepseek",
        "anthropic",
        "freeflow",
    ):
        for level in EFFORT_LEVELS:
            assert isinstance(reasoning_request_kwargs(provider, level), dict)


# ── merge / strip ───────────────────────────────────────────────────────────


def test_merge_preserves_an_existing_extra_body_entry():
    kwargs = {"model": "x", "extra_body": {"foo": 1}}
    merge_reasoning_kwargs(kwargs, "openrouter", "high")
    assert kwargs["extra_body"] == {"foo": 1, "reasoning": {"effort": "high"}}


def test_strip_removes_reasoning_effort_and_nested_reasoning():
    kwargs = {"model": "x", "temperature": 0.1}
    merge_reasoning_kwargs(kwargs, "openrouter", "high")
    merge_reasoning_kwargs(kwargs, "openai", "high")
    cleaned = strip_reasoning_kwargs(kwargs)
    assert cleaned == {"model": "x", "temperature": 0.1}


def test_strip_keeps_unrelated_extra_body_keys():
    cleaned = strip_reasoning_kwargs(
        {"model": "x", "extra_body": {"reasoning": {}, "cache": True}}
    )
    assert cleaned == {"model": "x", "extra_body": {"cache": True}}


# ── error detection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "400: reasoning_effort must be 'none' or 'default'",
        "Unsupported parameter: 'reasoning_effort'",
        "invalid request: thinking.budget_tokens exceeds max_tokens",
        "model does not support extended thinking",
    ],
)
def test_is_reasoning_error_true_for_rejections(message):
    assert is_reasoning_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "rate limit exceeded",
        "the assistant was still thinking about it",
        "connection reset",
    ],
)
def test_is_reasoning_error_false_otherwise(message):
    assert is_reasoning_error(Exception(message)) is False


# ── advisories ──────────────────────────────────────────────────────────────


def test_advisory_flags_openai_max_and_deepseek_collapse():
    assert "xhigh" in effort_advisory("openai", "max")
    assert "high" in effort_advisory("deepseek", "medium")


def test_advisory_is_quiet_when_the_level_maps_cleanly():
    assert effort_advisory("openai", "high") is None
    assert effort_advisory("openrouter", "high") is None


def test_advisory_always_warns_for_custom_providers():
    assert effort_advisory("custom:acme", "medium") is not None


# ── anthropic thinking translation ──────────────────────────────────────────


def test_anthropic_thinking_config_raises_false_to_the_minimum():
    # Thinking-off is the state where an Anthropic model narrates an action
    # instead of emitting one, so "false" buys the cheapest thinking, not none.
    assert anthropic_thinking_config("false") == anthropic_thinking_config("low")


def test_anthropic_thinking_config_scales_the_budget():
    low = anthropic_thinking_config("low")["budget_tokens"]
    high = anthropic_thinking_config("max")["budget_tokens"]
    assert 0 < low < high


def _fake_anthropic(record: dict):
    def create(**kwargs):
        record.update(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(text="{}")], stop_reason="end_turn"
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def test_anthropic_wrapper_enables_thinking_and_drops_temperature():
    client = AnthropicCompatibleClient(api_key="test-key")
    record: dict = {}
    client._client = _fake_anthropic(record)

    client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        max_tokens=1024,
        reasoning_effort="high",
    )

    assert record["thinking"] == {"type": "enabled", "budget_tokens": 8192}
    assert "temperature" not in record
    assert record["max_tokens"] > record["thinking"]["budget_tokens"]


def test_anthropic_wrapper_drops_temperature_when_effort_is_false():
    # "false" now leaves thinking on at its lowest budget, and extended thinking
    # rejects a non-default temperature — so the knob costs temperature too.
    client = AnthropicCompatibleClient(api_key="test-key")
    record: dict = {}
    client._client = _fake_anthropic(record)

    client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
        reasoning_effort="false",
    )

    assert record["thinking"]["budget_tokens"] == anthropic_thinking_config("low")["budget_tokens"]
    assert "temperature" not in record


def test_anthropic_wrapper_keeps_temperature_when_no_effort_marker():
    client = AnthropicCompatibleClient(api_key="test-key")
    record: dict = {}
    client._client = _fake_anthropic(record)

    client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.1,
    )

    assert "thinking" not in record
    assert record["temperature"] == pytest.approx(0.1)


# ── config persistence ──────────────────────────────────────────────────────


def test_reasoning_effort_round_trips_through_user_config(tmp_path):
    path = tmp_path / "config.json"
    assert get_saved_reasoning_effort(path) == "medium"
    save_reasoning_effort("XHigh", path)
    assert get_saved_reasoning_effort(path) == "xhigh"


def test_agent_config_defaults_to_medium():
    assert AgentConfig().reasoning_effort == "medium"


# ── runtime injection + fallback ────────────────────────────────────────────


def test_create_json_completion_injects_the_level(agent_factory):
    agent = agent_factory(stream=False, reasoning_effort="high")
    client = FakeLLMClient(content='{"thought":"ok"}')
    agent.client = client

    agent._call_model("gpt-test", "system", "user")

    assert client.requests[0]["reasoning_effort"] == "high"


def test_reasoning_rejection_retries_without_it_and_keeps_warning(agent_factory):
    class Rejecting(FakeLLMClient):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            if "reasoning_effort" in kwargs:
                raise RuntimeError("400: Unsupported parameter: 'reasoning_effort'")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content='{"thought":"ok"}'))
                ]
            )

    agent = agent_factory(stream=False, reasoning_effort="high", cache_enabled=False)
    agent.client = Rejecting()
    warnings: list[str] = []
    agent._ui.print_error = lambda message: warnings.append(message)

    out = agent._call_model("gpt-test", "system", "user")

    assert out == '{"thought":"ok"}'
    assert len(agent.client.requests) == 2
    assert "reasoning_effort" not in agent.client.requests[1]
    assert warnings and "high" in warnings[0]


def test_anthropic_false_is_raised_to_the_minimum_not_disabled():
    """Thinking-off is the state where these models narrate an action instead
    of emitting one, so 'false' buys the cheapest thinking rather than none."""
    from reasoning_effort import anthropic_thinking_config, effort_advisory

    off = anthropic_thinking_config("false")
    assert off is not None
    assert off == anthropic_thinking_config("low")

    advisory = effort_advisory("anthropic", "false")
    assert advisory is not None
    assert "not" in advisory and "off" in advisory


def test_absent_reasoning_marker_still_means_no_thinking_block():
    """Only an explicit 'false' is raised; no marker at all stays no marker."""
    from llm_clients import _anthropic_thinking_from_effort

    assert _anthropic_thinking_from_effort(None) is None
    assert _anthropic_thinking_from_effort("") is None
    assert _anthropic_thinking_from_effort("false") is not None
