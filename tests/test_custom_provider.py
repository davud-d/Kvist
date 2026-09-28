# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from custom_provider import (
    CustomProviderError,
    normalize_base_url,
    normalize_custom_provider_payload,
    validate_provider_name,
)


def _payload(**overrides):
    base = {
        "name": "my-llama",
        "label": "My Llama",
        "protocol": "openai",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "streaming": False,
        "models": {"fast": "m", "smart": "m"},
    }
    base.update(overrides)
    return base


def test_normalizes_a_full_payload():
    name, config = normalize_custom_provider_payload(_payload())
    assert name == "my-llama"
    assert config["protocol"] == "openai"
    assert config["base_url"] == "https://api.example.com/v1"
    assert config["models"] == {"fast": "m", "smart": "m"}
    assert config["streaming"] is False


def test_scheme_prefixed_and_trailing_slash_dropped():
    assert normalize_base_url("api.example.com/") == "https://api.example.com"


def test_no_v1_magic_in_the_pure_normaliser():
    _name, config = normalize_custom_provider_payload(_payload(base_url="https://api.example.com"))
    assert config["base_url"] == "https://api.example.com"


def test_default_label_derived_from_name():
    _name, config = normalize_custom_provider_payload(_payload(name="local_vllm", label=""))
    assert config["label"] == "Local Vllm"


@pytest.mark.parametrize("bad", ["", "  ", "Has Space", "bad!", "a/b"])
def test_rejects_invalid_names(bad):
    with pytest.raises(CustomProviderError):
        validate_provider_name(bad)


def test_name_is_lowercased():
    assert validate_provider_name("  MyLlama ") == "myllama"


def test_rejects_unknown_protocol():
    with pytest.raises(CustomProviderError):
        normalize_custom_provider_payload(_payload(protocol="grpc"))


def test_rejects_empty_base_url():
    with pytest.raises(CustomProviderError):
        normalize_custom_provider_payload(_payload(base_url="  "))


def test_rejects_missing_model_role():
    with pytest.raises(CustomProviderError):
        normalize_custom_provider_payload(_payload(models={"fast": "m"}))


def test_rejects_non_object_payload():
    with pytest.raises(CustomProviderError):
        normalize_custom_provider_payload("nope")
