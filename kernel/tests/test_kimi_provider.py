# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel.config.brand_icons import model_icon, provider_icon
from kernel.config.config_info import build_config_info
from kernel.context.context_budget import input_limit_for
from kernel.llm import llm_clients as llm
from kernel.llm import pricing
from kernel.llm.reasoning_effort import effort_advisory, reasoning_request_kwargs


def test_kimi_code_is_a_subscription_provider_with_regional_endpoints():
    assert llm.normalize_provider("Kimi Code") == "kimi"
    assert llm.normalize_provider("kimi-code") == "kimi"
    assert llm.provider_label("kimi") == "Kimi Code"
    assert llm.required_secret_keys_for_provider("kimi", []) == ("kimi_api_key",)
    assert llm.REGIONAL_BASE_URL_CANDIDATES["kimi"] == (
        "https://api.kimi.ai/coding/v1",
        "https://api.kimi.com/coding/v1",
    )
    assert llm.provider_supports_streaming("kimi")
    assert pricing.lookup_price("kimi", "k3.1-pro") is None
    assert pricing.call_cost("kimi", "k3.1-pro", 1000, 50, None, None, False) == (None, False)
    assert provider_icon("kimi") == "moonshot"


def test_kimi_code_has_documented_defaults_and_model_options(monkeypatch):
    monkeypatch.delenv("AGENT_FAST_MODEL", raising=False)
    monkeypatch.delenv("AGENT_SMART_MODEL", raising=False)
    models, warnings = llm.resolve_models_for_provider("kimi", {})
    assert models == {"fast": "kimi-for-coding", "smart": "k3.1-pro"}
    assert warnings == []
    assert [option["value"] for option in llm.get_model_options("kimi")] == [
        "k3.1-pro",
        "k3.1-pro-highspeed",
        "k3",
        "k3-256k",
        "kimi-for-coding",
        "kimi-for-coding-highspeed",
    ]
    assert llm.fallback_models_for_provider("kimi") == ()


def test_kimi_loads_live_models_beyond_the_static_catalog(monkeypatch):
    monkeypatch.setattr(llm, "_MODEL_DISCOVERY_CACHE", {})
    calls = []

    def fake_list_models(api_key, base_url):
        calls.append((api_key, base_url))
        return ["k3.1-pro", "k4-future-model"]

    monkeypatch.setattr(llm, "_list_models_via_openai", fake_list_models)
    available, error = llm.fetch_available_models("kimi", {"kimi_api_key": "[REDACTED]"})

    assert error is None
    assert available == ["k3.1-pro", "k4-future-model"]
    assert calls == [("[REDACTED]", "https://api.kimi.ai/coding/v1")]
    assert [option["value"] for option in llm.get_model_options("kimi", available)] == available
    # An unreachable or empty catalog keeps the documented models as a fallback.
    assert llm.get_model_options("kimi", []) == llm.get_model_options("kimi")


def test_kimi_accepts_a_saved_model_missing_from_the_static_catalog(monkeypatch):
    monkeypatch.delenv("AGENT_FAST_MODEL", raising=False)
    monkeypatch.delenv("AGENT_SMART_MODEL", raising=False)
    models, warnings = llm.resolve_models_for_provider(
        "kimi", {"fast": "k4-future-model", "smart": "k4-future-pro"}
    )
    assert models == {"fast": "k4-future-model", "smart": "k4-future-pro"}
    assert warnings == []


def test_kimi_code_client_uses_membership_endpoint_and_key(monkeypatch):
    created = []

    def fake_openai(**kwargs):
        created.append(kwargs)
        return "client"

    monkeypatch.setattr(llm, "OpenAI", fake_openai)
    assert llm.create_llm_client("kimi", {"kimi_api_key": "test-key"}) == "client"
    assert created[-1]["api_key"] == "test-key"
    assert created[-1]["base_url"] == "https://api.kimi.ai/coding/v1"

    credentials = {
        "kimi_api_key": "test-key",
        "kimi_base_url": "https://api.kimi.com/coding/v1",
    }
    assert llm.create_llm_client("kimi", credentials) == "client"
    assert created[-1]["base_url"] == "https://api.kimi.com/coding/v1"
    assert llm._discovery_endpoints("kimi", credentials) == [{
        "kind": "openai",
        "base_url": "https://api.kimi.com/coding/v1",
        "api_key": "test-key",
        "label": "Kimi Code",
    }]


def test_kimi_reasoning_never_disables_thinking():
    assert reasoning_request_kwargs("kimi", "false") == {"reasoning_effort": "low"}
    assert reasoning_request_kwargs("kimi", "low") == {"reasoning_effort": "low"}
    assert reasoning_request_kwargs("kimi", "medium") == {"reasoning_effort": "high"}
    assert reasoning_request_kwargs("kimi", "high") == {"reasoning_effort": "high"}
    assert reasoning_request_kwargs("kimi", "xhigh") == {"reasoning_effort": "max"}
    assert reasoning_request_kwargs("kimi", "max") == {"reasoning_effort": "max"}
    assert "'low'" in effort_advisory("kimi", "false")
    assert effort_advisory("kimi", "high") is None


def test_kimi_context_limits_distinguish_256k_models():
    assert input_limit_for("k3.1-pro") == 1_048_576
    assert input_limit_for("k3.1-pro-highspeed") == 1_048_576
    assert input_limit_for("k3") == 1_048_576
    assert input_limit_for("k3-256k") == 262_144
    assert input_limit_for("kimi-for-coding") == 1_048_576
    assert input_limit_for("kimi-for-coding-highspeed") == 262_144


def test_kimi_config_info_exposes_models_and_moonshot_brand(monkeypatch):
    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_FAST_MODEL", raising=False)
    monkeypatch.delenv("AGENT_SMART_MODEL", raising=False)
    entry = next(provider for provider in build_config_info()["providers"] if provider["id"] == "kimi")
    assert entry["label"] == "Kimi Code"
    assert entry["icon"] == "moonshot"
    assert entry["defaults"] == {"fast": "kimi-for-coding", "smart": "k3.1-pro"}
    assert entry["requires_manual_models"] is False
    assert len(entry["options"]) == 6
    assert {option["icon"] for option in entry["options"]} == {"moonshot"}
    assert model_icon("k3-256k") == "moonshot"
    assert model_icon("kimi-for-coding") == "moonshot"


def test_kimi_model_picker_selects_a_new_live_model(monkeypatch):
    from kernel import main

    monkeypatch.setattr(llm, "_MODEL_DISCOVERY_CACHE", {})
    monkeypatch.setattr(main, "dynamic_model_loading_enabled", lambda: True)
    monkeypatch.setattr(main, "load_available_credentials", lambda keys: {"kimi_api_key": "[REDACTED]"})
    monkeypatch.setattr(llm, "_list_models_via_openai", lambda key, url: ["k4-future-model"])

    class FakeUI:
        def choose_option(self, **kwargs):
            offered = [item["value"] for item in kwargs["options"]]
            if kwargs["title"] == "Select action:":
                assert "pick" in offered
                return "pick"
            assert offered == ["k4-future-model"]
            return "k4-future-model"

    selected = main.select_models_interactively(
        main.AgentConfig(provider="kimi", fast_model="kimi-for-coding", smart_model="k3.1-pro"),
        verbose=False,
        ui=FakeUI(),
    )
    assert selected == {"fast": "k4-future-model", "smart": "k4-future-model"}
