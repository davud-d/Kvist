# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel.context import context_budget
from kernel.llm import llm_clients, model_facts
from kernel.llm.model_facts import ModelFacts, parse_entry
from kernel.vision.vision import model_supports_vision


@pytest.fixture(autouse=True)
def _clean():
    llm_clients.clear_model_discovery_cache()
    yield
    llm_clients.clear_model_discovery_cache()


# Each shape as its vendor actually returns it (DeepSeek's measured Nov 27 2026).
DEEPSEEK = {"id": "deepseek-flash", "object": "model", "context_window": 1048576,
            "max_output_tokens": 393216, "input_modalities": ["text", "image"]}
DEEPSEEK_PRO = {"id": "deepseek-v4-pro", "object": "model", "context_window": 1048576,
                "input_modalities": ["text"]}
OPENROUTER = {"id": "qwen/qwen3.6-27b", "context_length": 262144,
              "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
              "top_provider": {"context_length": 131072, "max_completion_tokens": 32768}}
ANTHROPIC = {"id": "claude-opus-5-5", "type": "model", "max_input_tokens": 1000000, "max_tokens": 128000,
             "capabilities": {"image_input": {"supported": True}, "pdf_input": {"supported": True}}}
VLLM = {"id": "local/coder", "object": "model", "max_model_len": 65536}
BARE = {"id": "gpt-6.2-sol", "object": "model", "owned_by": "openai"}


@pytest.mark.parametrize(("entry", "expected"), [
    (DEEPSEEK, ModelFacts(context_window=1048576, sees_images=True)),
    (DEEPSEEK_PRO, ModelFacts(context_window=1048576, sees_images=False)),
    (OPENROUTER, ModelFacts(context_window=262144, sees_images=True)),
    (ANTHROPIC, ModelFacts(context_window=1000000, sees_images=True)),
    (VLLM, ModelFacts(context_window=65536, sees_images=None)),
    (BARE, ModelFacts()),
])
def test_each_vendor_shape_is_read(entry, expected):
    assert parse_entry(entry) == (entry["id"], expected)


@pytest.mark.parametrize("junk", [True, "1m", -5, 0, None, [1]])
def test_a_window_that_is_not_a_positive_number_is_not_a_window(junk):
    assert parse_entry({"id": "m", "context_window": junk}) == ("m", ModelFacts())


def test_sdk_objects_keep_the_extra_fields():
    from openai.types import Model

    # The SDK builds list items unvalidated, so DeepSeek's entry without "created" still arrives.
    item = Model.construct(**DEEPSEEK)
    assert parse_entry(item) == ("deepseek-flash", ModelFacts(context_window=1048576, sees_images=True))


def test_discovery_records_facts_per_provider(monkeypatch):
    monkeypatch.setattr(llm_clients, "_list_models_via_openai", lambda key, url: [DEEPSEEK, DEEPSEEK_PRO])
    ids, error = llm_clients.fetch_available_models("deepseek", {"deepseek_api_key": "k"}, force_refresh=True)

    assert error is None and ids == ["deepseek-flash", "deepseek-v4-pro"]
    assert model_facts.facts_for("deepseek", "deepseek-flash").sees_images is True
    assert model_facts.facts_for("openrouter", "deepseek-flash") is None  # another provider may state otherwise


def test_bare_id_lists_still_work(monkeypatch):
    """Older fakes and gateways hand back plain id strings."""
    monkeypatch.setattr(llm_clients, "_list_models_via_openai", lambda key, url: ["deepseek-flash"])
    assert llm_clients.fetch_available_models("deepseek", {"deepseek_api_key": "k"}, force_refresh=True) == (
        ["deepseek-flash"], None)


def test_the_catalog_outranks_the_vision_name_table():
    # The name table calls deepseek-v4-pro blind and knows nothing of a custom model.
    model_facts.remember("deepseek", [{"id": "deepseek-v4-pro", "input_modalities": ["text", "image"]}])
    model_facts.remember("custom:lab", [{"id": "new-model", "input_modalities": ["text"]}])

    assert model_supports_vision("deepseek", "deepseek-v4-pro") is True
    assert model_supports_vision("custom:lab", "new-model") is False
    assert model_supports_vision("custom:lab", "other-model") is None


def test_a_text_only_wrapper_stays_blind_whatever_the_catalog_says():
    model_facts.remember("freeflow", [{"id": "gemini-3.8-flash", "input_modalities": ["text", "image"]}])
    assert model_supports_vision("freeflow", "gemini-3.8-flash") is False


def test_a_stated_window_sets_the_budget_30k_below_it():
    model_facts.remember("deepseek", [DEEPSEEK])
    assert context_budget.derived_prompt_budget("deepseek", "deepseek-flash") == 1048576 - 30_000


def test_a_small_stated_window_keeps_a_proportional_reserve():
    model_facts.remember("custom:lab", [{"id": "tiny", "context_length": 32768}])
    budget = context_budget.derived_prompt_budget("custom:lab", "tiny")
    assert 0 < 32768 - budget < 30_000 and budget == 32768 - int(32768 * 0.15)


def test_without_a_stated_window_the_table_still_decides():
    assert context_budget.derived_prompt_budget("openai", "gpt-6.2-sol") == int(
        context_budget.input_limit_for("gpt-6.2-sol") * 0.85)


def test_the_env_var_pins_the_budget_over_a_stated_window(agent_factory, monkeypatch):
    from kernel.agent import agent_model

    model_facts.remember("deepseek", [DEEPSEEK])
    agent = agent_factory(provider="deepseek", fast_model="deepseek-flash")
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    assert agent_model.context_token_limit(agent) == 1048576 - 30_000

    monkeypatch.setenv("AGENT_MAX_CONTEXT_TOKENS", "200000")
    assert agent_model.context_token_limit(agent) == 200_000
