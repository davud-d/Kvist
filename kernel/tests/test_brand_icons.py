from __future__ import annotations

import pytest

from kernel.config import config_info
from kernel.config.brand_icons import model_icon, provider_icon
from kernel.llm.llm_clients import MANUAL_OPENAI_PROVIDERS


@pytest.mark.parametrize(
    ("provider", "brand"),
    [
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("chatgpt", "openai"),
        ("gemini", "google"),
        ("xiaomi-tokenplan", "xiaomi"),
        ("Anthropic", "anthropic"),
        ("custom:my-gateway", ""),
        ("", ""),
    ],
)
def test_provider_icon(provider, brand):
    assert provider_icon(provider) == brand


@pytest.mark.parametrize(
    ("model", "brand"),
    [
        ("claude-opus-5", "anthropic"),
        ("cc/claude-sonnet-5", "anthropic"),
        ("kr/claude-sonnet-4.5", "anthropic"),
        ("gpt-5.6-luna", "openai"),
        ("o3-mini", "openai"),
        ("ds-web/deepseek-v4-pro", "deepseek"),
        ("gemini-3.8-flash", "google"),
        ("qwen-3.8-27b", "qwen"),
        ("meta/llama-4-70b", "meta"),
        ("recursive/flint-v1.0:free", ""),
        ("", ""),
    ],
)
def test_model_icon(model, brand):
    assert model_icon(model) == brand


def test_every_builtin_provider_has_a_brand():
    # A provider added to the registry without a brand would show a blank tile.
    for provider in config_info._BUILTIN_PROVIDERS:
        assert provider_icon(provider), provider
    for provider in MANUAL_OPENAI_PROVIDERS:
        assert provider_icon(provider), provider


def test_config_info_names_the_brand_of_providers_and_their_models():
    info = config_info.build_config_info()
    by_id = {entry["id"]: entry for entry in info["providers"]}
    assert by_id["anthropic"]["icon"] == "anthropic"
    assert by_id["chatgpt"]["icon"] == "openai"
    assert all(entry["icon"] for entry in info["providers"])
    anthropic_options = by_id["anthropic"]["options"]
    assert anthropic_options
    assert {option["icon"] for option in anthropic_options} == {"anthropic"}
    assert by_id["anthropic"]["model_icons"]["claude-opus-5"] == "anthropic"
