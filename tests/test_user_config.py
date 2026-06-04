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
