from __future__ import annotations

from types import SimpleNamespace

import pytest

import main
from executor import SafeExecutor


def test_resolve_workspace_path_creates_directory_and_rejects_file(tmp_path):
    workspace = main.resolve_workspace_path(str(tmp_path / "new"))
    assert workspace.is_dir()

    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="directory"):
        main.resolve_workspace_path(str(file_path))


def test_build_agent_wires_config_without_real_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "get_saved_provider_or_none", lambda: "openai")
    monkeypatch.setattr(main, "get_saved_models", lambda provider: {})
    monkeypatch.setattr(main, "ensure_provider_credentials", lambda **kwargs: {"openai_api_key": "key"})
    monkeypatch.setattr(main, "resolve_openai_accessible_models", lambda key, models, language_hint=None: (models, ["warn"]))
    monkeypatch.setattr(main, "create_llm_client", lambda provider, credentials, verbose=False: SimpleNamespace(chat=None))

    agent, config, warnings = main.build_agent(
        executor=SafeExecutor(tmp_path),
        max_steps=7,
        persist_logs=False,
        verbose=True,
        language_hint="en",
    )

    assert agent.config.max_steps == 7
    assert config.provider == "openai"
    assert warnings == ["warn"]


def test_choose_provider_delegates_to_ui(monkeypatch):
    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return kwargs["current_value"]

    monkeypatch.setattr(main, "AgentUI", FakeUI)
    assert main.choose_provider("openai", False) == "openai"


def test_choose_session_creates_normalized_named_session(monkeypatch):
    created = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return "__new__"

        def prompt_text(self, **kwargs):
            return "Feature_One"

    agent = SimpleNamespace(
        current_session="default",
        _list_sessions=lambda: [],
        new_session=created.append,
    )
    monkeypatch.setattr(main, "AgentUI", FakeUI)

    assert main.choose_session(agent, False) == "feature_one"
    assert created == ["feature_one"]


def test_choose_session_rejects_path_like_name(monkeypatch):
    created = []

    class FakeUI:
        def __init__(self, **kwargs):
            pass

        def choose_option(self, **kwargs):
            return "__new__"

        def prompt_text(self, **kwargs):
            return "../outside"

    agent = SimpleNamespace(
        current_session="default",
        _list_sessions=lambda: [],
        new_session=created.append,
    )
    monkeypatch.setattr(main, "AgentUI", FakeUI)

    assert main.choose_session(agent, False) is None
    assert created == []


def test_configure_custom_provider_interactively_url_normalization(monkeypatch):
    saved_providers = {}
    monkeypatch.setattr(main, "get_custom_providers", lambda: {})
    monkeypatch.setattr(main, "save_custom_provider", saved_providers.__setitem__)

    class FakeUI:
        def __init__(self, **kwargs):
            self.option_selections = {}
            self.prompt_selections = {}

        def prompt_text(self, title, **kwargs):
            for pattern, val in self.prompt_selections.items():
                if pattern in title:
                    return val
            return "default_val"

        def prompt_secret(self, title, **kwargs):
            return ""

        def choose_option(self, title, **kwargs):
            for pattern, val in self.option_selections.items():
                if pattern in title:
                    return val
            return kwargs.get("current_value")

    ui = FakeUI()
    monkeypatch.setattr(main, "AgentUI", lambda **kwargs: ui)

    # 1. Test URL "https://api.example.dev" and "add"
    ui.prompt_selections = {
        "provider ID": "test-provider-1",
        "base URL": "https://api.example.dev",
        "display name": "Test Provider 1",
        "model ID": "gpt-4",
    }
    ui.option_selections = {
        "Select protocol": "openai",
        "base URL не содержит": "add",
        "streaming": "no",
        "Model configuration": "one",
        "Create this provider?": "yes",
    }
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-1"]["base_url"] == "https://api.example.dev/v1"

    # 2. Test URL "https://api.example.dev" and "keep"
    ui.prompt_selections["provider ID"] = "test-provider-2"
    ui.option_selections["base URL не содержит"] = "keep"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-2"]["base_url"] == "https://api.example.dev"

    # 3. Test URL "https://api.example.dev/" and "add"
    ui.prompt_selections["provider ID"] = "test-provider-3"
    ui.prompt_selections["base URL"] = "https://api.example.dev/"
    ui.option_selections["base URL не содержит"] = "add"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-3"]["base_url"] == "https://api.example.dev/v1"

    # 4. Test URL "https://api.example.dev/v1" (should not ask)
    ui.prompt_selections["provider ID"] = "test-provider-4"
    ui.prompt_selections["base URL"] = "https://api.example.dev/v1"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-4"]["base_url"] == "https://api.example.dev/v1"

    # 5. Test URL "https://api.example.dev/v1beta" (should not ask)
    ui.prompt_selections["provider ID"] = "test-provider-5"
    ui.prompt_selections["base URL"] = "https://api.example.dev/v1beta"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-5"]["base_url"] == "https://api.example.dev/v1beta"

    # 6. Test Anthropic protocol (should not ask or modify)
    ui.prompt_selections["provider ID"] = "test-provider-6"
    ui.prompt_selections["base URL"] = "https://api.example.dev"
    ui.option_selections["Select protocol"] = "anthropic"
    main.configure_custom_provider_interactively(ui, False)
    # Note: Anthropic should keep the original base URL as is
    assert saved_providers["test-provider-6"]["base_url"] == "https://api.example.dev"

    # 7. Test URL without scheme "api.example.dev" and "add_https" auto-add, and then "add" /v1
    ui.prompt_selections = {
        "provider ID": "test-provider-7",
        "base URL": "api.example.dev",
        "display name": "Test Provider 7",
        "model ID": "gpt-4",
    }
    ui.option_selections = {
        "base URL должен начинаться": "add_https",
        "Select protocol": "openai",
        "base URL не содержит": "add",
        "streaming": "no",
        "Model configuration": "one",
        "Create this provider?": "yes",
    }
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-7"]["base_url"] == "https://api.example.dev/v1"

    # 8. Test URL without scheme "api.example.dev" and "add_https" auto-add, and then "keep" as is
    ui.prompt_selections["provider ID"] = "test-provider-8"
    ui.option_selections["base URL не содержит"] = "keep"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-8"]["base_url"] == "https://api.example.dev"

    # 9. Test URL without scheme "api.example.dev/v1" and "add_https" auto-add
    ui.prompt_selections["provider ID"] = "test-provider-9"
    ui.prompt_selections["base URL"] = "api.example.dev/v1"
    main.configure_custom_provider_interactively(ui, False)
    assert saved_providers["test-provider-9"]["base_url"] == "https://api.example.dev/v1"


