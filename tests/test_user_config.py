from __future__ import annotations

import json

import pytest

import user_config


def test_load_and_save_user_config(tmp_path):
    path = tmp_path / "config.json"
    user_config.save_user_config({"x": "y"}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"x": "y"}
    assert user_config.load_user_config(path) == {"x": "y"}
    assert user_config.load_user_config(tmp_path / "missing.json") == {}


def test_load_user_config_rejects_invalid_json_and_non_object(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid"):
        user_config.load_user_config(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="invalid"):
        user_config.load_user_config(path)


def test_api_key_and_provider_model_persistence(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    user_config.save_user_config({"OPENAI_API_KEY": " legacy ", "llm_provider": "freeflow"}, path)

    assert user_config.load_api_key(path) == "legacy"
    assert user_config.get_saved_provider(path) == "freeflow"

    user_config.save_models_for_provider("freeflow", {"fast": " f ", "smart": "", "planner": "p"}, path)
    assert user_config.get_saved_models("freeflow", path) == {"fast": "f", "planner": "p"}

    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    assert user_config.load_available_credentials(("openai_api_key",), tmp_path / "empty.json") == {"openai_api_key": "env-key"}


def test_ensure_provider_credentials_uses_prompt_and_mocked_validation(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "prompt_secret", lambda label: "secret-key")
    monkeypatch.setattr(user_config, "validate_api_key", lambda api_key, model, language_hint=None: None)

    credentials = user_config.ensure_provider_credentials(
        provider="openai",
        validation_model="model",
        required_keys=("openai_api_key",),
        config_path=path,
    )

    assert credentials == {"openai_api_key": "secret-key"}
    assert user_config.load_user_config(path)["openai_api_key"] == "secret-key"


def test_validate_api_key_wraps_openai_errors(monkeypatch):
    class Models:
        def list(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(user_config, "OpenAI", lambda api_key: type("Client", (), {"models": Models()})())
    with pytest.raises(RuntimeError, match="Failed to validate"):
        user_config.validate_api_key("bad", "model")


def test_omniroute_env_key_aliases(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setenv("OMNIROUTE_API_KEY", "env-omniroute-key")
    monkeypatch.setenv("OMNIROUTE_BASE_URL", "http://custom:8080/v1")

    credentials = user_config.load_available_credentials(
        ("omniroute_api_key", "omniroute_base_url"),
        path,
    )

    assert credentials["omniroute_api_key"] == "env-omniroute-key"
    assert credentials["omniroute_base_url"] == "http://custom:8080/v1"


def test_omniroute_ensure_credentials_no_validation(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "prompt_secret", lambda label: "omniroute-secret")

    # OmniRoute should not call validate_api_key
    validation_called = []
    monkeypatch.setattr(
        user_config,
        "validate_api_key",
        lambda api_key, model, language_hint=None: validation_called.append(True),
    )

    credentials = user_config.ensure_provider_credentials(
        provider="omniroute",
        validation_model="kr/claude-sonnet-4.5",
        required_keys=("omniroute_api_key",),
        config_path=path,
    )

    assert credentials["omniroute_api_key"] == "omniroute-secret"
    assert validation_called == []  # Should not validate for OmniRoute


def test_omniroute_base_url_loaded(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    user_config.save_user_config(
        {
            "omniroute_api_key": "stored-key",
            "omniroute_base_url": "http://localhost:20128/v1",
        },
        path,
    )

    credentials = user_config.ensure_provider_credentials(
        provider="omniroute",
        validation_model="kr/claude-sonnet-4.5",
        required_keys=("omniroute_api_key",),
        config_path=path,
    )

    assert credentials["omniroute_api_key"] == "stored-key"
    assert credentials["omniroute_base_url"] == "http://localhost:20128/v1"


# ── OpenRouter tests ──────────────────────────────────────────────────────────


def test_openrouter_env_key_aliases(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-openrouter-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://custom.openrouter.ai/api/v1")

    credentials = user_config.load_available_credentials(
        ("openrouter_api_key", "openrouter_base_url"),
        path,
    )

    assert credentials["openrouter_api_key"] == "env-openrouter-key"
    assert credentials["openrouter_base_url"] == "https://custom.openrouter.ai/api/v1"


def test_openrouter_ensure_credentials_no_validation(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "prompt_secret", lambda label: "openrouter-secret")

    # OpenRouter should not call validate_api_key
    validation_called = []
    monkeypatch.setattr(
        user_config,
        "validate_api_key",
        lambda api_key, model, language_hint=None: validation_called.append(True),
    )

    credentials = user_config.ensure_provider_credentials(
        provider="openrouter",
        validation_model="openai/gpt-5.4-mini",
        required_keys=("openrouter_api_key",),
        config_path=path,
    )

    assert credentials["openrouter_api_key"] == "openrouter-secret"
    assert validation_called == []  # Should not validate for OpenRouter


def test_openrouter_base_url_loaded(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    user_config.save_user_config(
        {
            "openrouter_api_key": "stored-or-key",
            "openrouter_base_url": "https://openrouter.ai/api/v1",
        },
        path,
    )

    credentials = user_config.ensure_provider_credentials(
        provider="openrouter",
        validation_model="openai/gpt-5.4-mini",
        required_keys=("openrouter_api_key",),
        config_path=path,
    )

    assert credentials["openrouter_api_key"] == "stored-or-key"
    assert credentials["openrouter_base_url"] == "https://openrouter.ai/api/v1"


# ── Custom provider tests ────────────────────────────────────────────────────


def test_save_and_load_custom_provider(tmp_path):
    path = tmp_path / "config.json"

    # Save a custom provider
    user_config.save_custom_provider(
        "my-llama",
        {
            "label": "My Llama",
            "base_url": "http://localhost:8080/v1",
            "api_key": "sk-test-123",
            "models": {"fast": "llama-4", "smart": "llama-4-pro", "planner": "llama-4-pro"},
        },
        path,
    )

    # Load and verify
    providers = user_config.get_custom_providers(path)
    assert "my-llama" in providers
    info = providers["my-llama"]
    assert info["label"] == "My Llama"
    assert info["base_url"] == "http://localhost:8080/v1"
    assert info["api_key"] == "sk-test-123"
    assert info["models"]["fast"] == "llama-4"
    assert info["models"]["smart"] == "llama-4-pro"
    assert info["models"]["planner"] == "llama-4-pro"

    # Get by name
    single = user_config.get_custom_provider_by_name("my-llama", path)
    assert single is not None
    assert single["base_url"] == "http://localhost:8080/v1"

    # Get missing
    assert user_config.get_custom_provider_by_name("nonexistent", path) is None


def test_delete_custom_provider(tmp_path):
    path = tmp_path / "config.json"

    user_config.save_custom_provider(
        "test-prov",
        {
            "label": "Test Provider",
            "base_url": "http://localhost:1234/v1",
            "api_key": "",
            "models": {},
        },
        path,
    )
    assert "test-prov" in user_config.get_custom_providers(path)

    # Delete
    result = user_config.delete_custom_provider("test-prov", path)
    assert result is True
    assert "test-prov" not in user_config.get_custom_providers(path)

    # Delete non-existent
    result = user_config.delete_custom_provider("nonexistent", path)
    assert result is False


def test_custom_provider_invalid_entries_filtered(tmp_path):
    path = tmp_path / "config.json"

    # Write raw config with invalid entries
    from user_config import save_user_config
    save_user_config({
        "custom_providers": {
            "valid": {
                "label": "Valid",
                "base_url": "http://localhost:8080/v1",
                "api_key": "",
                "models": {},
            },
            "no_base_url": {
                "label": "Invalid",
                "base_url": "",
                "api_key": "",
                "models": {},
            },
            "not_a_dict": "just a string",
        },
    }, path)

    providers = user_config.get_custom_providers(path)
    assert "valid" in providers
    assert "no_base_url" not in providers  # filtered out - empty base_url
    assert "not_a_dict" not in providers   # filtered out - not a dict


def test_custom_provider_models_persistence(tmp_path):
    path = tmp_path / "config.json"

    # Save with models
    user_config.save_custom_provider(
        "local-gpt",
        {
            "label": "Local GPT",
            "base_url": "http://localhost:8000/v1",
            "api_key": "",
            "models": {"fast": "gpt-4", "smart": "gpt-4-turbo"},
        },
        path,
    )

    # Verify models field
    info = user_config.get_custom_provider_by_name("local-gpt", path)
    assert info["models"]["fast"] == "gpt-4"
    assert info["models"]["smart"] == "gpt-4-turbo"
    assert "planner" not in info["models"]  # wasn't set
