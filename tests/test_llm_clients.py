from __future__ import annotations

from types import SimpleNamespace

import llm_clients


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
    monkeypatch.setattr(llm_clients, "OpenAI", lambda api_key: ("client", api_key))
    assert llm_clients.create_llm_client("openai", {"openai_api_key": "key"}) == ("client", "key")
