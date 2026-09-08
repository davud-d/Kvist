# -*- coding: utf-8 -*-
"""Sliding-window context trimmer for the agent message history.

Keeps the first system prompt and the last `max_recent` messages,
hard-cutting everything in between.  O(N) time, O(N) memory where N
equals the number of retained messages.

Integration point
-----------------
In ``agent_model.call_model`` right after the ``messages`` list is
assembled (and before ``_create_json_completion``), insert::

    from context_utils import trim_history
    messages = trim_history(messages, max_recent=4)
"""
from __future__ import annotations

from typing import Any

# A retained message is passed through untouched — its dict identity and every
# key survive. This matters for ``reasoning_content``: DeepSeek's thinking mode
# rejects (HTTP 400) a history where an assistant ``content`` is present without
# the ``reasoning_content`` that came with it, so compression may drop a whole
# turn but must never strip that field from a turn it keeps.


def trim_history(
    messages: list[dict[str, Any]],
    max_recent: int = 4,
) -> list[dict[str, Any]]:
    """Return a trimmed copy of *messages* using a sliding-window strategy.

    Rules
    -----
    1. The **first** message whose ``role`` is ``"system"`` is always kept.
    2. The last *max_recent* non-system messages are kept.
    3. Everything in the middle is discarded.
    4. If the history is already short enough, return as-is (no copy).
    5. A kept message is never rewritten — every key it carries (including
       ``reasoning_content``) survives (see the module note above).

    Parameters
    ----------
    messages:
        List of chat-completion message dicts (``{"role": ..., "content": ...}``).
    max_recent:
        How many recent messages to preserve (default **4**).

    Returns
    -------
    list[dict[str, Any]]
        A valid list of message dicts safe for the chat-completion API.
    """
    if not messages:
        return messages

    # ── Separate the leading system prompt ────────────────────────
    system_prefix: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []

    for msg in messages:
        if not system_prefix and msg.get("role") == "system":
            system_prefix.append(msg)
        else:
            rest.append(msg)

    # Nothing to trim — the history already fits.
    if len(rest) <= max_recent:
        return messages

    # ── Sliding window: keep only the tail ────────────────────────
    return system_prefix + rest[-max_recent:]
