# -*- coding: utf-8 -*-
"""How big the active model's context is and how much of it the run occupies.

``used`` is everything that sits in the window right now:

* ``prompt``: the last request as it was sent (system prompt, tool schemas,
  history, the person's request, earlier tool results);
* ``generated``: what the call in flight has produced so far, or produced in
  the last call: reasoning, text and tool-call arguments;
* ``pending``: the person's new message and tool results that exist but have
  not been sent to the model yet.

The next request replaces all three with its own measured prompt.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

# Minimum gap between two live ``context`` events while a call streams.
REFRESH_SECONDS = 2.0
# Tokenising a growing stream is repeated work; counting is rate-limited.
_COUNT_INTERVAL_SECONDS = 0.25


@dataclass(slots=True)
class ContextLedger:
    prompt: int = 0
    generated: int = 0
    pending: int = 0
    streaming: bool = False
    last_publish: float = 0.0
    last_count: float = 0.0
    published: int = -1

    @property
    def total(self) -> int:
        return self.prompt + self.generated + self.pending


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """``limit`` is the budget enforced for the active model; ``used`` is the sum of the parts."""

    limit: int
    used: int
    prompt: int = 0
    generated: int = 0
    pending: int = 0

    @property
    def free(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def fraction(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(1.0, self.used / self.limit)


def context_usage(agent) -> ContextUsage:
    from kernel.agent_model import context_token_limit

    ledger = agent._context_ledger
    return ContextUsage(
        limit=context_token_limit(agent),
        used=ledger.total,
        prompt=ledger.prompt,
        generated=ledger.generated,
        pending=ledger.pending,
    )


def begin_call(agent, prompt_tokens: int) -> None:
    """A conversation request is about to be sent: its prompt is the new baseline."""
    ledger = agent._context_ledger
    ledger.prompt = max(0, prompt_tokens)
    ledger.generated = 0
    ledger.pending = 0
    ledger.streaming = True
    _publish(agent, force=True)


def mute_generation(agent) -> None:
    """The call about to stream is not part of the conversation (report, router)."""
    agent._context_ledger.streaming = False


def note_generated(agent, text: Callable[[], str]) -> None:
    """Count what the streaming call has produced so far; rate-limited."""
    ledger = agent._context_ledger
    now = time.monotonic()
    if not ledger.streaming or now - ledger.last_count < _COUNT_INTERVAL_SECONDS:
        return
    ledger.last_count = now
    ledger.generated = agent._count_tokens(text())
    _publish(agent, force=False)


def end_call(agent, prompt_tokens: int, generated_tokens: int) -> None:
    """The call finished with the provider's (or measured) exact totals."""
    ledger = agent._context_ledger
    ledger.prompt = max(0, prompt_tokens)
    ledger.generated = max(0, generated_tokens)
    ledger.pending = 0
    ledger.streaming = False
    _publish(agent, force=True)


def note_pending(agent, text: str) -> None:
    """Text that will enter the next prompt: the person's message or a tool result."""
    agent._context_ledger.pending += agent._count_tokens(text)
    _publish(agent, force=True)


def _publish(agent, *, force: bool) -> None:
    callback = getattr(getattr(agent, "_ui", None), "print_context", None)
    if callback is None:
        return
    ledger = agent._context_ledger
    now = time.monotonic()
    if ledger.total == ledger.published:
        return
    if not force and now - ledger.last_publish < REFRESH_SECONDS:
        return
    ledger.last_publish = now
    ledger.published = ledger.total
    usage = context_usage(agent)
    callback(
        {
            "context_tokens": usage.used,
            "context_limit": usage.limit,
            "prompt": usage.prompt,
            "generated": usage.generated,
            "pending": usage.pending,
        }
    )


def describe(usage: ContextUsage) -> str:
    """Text for ``/context``: used of limit, share, free and what the used part is made of."""
    if usage.limit <= 0:
        return "Context limit is not known yet."
    return (
        f"Context: {usage.used:,} / {usage.limit:,} tokens "
        f"({round(usage.fraction * 100)}%), {usage.free:,} free\n"
        f"  prompt (system, history, request, tool results): {usage.prompt:,}\n"
        f"  generated (reasoning, text, tool calls): {usage.generated:,}\n"
        f"  not sent yet (new message, tool results): {usage.pending:,}"
    )
