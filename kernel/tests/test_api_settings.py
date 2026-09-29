from __future__ import annotations

import json

import pytest

from kernel import agent_model
from kernel import api_settings
from kernel.agent_state import AgentConfig


def _config(**overrides) -> AgentConfig:
    return AgentConfig(**overrides)


def test_defaults_match_agent_config_defaults():
    """The dialog must open on what an unconfigured Kvist actually does."""
    config = _config()
    values = api_settings.default_api_settings()

    assert values["request_interval"] == config.api_request_interval
    assert values["request_timeout"] == config.api_timeout
    assert values["max_retries"] == config.max_retries
    assert values["retry_base_delay"] == config.retry_base_delay
    assert values["context_tokens"] == config.max_context_tokens
    assert values["response_cache"] is config.cache_enabled
    assert values["model_fallbacks"] is config.runtime_model_fallbacks_enabled


def test_only_changed_keys_are_persisted(tmp_path):
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values["context_tokens"] = 1_000_000

    api_settings.store_api_settings(values, path)

    assert json.loads(path.read_text(encoding="utf-8"))["api_settings"] == {
        "context_tokens": 1_000_000
    }
    assert api_settings.load_api_settings(path)["context_tokens"] == 1_000_000
    assert api_settings.load_api_settings(path)["request_interval"] == 5.0


def test_apply_writes_every_knob_onto_the_config(tmp_path):
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values.update(
        request_interval=0.0,
        request_timeout=300.0,
        max_retries=5,
        retry_base_delay=0.0,
        context_tokens=1_000_000,
        response_cache=False,
        model_fallbacks=True,
    )
    api_settings.store_api_settings(values, path)

    config = _config()
    api_settings.apply_api_settings(config, path)

    assert config.api_request_interval == 0.0
    assert config.api_timeout == 300.0
    assert config.max_retries == 5
    assert config.retry_base_delay == 0.0
    assert config.max_context_tokens == 1_000_000
    assert config.cache_enabled is False
    assert config.runtime_model_fallbacks_enabled is True


def test_untouched_interval_is_not_marked_as_the_users_choice(tmp_path):
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values["max_retries"] = 4
    api_settings.store_api_settings(values, path)

    config = _config()
    api_settings.apply_api_settings(config, path)

    assert config.api_interval_user_set is False


def test_chosen_interval_survives_the_fast_direct_provider_zeroing(
    tmp_path, monkeypatch
):
    """A number the user typed must not be silently ignored on OpenAI."""
    monkeypatch.delenv("AGENT_API_INTERVAL", raising=False)
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values["request_interval"] = 12.5
    api_settings.store_api_settings(values, path)

    config = _config(provider="openai")
    assert agent_model._get_api_interval(_Agent(config)) == 0.0

    api_settings.apply_api_settings(config, path)

    assert config.api_interval_user_set is True
    assert agent_model._get_api_interval(_Agent(config)) == 12.5


def test_removed_pacing_stays_removed_for_every_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_API_INTERVAL", raising=False)
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values["request_interval"] = 0.0
    api_settings.store_api_settings(values, path)

    config = _config(provider="openrouter")
    api_settings.apply_api_settings(config, path)

    assert agent_model._get_api_interval(_Agent(config)) == 0.0


def test_env_var_still_outranks_a_saved_interval(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    values = api_settings.default_api_settings()
    values["request_interval"] = 30.0
    api_settings.store_api_settings(values, path)
    monkeypatch.setenv("AGENT_API_INTERVAL", "1.5")

    config = _config(provider="openai")
    api_settings.apply_api_settings(config, path)

    assert agent_model._get_api_interval(_Agent(config)) == 1.5


def test_dynamic_model_loading_round_trips(tmp_path):
    path = tmp_path / "config.json"
    assert api_settings.dynamic_model_loading_enabled(path) is True

    values = api_settings.default_api_settings()
    values["dynamic_models"] = False
    api_settings.store_api_settings(values, path)

    assert api_settings.dynamic_model_loading_enabled(path) is False


def test_hand_edited_garbage_falls_back_to_the_default(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"api_settings": {"max_retries": 99, "response_cache": "yes"}}),
        encoding="utf-8",
    )

    values = api_settings.load_api_settings(path)

    assert values["max_retries"] == 2
    assert values["response_cache"] is True


