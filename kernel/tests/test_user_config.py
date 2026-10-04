from __future__ import annotations

import concurrent.futures
import errno
import json
import stat
import threading

import pytest

from kernel.tests.conftest import requires_posix

from kernel import durable_json
from kernel import user_config


def test_load_and_save_user_config(tmp_path):
    path = tmp_path / "config.json"
    user_config.save_user_config({"x": "y"}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"x": "y"}
    assert user_config.load_user_config(path) == {"x": "y"}
    assert user_config.load_user_config(tmp_path / "missing.json") == {}


def test_frontend_preference_round_trips_without_clobbering_settings(tmp_path):
    path = tmp_path / "config.json"
    assert user_config.get_saved_frontend(path) == "terminal"
    user_config.save_user_config({"other": "kept"}, path)
    user_config.save_frontend("tui", path)
    assert user_config.get_saved_frontend(path) == "tui"
    assert user_config.load_user_config(path)["other"] == "kept"
    user_config.save_frontend("Claude", path)
    assert user_config.get_saved_frontend(path) == "claude"
    user_config.save_frontend("terminal", path)
    assert user_config.get_saved_frontend(path) == "terminal"
    with pytest.raises(ValueError):
        user_config.save_frontend("stdio", path)


@pytest.mark.parametrize("value", [None, [], {}, "bogus", True])
def test_invalid_frontend_preference_falls_back(tmp_path, value):
    path = tmp_path / "config.json"
    user_config.save_user_config({"frontend": value}, path)
    assert user_config.get_saved_frontend(path) == "terminal"


