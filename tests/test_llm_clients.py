from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

import llm_clients


def _fake_openai_models_client(ids_by_url: dict[str | None, list[str]], calls: list[str | None] | None = None):
    def factory(api_key, base_url=None, **kwargs):
        if calls is not None:
            calls.append(base_url)
        ids = ids_by_url.get(base_url, [])
        return SimpleNamespace(
            models=SimpleNamespace(
                list=lambda: SimpleNamespace(data=[SimpleNamespace(id=item) for item in ids])
            )
        )

    return factory


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    llm_clients.clear_model_discovery_cache()
    yield
    llm_clients.clear_model_discovery_cache()


def test_provider_and_model_normalization():
    assert llm_clients.normalize_provider("unknown") == "openai"
    assert llm_clients.provider_label("freeflow") == "FreeFlow LLM"
    assert llm_clients.normalize_model_name("freeflow", "gemini 3.1 pro") == "gemini-3.1-pro-preview"
    assert llm_clients.infer_freeflow_backend("qwen-3-32b") == "groq"
    assert llm_clients.is_model_supported_by_provider("freeflow", "not-a-model") is False
    assert llm_clients.is_model_deprecated("freeflow", "mixtral-8x7b-32768") is False


def test_resolve_models_for_provider_uses_env_and_warns(monkeypatch):
    monkeypatch.setenv("AGENT_FAST_MODEL", "bad-model")
    models, warnings = llm_clients.resolve_models_for_provider("freeflow", {})

    assert models["fast"] == "gemini-3.5-flash"
    assert warnings


def test_fallback_candidates_and_required_backends():
    assert llm_clients.fallback_candidates_for_model("openai", "custom") == ("custom",)
    assert "gpt-5-mini" in llm_clients.fallback_candidates_for_model("openai", "custom", True)
    assert llm_clients.required_backends_for_models({"fast": "gemini-3.1-pro-preview", "smart": "qwen-3-32b"}) == ("gemini", "groq")
    assert llm_clients.required_secret_keys_for_provider("openai", ["x"]) == ("openai_api_key",)
    assert llm_clients.required_secret_keys_for_provider("freeflow", ["qwen-3-32b"]) == ("groq_api_key",)


def test_validate_model_selection_reports_missing_roles_and_backends():
    issues = llm_clients.validate_model_selection(
        "freeflow",
        {"fast": "gemini-3.1-pro-preview", "smart": "", "planner": "qwen-3-32b"},
        {"gemini_api_key": "key"},
    )

    assert any("No model" in issue for issue in issues)
    assert any("Groq" in issue for issue in issues)


def test_openai_accessible_model_resolution_is_mocked(monkeypatch):
    class Models:
        def list(self):
            return SimpleNamespace(data=[SimpleNamespace(id="gpt-5-mini")])

    monkeypatch.setattr(llm_clients, "OpenAI", lambda api_key: SimpleNamespace(models=Models()))
    models, warnings = llm_clients.resolve_openai_accessible_models(
        "key",
        {"fast": "missing", "smart": "gpt-5-mini"},
    )

    assert models["fast"] == "gpt-5-mini"
    assert warnings


def test_create_llm_client_openai_is_mocked(monkeypatch):
    monkeypatch.setattr(llm_clients, "OpenAI", lambda api_key, **kwargs: ("client", api_key))
    assert llm_clients.create_llm_client("openai", {"openai_api_key": "key"})[0] == "client"
    assert llm_clients.create_llm_client("openai", {"openai_api_key": "key"})[1] == "key"


def test_omniroute_provider_normalization():
    assert llm_clients.normalize_provider("omniroute") == "omniroute"
    assert llm_clients.normalize_provider("omni") == "omniroute"
    assert llm_clients.normalize_provider("or") == "omniroute"
    assert llm_clients.normalize_provider("9router") == "omniroute"
    assert llm_clients.provider_label("omniroute") == "OmniRoute"


def test_omniroute_model_options():
    # OmniRoute returns empty list - signals manual input needed
    assert llm_clients.get_model_options("omniroute") == []


def test_omniroute_fallback_models():
    # OmniRoute has no fallback chain
    assert llm_clients.fallback_models_for_provider("omniroute") == ()
    assert llm_clients.fallback_candidates_for_model("omniroute", "kr/claude-sonnet-4.5") == ("kr/claude-sonnet-4.5",)
    assert llm_clients.fallback_candidates_for_model("omniroute", "kr/claude-sonnet-4.5", True) == ("kr/claude-sonnet-4.5",)


