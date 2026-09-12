# -*- coding: utf-8 -*-
"""Budget sizing: what a wrong number here costs is a 400 on every long step.

These tests aim at the ways the sizing can be wrong, not at restating the
table. A test that asserts ``input_limit_for("x") == 272_000`` next to a table
saying 272_000 cannot fail for any reason worth knowing about.
"""
from __future__ import annotations

import pytest

import llm_clients
from context_budget import (
    DEFAULT_INPUT_LIMIT,
    FREEFLOW_PROMPT_BUDGET,
    MODEL_INPUT_LIMITS,
    _normalize_model,
    derived_prompt_budget,
    env_prompt_budget,
    input_limit_for,
    learned_budget_for,
    note_input_overflow,
    reset_learned_budgets,
)


@pytest.fixture(autouse=True)
def _clean_learned_state(monkeypatch):
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    reset_learned_budgets()
    yield
    reset_learned_budgets()


# ── the one fact the table exists to encode ─────────────────────────────────

def test_openai_is_sized_by_input_not_by_window():
    """The distinction the whole module turns on.

    GPT-5 reserves its 128k max output inside the advertised window, so a
    400k-window model refuses a 300k prompt. Sizing it off the window sends a
    prompt the API rejects outright, on every step, forever.
    """
    assert input_limit_for("gpt-5.4-mini") == 272_000
    assert derived_prompt_budget("openai", "gpt-5.4-mini") < 272_000

    # Anthropic bounds the input alone, so there the window *is* the limit.
    assert input_limit_for("claude-sonnet-5") == 1_000_000


# ── adversarial model ids ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "written,canonical",
    [
        ("  GPT-5.4-Mini  ", "gpt-5.4-mini"),
        ("anthropic/claude-opus-5", "claude-opus-5"),
        ("openrouter/anthropic/claude-opus-5", "claude-opus-5"),
        ("DeepSeek/DeepSeek-V4-Pro", "deepseek-v4-pro"),
    ],
)
def test_an_id_is_sized_the_same_however_it_is_spelled(written, canonical):
    """Case, padding and routing prefixes must not change the size.

    A gateway id that silently missed the table would fall to the generic
    limit and quietly cut the budget by four.
    """
    assert input_limit_for(written) == input_limit_for(canonical)


def test_a_small_tier_never_inherits_its_full_size_sibling():
    """`gpt-5.5-mini` prefix-matches `gpt-5.5`.

    Longest-prefix matching alone hands the 400k model a million-token budget,
    so the small-tier rule has to run before the table. Adding any new
    full-size id to the table re-arms this trap.
    """
    for family in ("gpt-5.5", "gpt-5.4", "gpt-5.6-sol", "gpt-6-astra"):
        assert input_limit_for(family) > 800_000, family
        for suffix in ("-mini", "-nano"):
            assert input_limit_for(family + suffix) == 272_000, family + suffix


def test_every_full_size_openai_entry_is_protected_by_that_rule():
    """Guards the table itself, not a fixed list: a future entry gets covered
    without anyone remembering to extend this test."""
    for prefix, limit in MODEL_INPUT_LIMITS.items():
        if not prefix.startswith(("gpt-5", "gpt-6", "gpt-7")) or limit <= 400_000:
            continue
        assert input_limit_for(f"{prefix}-mini") == 272_000, prefix


def test_a_newer_version_is_not_sized_by_an_older_generation():
    """A generic entry must not shadow the family fallback.

    A bare "gpt-5" entry prefix-matches "gpt-5.9-nova" too, so every model
    released into that line after this table was written silently got the
    legacy 272k instead of its real million. Short budgets are invisible and
    permanent; generous ones are corrected by the first rejection.
    """
    for unlisted in ("gpt-5.9-nova", "gpt-5.7-sol", "gpt-6-nova"):
        assert input_limit_for(unlisted) > 800_000, unlisted

    # ...while the legacy small tier it was there to cover still resolves.
    assert input_limit_for("gpt-5-mini") == 272_000


def test_no_entry_is_shadowed_by_a_shorter_one():
    """Every table entry must resolve to its own value, not a neighbour's."""
    for prefix, limit in MODEL_INPUT_LIMITS.items():
        assert input_limit_for(prefix.rstrip("-")) == limit or prefix.endswith("-"), prefix


