# -*- coding: utf-8 -*-
"""Per-token prices from the bundled LiteLLM-derived table (kernel/llm/model_prices.json).

``lookup_price`` answers "what does this provider charge for this model", in USD per 1M
tokens, or None when that cannot be said for certain. None is a real answer: callers show
"no cost" instead of a guess.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_TABLE_PATH = Path(__file__).with_name("model_prices.json")

# Providers whose bill is not per token (a subscription) or is set by a gateway whose
# multiplier we cannot turn into dollars: any number here would be invented.
_UNPRICED = frozenset({"chatgpt", "anymodel", "omniroute", "freeflow"})

# kvist provider -> the ``provider`` value of that vendor's rows in the table.
_TABLE_PROVIDER = {
    "openai": "openai", "anthropic": "anthropic", "deepseek": "deepseek", "gemini": "gemini",
    "xai": "xai", "mistral": "mistral", "groq": "groq", "moonshot": "moonshot", "zai": "zai",
    "together": "together_ai", "fireworks": "fireworks_ai", "cerebras": "cerebras",
    "cohere": "cohere", "perplexity": "perplexity", "deepinfra": "deepinfra", "nebius": "nebius",
    "sambanova": "sambanova", "minimax": "minimax", "qwen": "dashscope", "doubao": "volcengine",
}

# OpenRouter ids look like "vendor/model". For these vendors it bills the vendor's own list
# price; for open-weight models it routes to hosts with different prices, so those stay None.
_OPENROUTER_VENDORS = {"anthropic": "anthropic", "openai": "openai", "deepseek": "deepseek"}


@lru_cache(maxsize=1)
def _table() -> dict[str, dict]:
    return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))["models"]


def _row(table_provider: str, model: str) -> dict | None:
    """The table row for ``model`` under ``table_provider``; rows are keyed bare or "<provider>/<model>"."""
    for key in (model, f"{table_provider}/{model}"):
        row = _table().get(key)
        if row is not None and row["provider"] == table_provider:
            return {name: row[name] for name in ("input", "output", "cache_read", "cache_write")}
    return None


def lookup_price(provider: str, model: str) -> dict[str, float | None] | None:
    """{"input", "output", "cache_read", "cache_write"} in USD per 1M tokens, or None.

    A None *value* inside the dict means the table does not state that price.
    """
    if provider in _UNPRICED:
        return None
    if provider == "openrouter":
        vendor, _, name = model.partition("/")
        return _row(_OPENROUTER_VENDORS[vendor], name) if vendor in _OPENROUTER_VENDORS and name else None
    table_provider = _TABLE_PROVIDER.get(provider)
    return _row(table_provider, model) if table_provider else None
