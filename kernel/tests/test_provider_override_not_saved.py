# -*- coding: utf-8 -*-
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from kernel import main
from kernel.config import user_config
from kernel.protocol import event_stdio
from kernel.providers import chatgpt_auth
from kernel.sandbox.executor import SafeExecutor

SAVED_PROVIDER = "anthropic"


@pytest.fixture
def stubbed_model_side(monkeypatch):
    """Keep build_agent off the network: no model catalog, no real client."""
    monkeypatch.setattr(main, "fetch_available_models", lambda *args, **kwargs: ([], None))
    monkeypatch.setattr(main, "create_llm_client", lambda *args, **kwargs: SimpleNamespace(chat=None))
    monkeypatch.setattr(main, "network_isolation_available", lambda: True)


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the user config at tmp_path; the real ~/.kvist is never read."""
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "kvist"))
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    user_config.save_user_config(
        {user_config.LLM_PROVIDER_KEY: SAVED_PROVIDER, "deepseek_api_key": "fixture-key"}
    )
    return tmp_path / "kvist"


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "config.json"
    user_config.save_user_config(
        {user_config.LLM_PROVIDER_KEY: SAVED_PROVIDER, "deepseek_api_key": "fixture-key"},
        path,
    )
    return path


@pytest.fixture
def chatgpt_signed_in(monkeypatch):
    monkeypatch.setattr(chatgpt_auth, "has_credentials", lambda config_path=None: True)
    monkeypatch.setattr(
        chatgpt_auth,
        "login",
        lambda *args, **kwargs: pytest.fail("login must not run for a signed-in account"),
    )


def test_checking_chatgpt_does_not_replace_saved_provider(config_path, chatgpt_signed_in):
    assert user_config.ensure_provider_credentials("chatgpt", "gpt-6.1-sol", config_path=config_path) == {}

    assert user_config.get_saved_provider(config_path) == SAVED_PROVIDER


def test_checking_saved_provider_does_not_replace_saved_provider(config_path):
    credentials = user_config.ensure_provider_credentials(
        "deepseek",
        "model",
        required_keys=("deepseek_api_key",),
        config_path=config_path,
        prompt_secret_fn=lambda label: pytest.fail(f"no prompt expected for {label}"),
    )

    assert credentials == {"deepseek_api_key": "fixture-key"}
    assert user_config.get_saved_provider(config_path) == SAVED_PROVIDER


def test_explicit_pick_of_chatgpt_saves_it_as_main_provider(config_path, chatgpt_signed_in):
    user_config.ensure_provider_credentials(
        "chatgpt", "gpt-6.1-sol", config_path=config_path, save_selection=True
    )

    assert user_config.get_saved_provider(config_path) == "chatgpt"


def test_explicit_pick_of_key_provider_saves_it_as_main_provider(config_path):
    user_config.ensure_provider_credentials(
        "deepseek",
        "model",
        required_keys=("deepseek_api_key",),
        config_path=config_path,
        save_selection=True,
    )

    assert user_config.get_saved_provider(config_path) == "deepseek"
    assert user_config.load_user_config(config_path)["deepseek_api_key"] == "fixture-key"


def test_stdio_launch_with_chatgpt_override_keeps_saved_provider(
    isolated_config, stubbed_model_side, monkeypatch
):
    monkeypatch.setattr(chatgpt_auth, "has_credentials", lambda config_path=None: True)
    monkeypatch.setattr(
        chatgpt_auth, "login", lambda *args, **kwargs: pytest.fail("login must not run")
    )
    event_stdio.launch_multi(
        str(isolated_config.parent),
        main.build_agent,
        provider_override="chatgpt",
        in_stream=io.StringIO(""),
        out_stream=io.StringIO(),
    )

    assert user_config.get_saved_provider() == SAVED_PROVIDER


def test_rebuilds_of_the_session_follow_the_pick_not_the_override(
    isolated_config, stubbed_model_side, monkeypatch
):
    """A session started with --provider chatgpt: /models-style rebuilds must not
    save chatgpt, but a real pick (persist_selection=True, as /provider sends) must."""
    rebuilds = []
    monkeypatch.setattr(chatgpt_auth, "has_credentials", lambda config_path=None: True)
    monkeypatch.setattr(
        event_stdio,
        "serve_session_multi",
        lambda session, **kwargs: rebuilds.append(kwargs["rebuild"]),
    )
    event_stdio.launch_multi(
        str(isolated_config.parent),
        main.build_agent,
        provider_override="chatgpt",
        in_stream=io.StringIO(""),
        out_stream=io.StringIO(),
    )
    rebuild = rebuilds[0]

    rebuild(provider_override="chatgpt", models_override={"fast": "m", "smart": "m"})
    assert user_config.get_saved_provider() == SAVED_PROVIDER

    replacement, _config, _warnings = rebuild(provider_override="deepseek", persist_selection=True)
    assert replacement.config.provider == "deepseek"
    assert user_config.get_saved_provider() == "deepseek"


def test_env_launch_provider_is_used_but_not_saved(isolated_config, stubbed_model_side, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "deepseek")

    _agent, config, _warnings = main.build_agent(
        SafeExecutor(tmp_path), max_steps=5, persist_logs=False, verbose=False
    )

    assert config.provider == "deepseek"
    assert user_config.get_saved_provider() == SAVED_PROVIDER


def test_build_agent_with_an_explicit_override_saves_only_when_asked(
    isolated_config, stubbed_model_side, tmp_path
):
    main.build_agent(
        SafeExecutor(tmp_path),
        max_steps=5,
        persist_logs=False,
        verbose=False,
        provider_override="deepseek",
        persist_selection=False,
    )
    assert user_config.get_saved_provider() == SAVED_PROVIDER

    main.build_agent(
        SafeExecutor(tmp_path), max_steps=5, persist_logs=False, verbose=False, provider_override="deepseek"
    )
    assert user_config.get_saved_provider() == "deepseek"