def test_an_unknown_id_is_never_sized_above_its_family():
    """The family fallback is a guess. It must stay a *conservative* one: a new
    Haiku must not inherit Opus's million."""
    assert input_limit_for("claude-haiku-99") == 200_000
    assert input_limit_for("claude-haiku-next-gen") == 200_000
    assert input_limit_for("some-vendor-model") == DEFAULT_INPUT_LIMIT


# ── learning from a rejection ───────────────────────────────────────────────

@pytest.mark.parametrize("attempted", [1, 2, 10, 5_000, 7_999, 8_000, 100_000, 900_000])
def test_a_rejection_never_yields_a_budget_at_or_above_what_was_refused(attempted):
    """The exact contract: at most ``attempted - 1``, floored at 1.

    A floor of 8_000 applied here answered a refused 5k prompt with an 8k
    budget, so the retry resent something *larger* and bought the same
    rejection twice. Stating the bound as a formula rather than a threshold
    keeps that from being reintroduced at any other size.
    """
    reduced = note_input_overflow("m", attempted)
    assert 1 <= reduced <= max(1, attempted - 1)


def test_an_unshrinkable_prompt_is_the_callers_problem_not_a_silent_loop():
    """At the floor there is nothing left to give back, so the budget stops
    moving. ``call_model`` is what must then stop retrying — see
    ``test_overflow_does_not_resend_an_identical_prompt``."""
    assert note_input_overflow("m", 1) == 1
    assert note_input_overflow("m", 1) == 1


def test_repeated_rejections_converge_and_never_loosen():
    note_input_overflow("m", 100_000)
    first = learned_budget_for("m")
    note_input_overflow("m", 90_000)
    second = learned_budget_for("m")
    assert second < first

    # A later, larger rejection is not evidence the model grew.
    note_input_overflow("m", 900_000)
    assert learned_budget_for("m") == second


def test_learning_is_per_model_and_does_not_leak():
    note_input_overflow("model-a", 100_000)
    assert learned_budget_for("model-b") is None


def test_a_rejection_outranks_every_override():
    """Otherwise the retry resends exactly the prompt that was refused."""
    from types import SimpleNamespace

    import agent_model

    agent = SimpleNamespace(
        config=SimpleNamespace(
            provider="openai", fast_model="gpt-5.6-luna", max_context_tokens=900_000
        ),
        _state=SimpleNamespace(selected_action_model=""),
    )
    assert agent_model.context_token_limit(agent) == 900_000
    note_input_overflow("gpt-5.6-luna", 500_000)
    assert agent_model.context_token_limit(agent) == 300_000


# ── invariants over the shipped catalog ─────────────────────────────────────

def test_the_budget_always_leaves_headroom_under_the_limit():
    """Kvist counts with tiktoken; the provider counts with its own tokenizer.
    A budget equal to the limit is a budget that overshoots it."""
    ids = [prefix for prefix in MODEL_INPUT_LIMITS]
    ids += ["unknown-model", "claude-haiku-99", "gpt-9-future"]
    for model in ids:
        assert derived_prompt_budget("openai", model) < input_limit_for(model), model


def test_every_shipped_model_has_an_explicit_entry():
    """The family fallback is for ids discovered at launch. A model Kvist lists
    in its own picker must be sized from the docs."""
    for provider in ("openai", "anthropic", "deepseek", "openrouter", "freeflow"):
        for item in llm_clients.get_model_options(provider):
            normalized = _normalize_model(item["value"])
            assert any(
                normalized.startswith(prefix) for prefix in MODEL_INPUT_LIMITS
            ), item["value"]


def test_freeflow_limit_is_not_a_model_property():
    """It is a per-request cap on the free backends, so it holds whatever model
    is selected — including one whose own window is a million."""
    for model in ("gemini-3.8-flash", "openai/gpt-oss-120b", "deepseek-flash"):
        assert derived_prompt_budget("freeflow", model) == FREEFLOW_PROMPT_BUDGET


def test_env_override_ignores_junk_rather_than_crashing():
    import os

    for raw in ("not-a-number", "", "-5", "1e9"):
        os.environ["AGENT_MAX_CONTEXT_TOKENS"] = raw
        assert env_prompt_budget() >= 0
    del os.environ["AGENT_MAX_CONTEXT_TOKENS"]