def test_omniroute_model_validation():
    # Valid model ids
    assert llm_clients.is_valid_omniroute_model_id("kr/claude-sonnet-4.5") is True
    assert llm_clients.is_valid_omniroute_model_id("kr/claude-haiku-4.5") is True
    assert llm_clients.is_valid_omniroute_model_id("if/qwen3-coder-plus") is True
    assert llm_clients.is_valid_omniroute_model_id("auto") is True
    assert llm_clients.is_valid_omniroute_model_id("my-combo") is True

    # Invalid model ids
    assert llm_clients.is_valid_omniroute_model_id("") is False
    assert llm_clients.is_valid_omniroute_model_id("bad model") is False
    assert llm_clients.is_valid_omniroute_model_id("https://example.com") is False
    assert llm_clients.is_valid_omniroute_model_id("http://localhost") is False
    assert llm_clients.is_valid_omniroute_model_id("provider/") is False
    assert llm_clients.is_valid_omniroute_model_id("/model") is False
    assert llm_clients.is_valid_omniroute_model_id("provider//model") is False
    assert llm_clients.is_valid_omniroute_model_id('model"with"quotes') is False
    assert llm_clients.is_valid_omniroute_model_id("model\nwith\nnewline") is False


def test_omniroute_is_model_supported():
    assert llm_clients.is_model_supported_by_provider("omniroute", "kr/claude-sonnet-4.5") is True
    assert llm_clients.is_model_supported_by_provider("omniroute", "auto") is True
    assert llm_clients.is_model_supported_by_provider("omniroute", "bad model") is False
    assert llm_clients.is_model_supported_by_provider("omniroute", "https://x") is False


def test_omniroute_normalize_model_name():
    # OmniRoute only strips, no aliasing or case changes
    assert llm_clients.normalize_model_name("omniroute", "  kr/claude-sonnet-4.5  ") == "kr/claude-sonnet-4.5"
    assert llm_clients.normalize_model_name("omniroute", "kr/claude-sonnet-4.5") == "kr/claude-sonnet-4.5"


def test_omniroute_required_secret_keys():
    assert llm_clients.required_secret_keys_for_provider("omniroute", ["kr/claude-sonnet-4.5"]) == ("omniroute_api_key",)


def test_omniroute_resolve_models_no_defaults(monkeypatch):
    # OmniRoute has no defaults - only saved models and env
    models, warnings = llm_clients.resolve_models_for_provider("omniroute", {})
    assert models == {}
    assert warnings == []

    # With saved models
    models, warnings = llm_clients.resolve_models_for_provider(
        "omniroute",
        {"fast": "kr/claude-haiku-4.5", "smart": "kr/claude-sonnet-4.5", "planner": "kr/claude-sonnet-4.5"},
    )
    assert models["fast"] == "kr/claude-haiku-4.5"
    assert models["smart"] == "kr/claude-sonnet-4.5"
    assert models["planner"] == "kr/claude-sonnet-4.5"
    assert warnings == []

    # With invalid model
    models, warnings = llm_clients.resolve_models_for_provider(
        "omniroute",
        {"fast": "bad model", "smart": "kr/claude-sonnet-4.5"},
    )
    assert "fast" not in models
    assert models["smart"] == "kr/claude-sonnet-4.5"
    assert len(warnings) == 1
    assert "bad model" in warnings[0]


def test_omniroute_create_client(monkeypatch):
    created_clients = []

    def mock_openai(api_key, base_url=None, **kwargs):
        created_clients.append({"api_key": api_key, "base_url": base_url})
        return f"client-{api_key}"

    monkeypatch.setattr(llm_clients, "OpenAI", mock_openai)

    # With base URL
    client = llm_clients.create_llm_client(
        "omniroute",
        {"omniroute_api_key": "test-key", "omniroute_base_url": "http://localhost:20128/v1"},
    )
    assert client == "client-test-key"
    assert created_clients[-1]["api_key"] == "test-key"
    assert created_clients[-1]["base_url"] == "http://localhost:20128/v1"

    # Without base URL - should use default
    client = llm_clients.create_llm_client("omniroute", {"omniroute_api_key": "test-key2"})
    assert client == "client-test-key2"
    assert created_clients[-1]["api_key"] == "test-key2"
    assert created_clients[-1]["base_url"] == "http://localhost:20128/v1"


