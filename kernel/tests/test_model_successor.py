# -*- coding: utf-8 -*-
"""A model that left the provider's catalog is replaced by its successor, never by a stranger."""
from __future__ import annotations

import pytest

from kernel.llm.llm_clients import resolve_accessible_models, successor_in_catalog


@pytest.mark.parametrize(("retired", "catalog", "successor"), [
    # DeepSeek retiring V4: the Pro line's next version, wherever it is.
    ("deepseek-v4-pro", {"deepseek-flash", "deepseek-v4.1-pro"}, "deepseek-v4.1-pro"),
    ("deepseek-v4-pro", {"deepseek-flash", "deepseek-v5-pro", "deepseek-v4.1-pro"}, "deepseek-v5-pro"),
    # DeepSeek names the current line without a version: an alias of the family counts.
    ("deepseek-v4-flash", {"deepseek-flash", "deepseek-v4-pro"}, "deepseek-flash"),
    ("deepseek-v4-pro", {"deepseek-flash", "deepseek-pro"}, "deepseek-pro"),
    ("claude-sonnet-4-5", {"claude-sonnet-5", "claude-opus-5-5", "claude-haiku-5-5"}, "claude-sonnet-5"),
    ("gpt-6.2-sol", {"gpt-6.3-sol", "gpt-6.3-sol-mini", "gpt-6.3-luna"}, "gpt-6.3-sol"),
    ("kr/claude-sonnet-4.5", {"kr/claude-sonnet-4.6", "claude-sonnet-4.6"}, "kr/claude-sonnet-4.6"),
    ("qwen3-coder", {"qwen3.6-coder", "qwen3.6-27b"}, "qwen3.6-coder"),
])
def test_the_successor_is_the_newest_of_the_same_family(retired, catalog, successor):
    assert successor_in_catalog(retired, catalog) == successor


@pytest.mark.parametrize(("retired", "catalog"), [
    ("deepseek-v4-pro", {"deepseek-flash"}),                   # a pro never becomes a flash
    ("gpt-6.2-sol", {"gpt-6.3-sol-mini"}),                     # nor a model its smaller sibling
    ("deepseek-v4-pro", {"deepseek-v4-pro-vision"}),           # nor its variant
    ("llama-3.1-8b-instruct", {"llama-3.3-70b-instruct"}),     # a size is another model, not a version
    ("qwen3-235b-a22b", {"qwen3.6-30b-a3b"}),
    ("gpt-4o", {"gpt-5"}),                                     # letters inside a token are family
    ("deepseek-v4-pro", {"deepseek-v3-pro"}),                  # never an older version
    ("o3", {"gpt-6.3-sol"}),
])
def test_nothing_outside_the_family_or_older_is_offered(retired, catalog):
    assert successor_in_catalog(retired, catalog) is None


def test_launch_swaps_a_retired_model_and_says_so():
    models, warnings = resolve_accessible_models(
        "deepseek", {"fast": "deepseek-flash", "smart": "deepseek-v4-pro"},
        ["deepseek-flash", "deepseek-v4.1-pro"], language_hint="en")

    assert models == {"fast": "deepseek-flash", "smart": "deepseek-v4.1-pro"}
    assert len(warnings) == 1 and "likely retired" in warnings[0] and "/models" in warnings[0]


def test_with_no_successor_the_model_is_kept_and_the_way_out_named():
    """A gateway's catalog can be incomplete, so a missing model is not dropped blindly."""
    models, warnings = resolve_accessible_models(
        "custom:lab", {"smart": "house-model"}, ["other-model"], language_hint="ru")

    assert models == {"smart": "house-model"}
    assert "/models" in warnings[0]
