from __future__ import annotations

import time

from redaction import (
    REDACTED,
    escape_terminal_controls,
    redact_text,
    redact_value,
    sanitize_display_text,
)


def test_redact_text_covers_quoted_cli_and_url_credentials():
    synthetic = "synthetic-secret-value"
    message = (
        f'password="{synthetic} with spaces" '
        f"--api-key {synthetic} "
        f"https://user:{synthetic}@example.test/path"
    )

    redacted = redact_text(message)

    assert synthetic not in redacted
    assert redacted.count(REDACTED) == 3


def test_display_sanitizer_redacts_and_makes_controls_visible():
    synthetic = "sk-proj-" + "x" * 24
    original = f"Bearer {synthetic}\x1b[2J\u202ehidden\x08"

    sanitized = sanitize_display_text(original)

    assert synthetic not in sanitized
    assert "\x1b" not in sanitized
    assert "\u202e" not in sanitized
    assert "\x08" not in sanitized
    assert "\\x1b" in sanitized
    assert "\\u202e" in sanitized
    assert "\\x08" in sanitized
    assert escape_terminal_controls("line\n\tvalue") == "line\n\tvalue"
    assert sanitize_display_text("family 👨‍👩‍👧") == "family 👨‍👩‍👧"


def test_redact_value_handles_sensitive_camelcase_keys_and_secret_keys():
    synthetic_token = "sk-proj-" + "a" * 24
    payload = {
        "apiKey": "arbitrary credential",
        "Authorization": "arbitrary authorization",
        synthetic_token: "mapped value",
        "nested": [{"clientSecret": "another credential"}],
    }

    redacted = redact_value(payload)
    serialized = repr(redacted)

    assert synthetic_token not in serialized
    assert "arbitrary credential" not in serialized
    assert "arbitrary authorization" not in serialized
    assert "another credential" not in serialized


def test_redact_value_preserves_non_secret_token_metrics():
    payload = {
        "token_budget": 4096,
        "max_tokens": 8192,
        "input_tokens": 128,
    }

    assert redact_value(payload) == payload


def test_redact_value_safely_stringifies_unknown_objects():
    class Diagnostic:
        def __str__(self) -> str:
            return "Authorization: Bearer synthetic-value-123456"

    assert redact_value(Diagnostic()) == "Authorization: [REDACTED]"


def test_redaction_is_idempotent():
    original = "Authorization: Bearer synthetic-value-123456"
    once = redact_text(original)

    assert redact_text(once) == once


def test_redaction_preserves_common_source_expressions():
    source = (
        'token = os.environ.get("TOKEN")\n'
        "api_key = settings.API_KEY\n"
        "credentials: dict[str, str]\n"
        "is_password=True\n"
        "max_tokens=8192\n"
    )

    assert redact_text(source) == source


def test_spaced_high_entropy_assignment_is_still_redacted():
    synthetic = "AbCdEf0123456789AbCdEf0123456789"

    redacted = redact_text(f"api_key = {synthetic}")

    assert synthetic not in redacted
    assert REDACTED in redacted


def test_long_prefixed_sensitive_key_is_not_a_redaction_bypass():
    synthetic = "AbCdEf0123456789AbCdEf0123456789"
    key = ("ordinaryprefix" * 100) + "_access_token"

    assignment = redact_text(f"{key}={synthetic}")
    unusual_cli = redact_text(f"----{key} {synthetic}")

    assert synthetic not in assignment
    assert assignment == f"{key}={REDACTED}"
    assert synthetic not in unusual_cli
    assert unusual_cli == f"----{key} {REDACTED}"


def test_megabyte_text_redaction_remains_bounded_and_finds_tail_secret():
    synthetic = "AbCdEf0123456789AbCdEf0123456789"
    message = ("ordinary multilingual text 🌲\n" * 35_000) + (
        f"api_key={synthetic}"
    )

    started = time.monotonic()
    redacted = redact_text(message)
    elapsed = time.monotonic() - started

    assert synthetic not in redacted
    assert redacted.endswith(f"api_key={REDACTED}")
    # The regression took multiple seconds per MiB. Keep a generous bound so
    # noisy CI hosts still distinguish the linear implementation.
    assert elapsed < 1.0