# ── OpenRouter tests ──────────────────────────────────────────────────────────


def test_openrouter_provider_normalization():
    assert llm_clients.normalize_provider("openrouter") == "openrouter"
    assert llm_clients.normalize_provider("openroute") == "openrouter"
    assert llm_clients.normalize_provider("openrt") == "openrouter"
    assert llm_clients.normalize_provider("router") == "openrouter"
    assert llm_clients.provider_label("openrouter") == "OpenRouter"


def test_openrouter_model_options():
    # OpenRouter returns predefined model options (unlike OmniRoute)
    options = llm_clients.get_model_options("openrouter")
    assert len(options) > 0
    assert any(item["value"] == "anthropic/claude-sonnet-4-6" for item in options)
    assert any(item["value"] == "openai/gpt-5.4-mini" for item in options)
    # Verify all options have required keys
    for item in options:
        assert "value" in item
        assert "label" in item
        assert "description" in item


def test_openrouter_fallback_models():
    # OpenRouter has no fallback chain (like OmniRoute)
    assert llm_clients.fallback_models_for_provider("openrouter") == ()
    assert llm_clients.fallback_candidates_for_model("openrouter", "anthropic/claude-sonnet-4-6") == ("anthropic/claude-sonnet-4-6",)
    assert llm_clients.fallback_candidates_for_model("openrouter", "anthropic/claude-sonnet-4-6", True) == ("anthropic/claude-sonnet-4-6",)


def test_openrouter_model_validation():
    # OpenRouter reuses OmniRoute validator (same provider/model format)
    assert llm_clients.is_model_supported_by_provider("openrouter", "anthropic/claude-sonnet-4-6") is True
    assert llm_clients.is_model_supported_by_provider("openrouter", "openai/gpt-5.4-mini") is True
    assert llm_clients.is_model_supported_by_provider("openrouter", "google/gemini-3.5-flash") is True
    assert llm_clients.is_model_supported_by_provider("openrouter", "auto") is True
    assert llm_clients.is_model_supported_by_provider("openrouter", "") is False
    assert llm_clients.is_model_supported_by_provider("openrouter", "bad model") is False
    assert llm_clients.is_model_supported_by_provider("openrouter", "https://x.com") is False


def test_omniroute_multi_segment_model_id():
    # OmniRoute accepts multi-segment ids like backend/vendor/model
    assert llm_clients.is_model_supported_by_provider("omniroute", "groq/openai/gpt-oss-120b") is True
    assert llm_clients.validate_omniroute_model_id("groq/openai/gpt-oss-120b") is None
    assert llm_clients.normalize_model_name("omniroute", "  groq/openai/gpt-oss-120b  ") == "groq/openai/gpt-oss-120b"
    # Empty segments are still rejected
    assert llm_clients.is_model_supported_by_provider("omniroute", "groq//gpt-oss-120b") is False
    assert llm_clients.is_model_supported_by_provider("omniroute", "groq/openai/") is False


def test_openrouter_normalize_model_name():
    # OpenRouter only strips whitespace, preserves case (provider/model format)
    assert llm_clients.normalize_model_name("openrouter", "  anthropic/claude-sonnet-4-6  ") == "anthropic/claude-sonnet-4-6"
    assert llm_clients.normalize_model_name("openrouter", "openai/gpt-5.4-mini") == "openai/gpt-5.4-mini"


def test_openrouter_required_secret_keys():
    assert llm_clients.required_secret_keys_for_provider("openrouter", ["openai/gpt-5.4-mini"]) == ("openrouter_api_key",)


