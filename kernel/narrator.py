# -*- coding: utf-8 -*-
"""The narrator: a fast model voices what the strong model is doing while the person waits.

Only a voice frontend hears it (``AgentConfig.voice_mode``). It speaks while the
active model is not the fast one: a fast call takes seconds and needs no company.
Its lines are live-only ``narration`` events for the person's ears. They never
enter a model request, so there is nothing to log for reconstruction.

What it says rests on what exists: the thinking the provider streams, or, when
the provider shows none, the person's request and the steps the strong model has
already taken. A line is never invented to fill the time: the prompt lets it
answer ``SILENCE``, and a call whose material did not change is skipped.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

from kernel.redaction import defang_prompt_boundary_tags, redact_text

SILENCE = "SILENCE"
# Its own recent lines, so it does not say the same thing twice.
_REMEMBERED_LINES = 6
_QUOTES = "\"'«»“”„"


def narrating(agent: Any, model: str) -> AbstractContextManager[None]:
    """Context for one action-model call: the narrator speaks while it runs."""
    if not agent.config.voice_mode:
        return nullcontext()
    narrator = getattr(agent, "_narrator", None)
    if narrator is None:
        narrator = agent._narrator = Narrator(agent)
    return narrator.during_call(model)


def _material_text(text: str) -> str:
    return defang_prompt_boundary_tags(redact_text(text))


def narration_material(
    *,
    language: str,
    goal: str,
    steps: list[str],
    fresh_reasoning: str,
    said: list[str],
    elapsed: float,
) -> str:
    """The narrator's user message. Quoted work is wrapped as untrusted data, like tool output."""
    work = "\n".join(steps) or "(no steps yet)"
    thinking = fresh_reasoning.strip() or "(the provider shows no thinking)"
    spoken = "\n".join(f"- {line}" for line in said) or "(nothing yet)"
    return (
        f"Language of the request: {language}\n"
        f"Seconds this step has been running: {elapsed:.0f}\n\n"
        f"The person's request:\n{_material_text(goal)}\n\n"
        '<UNTRUSTED_TOOL_OUTPUT source="narrator_material">\n'
        f"What the work has done so far:\n{_material_text(work)}\n\n"
        f"Thinking since you last spoke:\n{_material_text(thinking)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>\n\n"
        f"What you already said:\n{spoken}"
    )


def spoken_line(reply: str) -> str:
    """The line to say, or "" when the narrator chose silence."""
    line = reply.strip().strip(_QUOTES).strip()
    if line.upper().rstrip(".!…") == SILENCE:
        return ""
    return line


class Narrator:
    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._said: deque[str] = deque(maxlen=_REMEMBERED_LINES)
        self._reasoning = ""
        self._consumed = 0
        self._call_started = 0.0

    def note_reasoning(self, text: str) -> None:
        """Take the cumulative thinking of the call in flight ("" = a new call began)."""
        self._reasoning = text

    @contextmanager
    def during_call(self, model: str) -> Iterator[None]:
        if not self._can_speak(model):
            yield
            return
        stop = threading.Event()
        self._reasoning = ""
        self._consumed = 0
        self._call_started = time.monotonic()
        threading.Thread(target=self._run, args=(stop,), daemon=True, name="kvist-narrator").start()
        try:
            yield
        finally:
            # A line still being written is dropped, never spoken over the next step.
            stop.set()

    def _can_speak(self, model: str) -> bool:
        agent = self._agent
        return (
            model != agent.config.fast_model
            and getattr(agent._ui, "print_narration", None) is not None
        )

    def _run(self, stop: threading.Event) -> None:
        config = self._agent.config
        if stop.wait(config.narrator_delay_seconds):
            return
        last_seen: tuple[int, int] | None = None
        while True:
            seen = (len(self._reasoning), max(self._agent._state.rolling_summaries, default=0))
            if seen != last_seen:
                last_seen = seen
                if not self._narrate_once(stop):
                    return
            if stop.wait(config.narrator_interval_seconds):
                return

    def _narrate_once(self, stop: threading.Event) -> bool:
        """Say one line; False when the narrator should stay quiet for the rest of this call."""
        agent = self._agent
        reasoning = self._reasoning
        fresh = reasoning[self._consumed :] if len(reasoning) >= self._consumed else reasoning
        material = narration_material(
            language=agent._state.response_language or "en",
            goal=agent._state.user_goal,
            steps=self._recent_steps(),
            fresh_reasoning=fresh,
            said=list(self._said),
            elapsed=time.monotonic() - self._call_started,
        )
        try:
            reply = self._ask(material)
        except Exception as error:  # provider failures are many; a narrator must never take the run down
            agent._ui.debug_print(f"[narrator] {redact_text(str(error))}")
            return False
        self._consumed = len(reasoning)
        line = spoken_line(reply)
        if stop.is_set() or not line:
            return not stop.is_set()
        self._said.append(line)
        agent._ui.print_narration(line)
        return True

    def _recent_steps(self) -> list[str]:
        """The newest steps of the work that fit the budget, oldest first."""
        agent = self._agent
        summaries = agent._state.rolling_summaries
        chosen: list[str] = []
        used = 0
        for step in sorted(summaries, reverse=True):
            entry = f"step {step}: {summaries[step]}"
            used += agent._count_tokens(entry)
            if used > agent.config.narrator_context_tokens:
                break
            chosen.append(entry)
        return chosen[::-1]

    def _ask(self, material: str) -> str:
        from kernel.agent_model import (
            _create_completion_with_reasoning_fallback,
            _sends_temperature,
            load_narrator_prompt,
            safe_extract_completion_content,
        )
        from kernel.reasoning_effort import merge_reasoning_kwargs

        agent = self._agent
        config = agent.config
        model = config.fast_model
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": load_narrator_prompt()},
                {"role": "user", "content": material},
            ],
            "timeout": config.api_timeout,
        }
        if _sends_temperature(agent, model):
            request["temperature"] = config.model_temperature
        # A line of a few words gains nothing from thinking first.
        merge_reasoning_kwargs(request, config.provider, "false")
        response = _create_completion_with_reasoning_fallback(agent, request)
        return safe_extract_completion_content(response, config.provider, model)
