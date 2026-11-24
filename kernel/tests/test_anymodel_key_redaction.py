# -*- coding: utf-8 -*-
"""An AnyModel API key saved in an existing config never reaches logs: the same
suffix-based matching in redaction.py that covers every other provider's key.
"""

from __future__ import annotations

from kernel.sandbox.redaction import redact_text, redact_value

SYNTHETIC_KEY = "am-synthetic-not-a-real-secret-0123456789"


def test_anymodel_api_key_matches_the_generic_sensitive_key_suffix():
    redacted = redact_value({"anymodel_api_key": SYNTHETIC_KEY})
    assert redacted["anymodel_api_key"] == "[REDACTED]"


def test_anymodel_api_key_assignment_is_redacted_in_free_text():
    line = f"anymodel_api_key={SYNTHETIC_KEY}"
    redacted = redact_text(line)
    assert SYNTHETIC_KEY not in redacted
