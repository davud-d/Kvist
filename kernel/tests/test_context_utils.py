from __future__ import annotations

from kernel.context.context_utils import trim_history


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


def test_trim_history_keeps_reasoning_content_on_retained_messages():
    kept = {"role": "assistant", "content": "answer", "reasoning_content": "because"}
    messages = (
        [{"role": "system", "content": "s"}]
        + [{"role": "user", "content": str(i)} for i in range(4)]
        + [kept]
    )

    trimmed = trim_history(messages, max_recent=2)

    # The retained assistant message is the same object — no key is stripped.
    assert trimmed[-1] is kept
    assert trimmed[-1]["reasoning_content"] == "because"
