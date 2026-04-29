# -*- coding: utf-8 -*-
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

from openai import AuthenticationError, OpenAI

USER_CONFIG_DIRNAME = ".devagent"
USER_CONFIG_FILENAME = "config.json"
USER_CONFIG_KEY = "openai_api_key"
LEGACY_USER_CONFIG_KEY = "OPENAI_API_KEY"
USER_CONFIG_KEYS = (USER_CONFIG_KEY, LEGACY_USER_CONFIG_KEY)


def get_user_config_path() -> Path:
    return Path.home() / USER_CONFIG_DIRNAME / USER_CONFIG_FILENAME


def load_api_key_or_bootstrap(validation_model: str) -> str:
    config_path = get_user_config_path()
    if config_path.exists():
        return load_api_key(config_path)

    api_key = prompt_api_key()
    if not api_key:
        raise RuntimeError("OpenAI API key is required.")

    validate_api_key(api_key, validation_model)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({USER_CONFIG_KEY: api_key}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return api_key


def load_api_key(config_path: Path | None = None) -> str:
    path = config_path or get_user_config_path()
    try:
        raw_data = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError("Failed to read user config.") from error

    if not raw_data:
        raise RuntimeError("User config is empty.")

    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as error:
        raise RuntimeError("User config is invalid.") from error

    if not isinstance(payload, dict):
        raise RuntimeError("User config is invalid.")

    api_key = next(
        (
            value
            for key in USER_CONFIG_KEYS
            if isinstance((value := payload.get(key)), str) and value.strip()
        ),
        None,
    )
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError("OpenAI API key is missing in user config.")

    return api_key.strip()


def prompt_api_key() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("OpenAI API key: ").strip()

    print("OpenAI API key: ", end="", flush=True)
    data = sys.stdin.buffer.readline()
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def validate_api_key(api_key: str, model: str) -> None:
    client = OpenAI(api_key=api_key)
    try:
        client.models.list()
    except AuthenticationError as error:
        raise RuntimeError("Invalid OpenAI API key.") from error
    except Exception as error:
        raise RuntimeError("Failed to validate OpenAI API key.") from error
