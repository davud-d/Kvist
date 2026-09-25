from __future__ import annotations

import pytest

import config_info
import user_config
from llm_clients import MANUAL_OPENAI_PROVIDERS

_CREDENTIAL_ENVS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "OMNIROUTE_API_KEY",
    "OPENROUTER_API_KEY",
    "AGENT_FAST_MODEL",
    "AGENT_SMART_MODEL",
    "AGENT_PLANNER_MODEL",
)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    # HOME/USERPROFILE are already redirected to tmp_path by the autouse
    # fixture in conftest.py.
    for env in _CREDENTIAL_ENVS:
        monkeypatch.delenv(env, raising=False)
    (tmp_path / ".kvist").mkdir()
    return tmp_path


def test_build_config_info_reports_saved_defaults_and_credentials(isolated_config):
    user_config.save_user_config(
        {
            "llm_provider": "openai",
            "openai_api_key": "fixture-not-a-real-key",
            "role_selection": "coder",
        }
    )

    info = config_info.build_config_info()

    assert info["ok"] is True
    assert info["saved_provider"] == "openai"
    assert info["saved_role"] == "coder"

    by_id = {provider["id"]: provider for provider in info["providers"]}
    assert set(by_id) == {
        "openai", "anthropic", "deepseek", "openrouter", "omniroute", "freeflow",
    } | set(MANUAL_OPENAI_PROVIDERS)
    assert [p["id"] for p in info["providers"]][:3] == ["openai", "anthropic", "deepseek"]

    qwen = by_id["qwen"]
    assert qwen["requires_manual_models"] is True
    assert qwen["options"] == []

    openai = by_id["openai"]
    assert openai["has_credentials"] is True
    assert openai["missing_keys"] == []
    assert openai["defaults"]["fast"]  # a real default model, not a placeholder
    assert openai["options"]  # selectable suggestions are exposed

    anthropic = by_id["anthropic"]
    assert anthropic["has_credentials"] is False
    assert anthropic["missing_keys"] == ["Anthropic API key"]


def test_build_config_info_defaults_to_auto_role(isolated_config):
    info = config_info.build_config_info()
    assert info["saved_role"] == "auto"
    assert info["custom_providers"] == []


def test_build_config_info_includes_custom_providers(isolated_config):
    user_config.save_user_config(
        {
            "llm_provider": "custom:deepseek",
            "custom_providers": {
                "deepseek": {
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "fixture-key",
                    "models": {
                        "fast": "deepseek-v4-pro",
                        "smart": "deepseek-v4-pro",
                        "planner": "deepseek-v4-pro",
                    },
                },
                "keyless": {
                    "base_url": "https://api.example.com/v1",
                    "models": {"fast": "m", "smart": "m", "planner": "m"},
                },
            },
        }
    )

    info = config_info.build_config_info()

    assert info["saved_provider"] == "custom:deepseek"
    by_id = {entry["id"]: entry for entry in info["custom_providers"]}
    assert set(by_id) == {"custom:deepseek", "custom:keyless"}

    deepseek = by_id["custom:deepseek"]
    assert deepseek["custom"] is True
    assert deepseek["requires_manual_models"] is True
    assert deepseek["base_url"] == "https://api.deepseek.com/v1"
    assert deepseek["has_credentials"] is True
    assert deepseek["defaults"]["smart"] == "deepseek-v4-pro"

    keyless = by_id["custom:keyless"]
    assert keyless["has_credentials"] is False
    assert keyless["missing_keys"] == ["API key"]