@pytest.mark.parametrize(
    ("key", "text", "expected"),
    [
        ("context_tokens", "1m", 1_000_000),
        ("context_tokens", "250k", 250_000),
        ("context_tokens", "1 000 000 tokens", 1_000_000),
        ("context_tokens", "auto", 0),
        ("context_tokens", "0", 0),
        ("request_interval", "off", 0.0),
        ("request_interval", "2,5", 2.5),
        ("request_interval", "10 s", 10.0),
        ("max_retries", "4 attempts", 4),
    ],
)
def test_parse_number_accepts_what_the_menu_shows(key, text, expected):
    assert api_settings.parse_number(api_settings.setting_for(key), text) == expected


def test_menu_rendering_round_trips_through_the_parser():
    """Every displayed value must be re-readable, since it pre-fills the field."""
    for setting in api_settings.API_SETTINGS:
        if setting.kind == "bool":
            continue
        shown = api_settings.format_value(setting, setting.default)
        assert api_settings.parse_number(setting, shown) == setting.default


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("context_tokens", "500"),
        ("context_tokens", "5m"),
        ("max_retries", "0"),
        ("max_retries", "11"),
        ("request_timeout", "1"),
        ("request_interval", "-3"),
        ("request_interval", "soon"),
        ("request_interval", ""),
    ],
)
def test_parse_number_rejects_out_of_range_and_nonsense(key, text):
    with pytest.raises(ValueError):
        api_settings.parse_number(api_settings.setting_for(key), text)


def test_zero_is_only_accepted_where_it_is_a_mode():
    interval = api_settings.setting_for("request_interval")
    timeout = api_settings.setting_for("request_timeout")

    assert api_settings.parse_number(interval, "0") == 0.0
    with pytest.raises(ValueError):
        api_settings.parse_number(timeout, "0")


def test_format_value_labels_the_modes_and_toggles():
    assert api_settings.format_value(
        api_settings.setting_for("context_tokens"), 0
    ) == "auto"
    assert api_settings.format_value(
        api_settings.setting_for("request_interval"), 0.0
    ) == "no pacing"
    assert api_settings.format_value(
        api_settings.setting_for("dynamic_models"), True
    ) == "on"


def test_active_env_override_is_reported_only_when_set(monkeypatch):
    setting = api_settings.setting_for("context_tokens")
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    assert api_settings.active_env_override(setting) == ""

    monkeypatch.setenv("AGENT_MAX_CONTEXT_TOKENS", "300000")
    assert api_settings.active_env_override(setting) == "AGENT_MAX_CONTEXT_TOKENS"
    assert api_settings.active_env_override(
        api_settings.setting_for("max_retries")
    ) == ""


class _Agent:
    """The single attribute ``agent_model._get_api_interval`` reads."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config


def test_context_window_override_reaches_the_prompt_budget(
    tmp_path, agent_factory, monkeypatch
):
    """Raising the window in the dialog must change the budget the trimmer uses."""
    monkeypatch.delenv("AGENT_MAX_CONTEXT_TOKENS", raising=False)
    path = tmp_path / "config.json"
    agent = agent_factory(fast_model="claude-sonnet-4-5")
    derived = agent_model.context_token_limit(agent)

    values = api_settings.default_api_settings()
    values["context_tokens"] = 1_000_000
    api_settings.store_api_settings(values, path)
    api_settings.apply_api_settings(agent.config, path)

    assert derived < 1_000_000
    assert agent_model.context_token_limit(agent) == 1_000_000