def test_openrouter_resolve_models_no_defaults(monkeypatch):
    # OpenRouter has no defaults - only saved models and env
    models, warnings = llm_clients.resolve_models_for_provider("openrouter", {})
    assert models == {}
    assert warnings == []

    # With saved models
    models, warnings = llm_clients.resolve_models_for_provider(
        "openrouter",
        {"fast": "anthropic/claude-haiku-4-5", "smart": "anthropic/claude-sonnet-4-6", "planner": "anthropic/claude-sonnet-4-6"},
    )
    assert models["fast"] == "anthropic/claude-haiku-4-5"
    assert models["smart"] == "anthropic/claude-sonnet-4-6"
    assert models["planner"] == "anthropic/claude-sonnet-4-6"
    assert warnings == []

    # With env override
    monkeypatch.setenv("AGENT_FAST_MODEL", "openai/gpt-5.4-mini")
    models, warnings = llm_clients.resolve_models_for_provider("openrouter", {})
    assert models["fast"] == "openai/gpt-5.4-mini"


def test_openrouter_create_client(monkeypatch):
    created_clients = []

    def mock_openai(api_key, base_url=None, **kwargs):
        created_clients.append({"api_key": api_key, "base_url": base_url})
        return f"client-{api_key}"

    monkeypatch.setattr(llm_clients, "OpenAI", mock_openai)

    # With base URL
    client = llm_clients.create_llm_client(
        "openrouter",
        {"openrouter_api_key": "sk-or-test", "openrouter_base_url": "https://custom.openrouter.ai/api/v1"},
    )
    assert client == "client-sk-or-test"
    assert created_clients[-1]["api_key"] == "sk-or-test"
    assert created_clients[-1]["base_url"] == "https://custom.openrouter.ai/api/v1"

    # Without base URL - should use default
    client = llm_clients.create_llm_client("openrouter", {"openrouter_api_key": "sk-or-test2"})
    assert client == "client-sk-or-test2"
    assert created_clients[-1]["api_key"] == "sk-or-test2"
    assert created_clients[-1]["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_supports_streaming():
    assert llm_clients.provider_supports_streaming("openrouter") is True
    assert llm_clients.provider_supports_streaming("openai") is True
    assert llm_clients.provider_supports_streaming("omniroute") is False
    assert llm_clients.provider_supports_streaming("freeflow") is False


# ── Custom provider tests ────────────────────────────────────────────────────


def test_is_custom_provider():
    assert llm_clients.is_custom_provider("custom:my-llama") is True
    assert llm_clients.is_custom_provider("custom:local-vllm") is True
    assert llm_clients.is_custom_provider("custom:test") is True
    assert llm_clients.is_custom_provider("openai") is False
    assert llm_clients.is_custom_provider("openrouter") is False
    assert llm_clients.is_custom_provider("omniroute") is False
    assert llm_clients.is_custom_provider("freeflow") is False
    assert llm_clients.is_custom_provider("") is False
    assert llm_clients.is_custom_provider("custom:") is True  # empty name, but prefix matches


def test_extract_custom_provider_name():
    assert llm_clients.extract_custom_provider_name("custom:my-llama") == "my-llama"
    assert llm_clients.extract_custom_provider_name("custom:local-vllm") == "local-vllm"
    assert llm_clients.extract_custom_provider_name("custom:test") == "test"
    assert llm_clients.extract_custom_provider_name("openai") == ""


def test_custom_provider_normalization():
    assert llm_clients.normalize_provider("custom:my-llama") == "custom:my-llama"
    assert llm_clients.normalize_provider("custom:test") == "custom:test"
    # "custom" without a name is a placeholder
    assert llm_clients.normalize_provider("custom") == "custom"


def test_custom_provider_label():
    label = llm_clients.provider_label("custom:my-llama")
    assert "my-llama" in label
    assert "Custom:" in label or "custom" in label.lower()


def test_custom_provider_streaming_is_opt_in(monkeypatch):
    monkeypatch.setattr(
        "user_config.get_custom_provider_by_name",
        lambda name: {"streaming": False},
    )
    assert llm_clients.provider_supports_streaming("custom:my-llama") is False

    monkeypatch.setattr(
        "user_config.get_custom_provider_by_name",
        lambda name: {"streaming": True},
    )
    assert llm_clients.provider_supports_streaming("custom:my-llama") is True


def test_custom_provider_model_options():
    assert llm_clients.get_model_options("custom:my-llama") == []


def test_custom_provider_fallback():
    assert llm_clients.fallback_models_for_provider("custom:my-llama") == ()
    assert llm_clients.fallback_candidates_for_model("custom:my-llama", "any-model") == ("any-model",)
    assert llm_clients.fallback_candidates_for_model("custom:my-llama", "any-model", True) == ("any-model",)


def test_custom_provider_model_support():
    assert llm_clients.is_model_supported_by_provider("custom:my-llama", "any-model") is True
    assert llm_clients.is_model_supported_by_provider("custom:my-llama", "") is False


def test_custom_provider_required_keys():
    assert llm_clients.required_secret_keys_for_provider("custom:my-llama", ["test-model"]) == ()


def test_custom_provider_resolve_models():
    # No saved models, no env → empty
    models, warnings = llm_clients.resolve_models_for_provider("custom:my-llama", {})
    assert models == {}
    assert warnings == []

    # With saved models
    models, warnings = llm_clients.resolve_models_for_provider(
        "custom:my-llama",
        {"fast": "llama-4", "smart": "llama-4-pro", "planner": "llama-4-pro"},
    )
    assert models["fast"] == "llama-4"
    assert models["smart"] == "llama-4-pro"


def test_custom_provider_create_client_without_api_key(monkeypatch):
    # The provider-creation dialog lets the API key be skipped for a local
    # endpoint; building the client must not crash on the empty key.
    created = []

    def mock_openai(api_key, base_url=None, **kwargs):
        created.append({"api_key": api_key, "base_url": base_url})
        return f"client-{api_key}"

    monkeypatch.setattr(llm_clients, "OpenAI", mock_openai)
    monkeypatch.setattr(
        "user_config.get_custom_provider_by_name",
        lambda name: {"api_key": "", "base_url": "http://localhost:8000/v1"},
    )

    client = llm_clients.create_llm_client("custom:local-vllm", {})
    assert client == "client-not-needed"
    assert created[-1]["api_key"]
    assert created[-1]["base_url"] == "http://localhost:8000/v1"


def test_custom_provider_normalize_model_name():
    assert llm_clients.normalize_model_name("custom:my-llama", "  llama-4  ") == "llama-4"
    assert llm_clients.normalize_model_name("custom:my-llama", "llama-4") == "llama-4"


# ── Anthropic provider tests ─────────────────────────────────────────────────


def test_anthropic_provider_normalization():
    assert llm_clients.normalize_provider("anthropic") == "anthropic"
    assert llm_clients.normalize_provider("claude") == "anthropic"
    assert llm_clients.normalize_provider("ant") == "anthropic"
    assert llm_clients.provider_label("anthropic") == "Anthropic"


def test_anthropic_model_options():
    options = llm_clients.get_model_options("anthropic")
    assert len(options) > 0
    values = [o["value"] for o in options]
    assert "claude-sonnet-4-6" in values
    assert "claude-haiku-4-5" in values
    assert "claude-opus-4-8" in values


def test_anthropic_model_defaults():
    models, warnings = llm_clients.resolve_models_for_provider("anthropic", {})
    assert models["fast"] == "claude-haiku-4-5"
    assert models["smart"] == "claude-sonnet-4-6"
    assert models["planner"] == "claude-sonnet-4-6"
    assert warnings == []


def test_anthropic_streaming_support():
    assert llm_clients.provider_supports_streaming("anthropic") is True


def test_anthropic_required_keys():
    assert llm_clients.required_secret_keys_for_provider("anthropic", ["claude-sonnet-4-6"]) == ("anthropic_api_key",)


def test_anthropic_model_support():
    assert llm_clients.is_model_supported_by_provider("anthropic", "claude-sonnet-4-6") is True
    assert llm_clients.is_model_supported_by_provider("anthropic", "any-model") is True


def test_anthropic_fallback():
    # Anthropic has fallback chain through defaults
    assert llm_clients.fallback_models_for_provider("anthropic") == ()
    assert llm_clients.fallback_candidates_for_model("anthropic", "claude-sonnet-4-6") == ("claude-sonnet-4-6",)


def test_anthropic_normalize_model_name():
    assert llm_clients.normalize_model_name("anthropic", "  claude-sonnet-4-6  ") == "claude-sonnet-4-6"


# ── DeepSeek provider tests ──────────────────────────────────────────────────


def test_deepseek_provider_normalization():
    assert llm_clients.normalize_provider("deepseek") == "deepseek"
    assert llm_clients.normalize_provider("ds") == "deepseek"
    assert llm_clients.provider_label("deepseek") == "DeepSeek"


def test_deepseek_model_options():
    options = llm_clients.get_model_options("deepseek")
    assert len(options) > 0
    values = [o["value"] for o in options]
    assert "deepseek-v4-pro" in values
    assert "deepseek-v4-flash" in values


def test_deepseek_model_defaults():
    models, warnings = llm_clients.resolve_models_for_provider("deepseek", {})
    assert models["fast"] == "deepseek-v4-flash"
    assert models["smart"] == "deepseek-v4-pro"
    assert models["planner"] == "deepseek-v4-pro"
    assert warnings == []


def test_deepseek_streaming_and_json_mode():
    assert llm_clients.provider_supports_streaming("deepseek") is True
    assert llm_clients.provider_supports_json_mode("deepseek") is True


def test_deepseek_required_keys():
    assert llm_clients.required_secret_keys_for_provider("deepseek", ["deepseek-v4-pro"]) == ("deepseek_api_key",)


def test_deepseek_model_support():
    assert llm_clients.is_model_supported_by_provider("deepseek", "deepseek-v4-pro") is True
    assert llm_clients.is_model_supported_by_provider("deepseek", "any-model") is True


def test_deepseek_fallback():
    # DeepSeek has no fallback chain (like Anthropic)
    assert llm_clients.fallback_models_for_provider("deepseek") == ()
    assert llm_clients.fallback_candidates_for_model("deepseek", "deepseek-v4-pro") == ("deepseek-v4-pro",)


def test_deepseek_normalize_model_name():
    assert llm_clients.normalize_model_name("deepseek", "  deepseek-v4-pro  ") == "deepseek-v4-pro"


def test_deepseek_create_client(monkeypatch):
    created_clients = []

    def mock_openai(api_key, base_url=None, **kwargs):
        created_clients.append({"api_key": api_key, "base_url": base_url})
        return f"client-{api_key}"

    monkeypatch.setattr(llm_clients, "OpenAI", mock_openai)

    # With base URL
    client = llm_clients.create_llm_client(
        "deepseek",
        {"deepseek_api_key": "sk-ds-test", "deepseek_base_url": "https://custom.deepseek.example/v1"},
    )
    assert client == "client-sk-ds-test"
    assert created_clients[-1]["api_key"] == "sk-ds-test"
    assert created_clients[-1]["base_url"] == "https://custom.deepseek.example/v1"

    # Without base URL - should use default
    client = llm_clients.create_llm_client("deepseek", {"deepseek_api_key": "sk-ds-test2"})
    assert client == "client-sk-ds-test2"
    assert created_clients[-1]["base_url"] == llm_clients.DEEPSEEK_DEFAULT_BASE_URL


def test_fetch_available_models_deepseek_queries_v1_models(monkeypatch):
    calls: list[str | None] = []
    monkeypatch.setattr(
        llm_clients,
        "OpenAI",
        _fake_openai_models_client(
            {llm_clients.DEEPSEEK_DEFAULT_BASE_URL: ["deepseek-v4-flash"]},
            calls,
        ),
    )

    ids, error = llm_clients.fetch_available_models("deepseek", {"deepseek_api_key": "k"})

    assert error is None
    assert ids == ["deepseek-v4-flash"]
    assert calls == [llm_clients.DEEPSEEK_DEFAULT_BASE_URL]


# ── Live model discovery (/v1/models) tests ──────────────────────────────────


def test_fetch_available_models_openrouter_queries_v1_models(monkeypatch):
    calls: list[str | None] = []
    monkeypatch.setattr(
        llm_clients,
        "OpenAI",
        _fake_openai_models_client(
            {llm_clients.OPENROUTER_DEFAULT_BASE_URL: ["b-model", "a-model"]},
            calls,
        ),
    )

    ids, error = llm_clients.fetch_available_models("openrouter", {"openrouter_api_key": "k"})

    assert error is None
    assert ids == ["a-model", "b-model"]
    assert calls == [llm_clients.OPENROUTER_DEFAULT_BASE_URL]

    # Second call is served from the short-lived cache (no new client).
    monkeypatch.setattr(llm_clients, "OpenAI", lambda *a, **k: pytest.fail("cache miss"))
    cached_ids, cached_error = llm_clients.fetch_available_models("openrouter", {"openrouter_api_key": "k"})
    assert cached_error is None
    assert cached_ids == ["a-model", "b-model"]


def test_fetch_available_models_openai_without_base_url(monkeypatch):
    calls: list[str | None] = []
    monkeypatch.setattr(
        llm_clients,
        "OpenAI",
        _fake_openai_models_client({None: ["gpt-x"]}, calls),
    )

    ids, error = llm_clients.fetch_available_models("openai", {"openai_api_key": "key"})

    assert error is None
    assert ids == ["gpt-x"]
    assert calls == [None]


def test_fetch_available_models_freeflow_merges_backends(monkeypatch):
    urls = [
        llm_clients._GEMINI_OPENAI_BASE_URL,
        llm_clients._GROQ_OPENAI_BASE_URL,
    ]
    monkeypatch.setattr(
        llm_clients,
        "OpenAI",
        _fake_openai_models_client({urls[0]: ["gemini-x"], urls[1]: ["llama-y"]}),
    )

    ids, error = llm_clients.fetch_available_models(
        "freeflow",
        {"gemini_api_key": "g", "groq_api_key": "q"},
    )

    assert error is None
    assert set(ids) == {"gemini-x", "llama-y"}


def test_fetch_available_models_reports_total_failure(monkeypatch):
    def failing_client(api_key, base_url=None, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm_clients, "OpenAI", failing_client)

    ids, error = llm_clients.fetch_available_models("openai", {"openai_api_key": "key"})

    assert ids == []
    assert error is not None
    assert "boom" in error
    assert "OpenAI" in error

    # Failures are not cached — a retry hits the endpoint again.
    monkeypatch.setattr(llm_clients, "OpenAI", _fake_openai_models_client({None: ["gpt-x"]}))
    retry_ids, retry_error = llm_clients.fetch_available_models("openai", {"openai_api_key": "key"})
    assert retry_error is None
    assert retry_ids == ["gpt-x"]


def test_fetch_available_models_skips_endpoints_without_keys():
    ids, error = llm_clients.fetch_available_models("freeflow", {})
    assert ids == []
    assert error is None


def test_fetch_available_models_custom_anthropic_uses_sdk(monkeypatch):
    captured: dict[str, str] = {}

    class FakeAnthropic:
        def __init__(self, api_key=None, base_url=None):
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            self.models = SimpleNamespace(
                list=lambda limit=20: SimpleNamespace(data=[SimpleNamespace(id="claude-live")])
            )

    fake_module = types.SimpleNamespace(Anthropic=FakeAnthropic)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    monkeypatch.setattr(
        "user_config.get_custom_provider_by_name",
        lambda name: {
            "protocol": "anthropic",
            "base_url": "https://gw.example.com",
            "api_key": "secret-key",
        },
    )

    ids, error = llm_clients.fetch_available_models("custom:my-gw", {})

    assert error is None
    assert ids == ["claude-live"]
    assert captured["base_url"] == "https://gw.example.com"


def test_resolve_accessible_models_falls_back_within_catalog():
    models, warnings = llm_clients.resolve_accessible_models(
        "openai",
        {"fast": "gone-model", "smart": "kept-model"},
        ["kept-model", "gpt-5-mini"],
        language_hint="en",
    )

    assert models["smart"] == "kept-model"
    assert models["fast"] == "gpt-5-mini"
    assert len(warnings) == 1
    assert "gone-model" in warnings[0]


def test_resolve_accessible_models_keeps_unknown_when_no_candidate_matches():
    models, warnings = llm_clients.resolve_accessible_models(
        "openrouter",
        {"fast": "vendor/custom-model"},
        ["other/model"],
    )

    assert models == {"fast": "vendor/custom-model"}
    assert len(warnings) == 1


def test_resolve_accessible_models_empty_catalog_is_noop():
    requested = {"fast": "x", "smart": "y"}
    models, warnings = llm_clients.resolve_accessible_models("openai", requested, [])

    assert models == requested
    assert warnings == []


def test_get_model_options_from_live_catalog_overrides_static():
    options = llm_clients.get_model_options("openai", ["zeta", "alpha"])
    assert [(item["value"], item["label"]) for item in options] == [("alpha", "alpha"), ("zeta", "zeta")]

    # Empty live catalog falls back to the static curated hints.
    fallback = llm_clients.get_model_options("openai", [])
    assert fallback == llm_clients.get_model_options("openai")