@requires_posix
def test_user_config_file_is_owner_only(tmp_path):
    path = tmp_path / "config.json"

    user_config.save_user_config(
        {"openai_api_key": "fixture-not-a-real-key"},
        path,
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_user_config_recovers_previous_complete_generation(tmp_path):
    path = tmp_path / "config.json"
    user_config.save_user_config({"generation": 1}, path)
    user_config.save_user_config({"generation": 2}, path)
    path.write_text("{partial", encoding="utf-8")

    assert user_config.load_user_config(path) == {"generation": 2}
    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": 2}


def test_interrupted_user_config_replace_keeps_last_complete_value(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "config.json"
    user_config.save_user_config({"generation": 1}, path)
    original_replace = durable_json._replace_path

    def fail_primary_replace(source, destination):
        if destination == path:
            raise OSError(errno.ENOSPC, "simulated full disk")
        original_replace(source, destination)

    monkeypatch.setattr(
        durable_json,
        "_replace_path",
        fail_primary_replace,
    )

    with pytest.raises(RuntimeError, match="save user config"):
        user_config.save_user_config({"generation": 2}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"generation": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_user_config_symlink_parent_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-config"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="save user config"):
        user_config.save_user_config({"unsafe": True}, linked / "config.json")

    assert not (outside / "config.json").exists()


def test_concurrent_config_field_updates_do_not_clobber_each_other(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "config.json"
    write_barrier = threading.Barrier(2)
    original_save = user_config.save_user_config

    def synchronized_save(*args, **kwargs):
        write_barrier.wait(timeout=5)
        return original_save(*args, **kwargs)

    # This hook made the old load-then-save implementation deterministic.
    # Transactional field updates no longer call the whole-document saver.
    monkeypatch.setattr(
        user_config,
        "save_user_config",
        synchronized_save,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(user_config.save_role_selection, "coder", path),
            pool.submit(
                user_config.save_models_for_provider,
                "openai",
                {"fast": "fast-model"},
                path,
            ),
        ]
        for future in futures:
            future.result(timeout=5)

    payload = user_config.load_user_config(path)
    assert payload["role_selection"] == "coder"
    assert payload["llm_models"] == {
        "openai": {"fast": "fast-model"},
    }


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
    # A "planner" model saved by an older build is ignored, not an error.
    assert user_config.get_saved_models("freeflow", path) == {"fast": "f"}

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


def test_ensure_provider_credentials_uses_injected_prompt_secret_fn(tmp_path, monkeypatch):
    """A frontend with a real masked-input widget must be able to override the
    bare getpass() fallback, which has no visible feedback at all."""
    path = tmp_path / "config.json"
    calls = []

    def fake_ui_prompt(label: str) -> str:
        calls.append(label)
        return "ui-provided-key"

    # If the injected function were ignored, this would blow up: the bare
    # module-level prompt_secret is not mocked here.
    monkeypatch.setattr(
        user_config, "prompt_secret", lambda label: (_ for _ in ()).throw(AssertionError("fallback used"))
    )

    credentials = user_config.ensure_provider_credentials(
        provider="qwen",
        validation_model="qwen-max",
        required_keys=("qwen_api_key",),
        config_path=path,
        prompt_secret_fn=fake_ui_prompt,
    )

    assert credentials == {"qwen_api_key": "ui-provided-key"}
    assert calls == ["Qwen (DashScope) API key"]
    assert user_config.load_user_config(path)["qwen_api_key"] == "ui-provided-key"


def test_validate_api_key_wraps_openai_errors(monkeypatch):
    class Models:
        def list(self):
            raise RuntimeError("offline")

    monkeypatch.setattr(user_config, "OpenAI", lambda api_key: type("Client", (), {"models": Models()})())
    with pytest.raises(RuntimeError, match="Failed to validate"):
        user_config.validate_api_key("bad", "model")


def test_manual_provider_env_key_aliases(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setenv("QWEN_API_KEY", "env-qwen-key")
    monkeypatch.setenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    credentials = user_config.load_available_credentials(
        ("qwen_api_key", "qwen_base_url"),
        path,
    )

    assert credentials["qwen_api_key"] == "env-qwen-key"
    assert credentials["qwen_base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_manual_provider_secret_label():
    assert user_config.secret_label_for_key("qwen_api_key") == "Qwen (DashScope) API key"


def test_manual_provider_ensure_credentials_persists_base_url(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "prompt_secret", lambda label: "moonshot-secret")
    user_config.save_user_config({"moonshot_base_url": "https://api.moonshot.cn/v1"}, path)

    credentials = user_config.ensure_provider_credentials(
        provider="moonshot",
        validation_model="kimi-k2",
        required_keys=("moonshot_api_key",),
        config_path=path,
    )

    assert credentials["moonshot_api_key"] == "moonshot-secret"
    assert credentials["moonshot_base_url"] == "https://api.moonshot.cn/v1"


def test_manual_provider_ensure_credentials_missing_key_raises(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(user_config, "prompt_secret", lambda label: "")

    with pytest.raises(RuntimeError, match="Moonshot AI"):
        user_config.ensure_provider_credentials(
            provider="moonshot",
            validation_model="kimi-k2",
            required_keys=("moonshot_api_key",),
            config_path=path,
        )


def test_prompt_provider_choice_accepts_manual_provider_name(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "qwen")
    assert user_config.prompt_provider_choice() == "qwen"


def test_prompt_provider_choice_offers_anymodel_first(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    assert user_config.prompt_provider_choice() == "anymodel"
    assert "1. AnyModel" in capsys.readouterr().out


def test_prompt_provider_choice_accepts_legacy_numbered_options(monkeypatch):
    # AnyModel took option 1, so the old option 2 (OpenAI) is 2 and Anthropic is 3.
    monkeypatch.setattr("builtins.input", lambda prompt="": "3")
    assert user_config.prompt_provider_choice() == "anthropic"


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
            "models": {"fast": "llama-4", "smart": "llama-4-pro"},
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


def test_clear_saved_provider(tmp_path):
    path = tmp_path / "config.json"

    user_config.save_user_config({"llm_provider": "custom:gone", "gemini_api_key": "keep-me"}, path)
    user_config.clear_saved_provider(path)

    payload = user_config.load_user_config(path)
    assert "llm_provider" not in payload
    assert payload["gemini_api_key"] == "keep-me"

    # Clearing again with nothing saved is a no-op, not an error.
    user_config.clear_saved_provider(path)
    assert "llm_provider" not in user_config.load_user_config(path)


def test_reset_user_config(tmp_path):
    path = tmp_path / "config.json"

    user_config.save_user_config(
        {
            "llm_provider": "openai",
            "openai_api_key": "sk-test",
            "custom_providers": {"my-llama": {"label": "My Llama", "base_url": "http://x/v1"}},
        },
        path,
    )
    user_config.reset_user_config(path)

    assert user_config.load_user_config(path) == {}
    assert user_config.get_custom_providers(path) == {}
    assert user_config.get_saved_provider_or_none(path) is None


def test_custom_provider_invalid_entries_filtered(tmp_path):
    path = tmp_path / "config.json"

    # Write raw config with invalid entries
    from kernel.user_config import save_user_config
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
