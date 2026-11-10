# -*- coding: utf-8 -*-
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from kernel.llm import pricing

_ROOT = Path(__file__).resolve().parents[2]

_PRICES = {
    "claude-sonnet-5": {"provider": "anthropic", "input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    "gpt-5": {"provider": "openai", "input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": None},
    "deepseek-flash": {"provider": "deepseek", "input": 0.3, "output": 1.2, "cache_read": 0.006, "cache_write": 0.0},
    "groq/openai/gpt-oss-120b": {"provider": "groq", "input": 0.15, "output": 0.6, "cache_read": None, "cache_write": None},
    # Same bare name under another vendor: must never answer for the wrong provider.
    "shared-name": {"provider": "bedrock", "input": 9.0, "output": 9.0, "cache_read": None, "cache_write": None},
}


@pytest.fixture(autouse=True)
def _fixture_table(monkeypatch):
    monkeypatch.setattr(pricing, "_table", lambda: _PRICES)


def test_direct_provider_returns_the_four_prices():
    assert pricing.lookup_price("anthropic", "claude-sonnet-5") == {
        "input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5,
    }


def test_unstated_cache_price_stays_none_inside_the_answer():
    assert pricing.lookup_price("openai", "gpt-5")["cache_write"] is None


def test_prefixed_table_keys_are_found_by_the_bare_model_id():
    assert pricing.lookup_price("groq", "openai/gpt-oss-120b")["input"] == 0.15


def test_kvist_provider_name_maps_to_the_table_provider():
    assert pricing.lookup_price("deepseek", "deepseek-flash")["output"] == 1.2


def test_the_same_model_name_under_another_vendor_is_not_returned():
    assert pricing.lookup_price("anthropic", "shared-name") is None


def test_openrouter_resolves_vendor_models_to_the_vendor_list_price():
    assert pricing.lookup_price("openrouter", "anthropic/claude-sonnet-5")["input"] == 2.0


@pytest.mark.parametrize("model", ["meta-llama/llama-4", "claude-sonnet-5", "anthropic/", ""])
def test_openrouter_models_it_cannot_price_are_none(model):
    assert pricing.lookup_price("openrouter", model) is None


@pytest.mark.parametrize("provider", ["chatgpt", "anymodel", "omniroute", "freeflow"])
def test_subscription_and_gateway_providers_are_never_priced(provider):
    assert pricing.lookup_price(provider, "claude-sonnet-5") is None
    assert pricing.lookup_price(provider, "cc/claude-sonnet-5") is None


def test_unknown_model_and_unknown_provider_are_none():
    assert pricing.lookup_price("anthropic", "claude-nope") is None
    assert pricing.lookup_price("nowhere", "claude-sonnet-5") is None


def test_bundled_table_covers_the_models_kvist_offers(monkeypatch):
    monkeypatch.undo()  # drop the fixture table, read the committed file
    for provider, model in [("anthropic", "claude-sonnet-5"), ("deepseek", "deepseek-flash"), ("openai", "gpt-5")]:
        price = pricing.lookup_price(provider, model)
        assert price is not None and price["input"] > 0 and price["output"] > 0


def test_pyproject_ships_the_price_table():
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "model_prices.json" in pyproject["tool"]["setuptools"]["package-data"]["kernel.llm"]
