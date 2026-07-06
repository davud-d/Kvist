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
