# -*- coding: utf-8 -*-
"""The tunable API transport knobs behind ``/settings-api``.

These are the parameters a user must be able to change without editing code:
how fast Kvist may hit the provider, how long it waits for one call, how hard
it retries, how large a prompt it may build, and whether it trusts the
provider's live model catalog. Each one is a single global preference stored in
``~/.kvist/config.json`` under ``api_settings`` and stamped onto ``AgentConfig``.

Only the keys the user actually changed are stored, and that is also how an
explicitly chosen request interval is told apart from the shipped default: the
pacing table in ``agent_model`` zeroes the interval for the direct vendor APIs,
so a user who typed a number has to outrank that table instead of being
silently ignored.

The two env vars that cover the same ground (``AGENT_API_INTERVAL``,
``AGENT_MAX_CONTEXT_TOKENS``) are read at call time and keep winning over these
values; the dialog says so rather than pretending its number is in effect.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from kernel.agent_state import AgentConfig
from kernel.user_config import get_saved_api_settings, save_api_settings

Number = int | float
Value = bool | int | float


@dataclass(frozen=True, slots=True)
class ApiSetting:
    key: str
    label: str
    description: str
    kind: str  # "bool" | "int" | "float"
    default: Value
    minimum: float = 0.0
    maximum: float = 0.0
    unit: str = ""
    # What 0 means for the knobs where it is a mode and not a quantity. Such a
    # setting accepts 0 even when ``minimum`` is higher.
    zero_label: str = ""
    # The env var that overrides this knob at call time, if there is one.
    env_var: str = ""


API_SETTINGS: tuple[ApiSetting, ...] = (
    ApiSetting(
        key="request_interval",
        label="Request interval",
        description=(
            "Minimum gap between two API request starts. 0 removes the pacing "
            "entirely; a number applies to every provider, including the "
            "direct vendor APIs that are unpaced by default."
        ),
        kind="float",
        default=5.0,
        minimum=0.1,
        maximum=300.0,
        unit="s",
        zero_label="no pacing",
        env_var="AGENT_API_INTERVAL",
    ),
    ApiSetting(
        key="request_timeout",
        label="Request timeout",
        description="How long one model call may take before it is abandoned.",
        kind="float",
        default=60.0,
        minimum=5.0,
        maximum=1800.0,
        unit="s",
    ),
    ApiSetting(
        key="max_retries",
        label="Retry attempts",
        description="How many times one failed call is tried before the step fails.",
        kind="int",
        default=2,
        minimum=1,
        maximum=10,
        unit="attempts",
    ),
    ApiSetting(
        key="retry_base_delay",
        label="Retry backoff",
        description="The first retry waits this long; every further retry doubles it.",
        kind="float",
        default=2.0,
        minimum=0.0,
        maximum=120.0,
        unit="s",
    ),
    ApiSetting(
        key="context_tokens",
        label="Context window",
        description=(
            "Prompt budget for one call. 0 derives it from the active model's "
            "documented input limit; a number overrides that both ways."
        ),
        kind="int",
        default=0,
        minimum=8_000,
        maximum=2_000_000,
        unit="tokens",
        zero_label="auto",
        env_var="AGENT_MAX_CONTEXT_TOKENS",
    ),
    ApiSetting(
        key="dynamic_models",
        label="Dynamic model loading",
        description=(
            "Discover the model catalog from the provider's /v1/models on every "
            "launch. Off keeps the built-in catalog and never calls the endpoint."
        ),
        kind="bool",
        default=True,
    ),
    ApiSetting(
        key="response_cache",
        label="Response cache",
        description="Reuse a model reply when an identical prompt repeats in a run.",
        kind="bool",
        default=True,
    ),
    ApiSetting(
        key="model_fallbacks",
        label="Model fallbacks",
        description=(
            "Switch to another model of the same provider when the selected one "
            "fails at runtime."
        ),
        kind="bool",
        default=False,
    ),
)

_BY_KEY: dict[str, ApiSetting] = {setting.key: setting for setting in API_SETTINGS}

_SUFFIX_MULTIPLIERS = {"k": 1_000, "m": 1_000_000}
_ZERO_WORDS = frozenset({"auto", "off", "none", "no", "default", "-"})


def setting_for(key: str) -> ApiSetting:
    return _BY_KEY[key]


def default_api_settings() -> dict[str, Value]:
    return {setting.key: setting.default for setting in API_SETTINGS}


def load_api_settings(config_path: Path | None = None) -> dict[str, Value]:
    """The effective settings: shipped defaults overlaid with valid saved values."""
    saved = get_saved_api_settings(config_path)
    values = default_api_settings()
    for setting in API_SETTINGS:
        if setting.key not in saved:
            continue
        try:
            values[setting.key] = coerce_value(setting, saved[setting.key])
        except ValueError:
            # A hand-edited config must not stop Kvist from starting.
            continue
    return values


def store_api_settings(
    values: dict[str, Value],
    config_path: Path | None = None,
) -> None:
    """Persist *values*, keeping only what differs from the shipped default.

    A key's absence is meaningful downstream — it is what ``apply_api_settings``
    reads as "the user never chose this" — so a value equal to the default is
    removed rather than written.
    """
    changed = {
        setting.key: values[setting.key]
        for setting in API_SETTINGS
        if setting.key in values and values[setting.key] != setting.default
    }
    save_api_settings(changed, config_path)


def apply_api_settings(
    config: AgentConfig,
    config_path: Path | None = None,
) -> dict[str, Value]:
    """Stamp the persisted settings onto *config*. Returns what was applied."""
    saved = get_saved_api_settings(config_path)
    values = load_api_settings(config_path)

    config.api_request_interval = float(values["request_interval"])
    config.api_interval_user_set = "request_interval" in saved
    config.api_timeout = float(values["request_timeout"])
    config.max_retries = int(values["max_retries"])
    config.retry_base_delay = float(values["retry_base_delay"])
    config.max_context_tokens = int(values["context_tokens"])
    config.cache_enabled = bool(values["response_cache"])
    config.runtime_model_fallbacks_enabled = bool(values["model_fallbacks"])
    return values


def dynamic_model_loading_enabled(config_path: Path | None = None) -> bool:
    """Whether launch-time /v1/models discovery is allowed.

    Read straight from the preference rather than from ``AgentConfig``: the
    model pickers need it before an agent exists.
    """
    return bool(load_api_settings(config_path)["dynamic_models"])


def active_env_override(setting: ApiSetting) -> str:
    """The env var currently shadowing *setting*, or an empty string."""
    if not setting.env_var:
        return ""
    raw = os.getenv(setting.env_var)
    if raw is None or not raw.strip():
        return ""
    return setting.env_var


def format_value(setting: ApiSetting, value: Value) -> str:
    """One-line rendering of *value* for the settings menu."""
    if setting.kind == "bool":
        return "on" if value else "off"
    if setting.zero_label and not value:
        return setting.zero_label
    return f"{_format_number(setting, value)} {setting.unit}".strip()


def _format_number(setting: ApiSetting, value: Value) -> str:
    if setting.kind == "int":
        return f"{int(value):,}".replace(",", " ")
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return str(rounded)


def parse_number(setting: ApiSetting, text: str) -> Number:
    """Read a typed value for a numeric setting, or raise ``ValueError``.

    Accepts the words behind ``zero_label`` ("auto", "off"), a ``k``/``m``
    suffix so a million-token window is "1m", and the setting's own unit as a
    trailing word so echoing what the menu shows back is not an error.
    """
    cleaned = text.strip().casefold().replace(",", ".").replace(" ", "")
    if not cleaned:
        raise ValueError("Enter a value.")
    if setting.zero_label and (cleaned in _ZERO_WORDS or cleaned == setting.zero_label):
        return 0 if setting.kind == "int" else 0.0

    if setting.unit and cleaned.endswith(setting.unit) and cleaned != setting.unit:
        cleaned = cleaned[: -len(setting.unit)]
    multiplier = _SUFFIX_MULTIPLIERS.get(cleaned[-1:], 1)
    if multiplier != 1:
        cleaned = cleaned[:-1]

    try:
        number = float(cleaned) * multiplier
    except ValueError as error:
        raise ValueError(f"'{text.strip()}' is not a number.") from error
    return _coerce_number(setting, number)


def coerce_value(setting: ApiSetting, value: object) -> Value:
    """Validate a stored JSON value for *setting*, or raise ``ValueError``."""
    if setting.kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{setting.label} must be true or false.")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{setting.label} must be a number.")
    return _coerce_number(setting, float(value))


def _coerce_number(setting: ApiSetting, number: float) -> Number:
    if setting.zero_label and number == 0:
        return 0 if setting.kind == "int" else 0.0
    if number < setting.minimum or number > setting.maximum:
        raise ValueError(range_hint(setting))
    if setting.kind == "int":
        return int(round(number))
    return round(number, 2)


def range_hint(setting: ApiSetting) -> str:
    """The accepted range of a numeric setting, as one sentence."""
    low = _format_number(setting, setting.minimum)
    high = _format_number(setting, setting.maximum)
    unit = f" {setting.unit}" if setting.unit else ""
    message = f"{setting.label} must be between {low} and {high}{unit}"
    if setting.zero_label:
        return f"{message}, or 0 for {setting.zero_label}."
    return f"{message}."
