from __future__ import annotations

from context_utils import trim_history


def test_trim_history_keeps_first_system_and_recent_messages():
    messages = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": str(i)} for i in range(6)
    ]

    trimmed = trim_history(messages, max_recent=2)

    assert trimmed == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "4"},
        {"role": "user", "content": "5"},
    ]


def test_trim_history_returns_original_when_short_enough():
    messages = [{"role": "user", "content": "1"}]
    assert trim_history(messages, max_recent=4) is messages
