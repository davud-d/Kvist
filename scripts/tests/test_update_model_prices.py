# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "update_model_prices.py"
_SPEC = importlib.util.spec_from_file_location("update_model_prices", _PATH)
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

_LITELLM = {
    "sample_spec": {"input_cost_per_token": 0.0, "output_cost_per_token": 0.0},
    "claude-sonnet-5": {
        "litellm_provider": "anthropic",
        "input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05,
        "cache_read_input_token_cost": 2e-07, "cache_creation_input_token_cost": 2.5e-06,
        "max_output_tokens": 128000,
    },
    "plain-model": {"litellm_provider": "openai", "input_cost_per_token": 3e-06, "output_cost_per_token": 6e-06},
    "free-cache": {"litellm_provider": "deepseek", "input_cost_per_token": 3e-07,
                   "output_cost_per_token": 1.2e-06, "cache_creation_input_token_cost": 0.0,
                   "max_output_tokens": "unlimited"},
    "image-model": {"litellm_provider": "openai", "output_cost_per_image": 0.04, "mode": "image_generation"},
}


def test_prices_become_dollars_per_million_tokens():
    models = script.convert(_LITELLM)["models"]

    assert models["claude-sonnet-5"] == {
        "provider": "anthropic", "input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5,
        "max_output_tokens": 128000,
    }


def test_the_output_ceiling_is_copied_and_unstated_or_unusable_ones_are_null():
    models = script.convert(_LITELLM)["models"]

    assert models["claude-sonnet-5"]["max_output_tokens"] == 128000
    assert models["plain-model"]["max_output_tokens"] is None
    assert models["free-cache"]["max_output_tokens"] is None


def test_missing_prices_are_null_and_a_stated_zero_stays_zero():
    models = script.convert(_LITELLM)["models"]

    assert models["plain-model"]["cache_read"] is None
    assert models["plain-model"]["cache_write"] is None
    assert models["free-cache"]["cache_write"] == 0.0


def test_entries_without_a_token_price_and_the_spec_sample_are_left_out():
    models = script.convert(_LITELLM)["models"]

    assert "image-model" not in models
    assert "sample_spec" not in models


def test_main_reads_a_file_and_writes_the_table(tmp_path, monkeypatch):
    source = tmp_path / "litellm.json"
    source.write_text(json.dumps(_LITELLM), encoding="utf-8")
    monkeypatch.setattr(script, "OUTPUT", tmp_path / "out.json")

    assert script.main(["update_model_prices.py", str(source)]) == 0

    written = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert written["unit"] == "usd_per_1m_tokens"
    assert set(written["models"]) == {"claude-sonnet-5", "plain-model", "free-cache"}
