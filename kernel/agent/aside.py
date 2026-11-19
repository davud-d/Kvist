# -*- coding: utf-8 -*-
"""The aside: a fast model speaks for the agent when the agent has worked a long while without a word.

The action loop lets a response carry calls and no text, and the runtime shows what
every call touches, so a person can watch a dozen calls go by and hear nothing.
When the agent has been silent for ``SILENT_STEPS`` responses or ``SILENT_CALLS``
calls, a thread asks the fast model for one line in the agent's own voice, while
the strong model goes on with its next call: the aside never holds the work up.
The line streams to the frontend as ``aside`` events marked ``from_model: false``.

It is for the person's eyes only. It never enters a model request and is never
journaled, so the strong model cannot take it for something it said, and a
resumed session has nothing to reconcile. Like the narrator, it may answer
``SILENCE``; nothing is sent for that, not even an empty line.
"""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text, redact_value
from kernel.session.session_events import truncate_middle

SILENCE = "SILENCE"
# Silence is measured two ways: a few sequential responses are a lot of work with
# nothing said, and so is one response that fans out into many parallel calls.
SILENT_STEPS = 3
SILENT_CALLS = 4
# Its own recent lines, so it does not say the same thing twice.
_REMEMBERED_LINES = 6
_KEPT_CALLS = 60
_QUOTES = "\"'«»“”„"


@dataclass(slots=True, frozen=True)
class AsideCall:
    """One call of the work as the aside sees it: what was asked, and for a shell command what came back."""

    label: str
    name: str
    arguments: dict[str, Any]
    output: str = ""


def aside_calls(planned: Iterable[Any], answers: list[str]) -> list[AsideCall]:
    """The calls of one response paired with their answers (``planned`` and ``answers`` share call order)."""
    calls = []
    for item, answer in zip(planned, answers):
        command = item.command
        label = command.action.description if command is not None else item.request.name or "?"
        output = answer if item.request.name == "run_shell" else ""
        calls.append(AsideCall(label, item.request.name or "?", dict(item.request.arguments or {}), output))
    return calls


def note_turn(agent: Any, spoke: bool, calls: list[AsideCall]) -> None:
    """Tell the agent's aside that a response ran: ``spoke`` is whether it carried words for the person."""
    aside = getattr(agent, "_aside", None)
    if aside is None:
        aside = agent._aside = Aside(agent)
    aside.note_response(spoke, calls)


def load_aside_prompt() -> str:
    from kernel.agent.agent_model import _PROMPT_CACHE, _PROMPT_DIR

    if "aside" not in _PROMPT_CACHE:
        _PROMPT_CACHE["aside"] = (_PROMPT_DIR / "aside_prompt.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE["aside"]


def spoken_line(reply: str) -> str:
    """The line to say, or "" when the model chose silence."""
    line = reply.strip().strip(_QUOTES).strip()
    if line.upper().rstrip(".!…") == SILENCE:
        return ""
    return line


def may_become_silence(reply: str) -> bool:
    """Whether what has streamed so far could still turn out to be the word SILENCE."""
    start = reply.strip().upper().rstrip(".!…")
    return SILENCE.startswith(start)


def _material_text(text: str) -> str:
    return defang_prompt_boundary_tags(redact_text(text))


class Aside:
    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._calls: deque[AsideCall] = deque(maxlen=_KEPT_CALLS)
        self._said: deque[str] = deque(maxlen=_REMEMBERED_LINES)
        self._silent_steps = 0
        self._silent_calls = 0
        self._speaking = threading.Event()

    def note_response(self, spoke: bool, calls: list[AsideCall]) -> None:
        self._calls.extend(calls)
        if spoke:
            self._silent_steps = self._silent_calls = 0
            return
        self._silent_steps += 1
        self._silent_calls += len(calls)
        if self._silent_steps < SILENT_STEPS and self._silent_calls < SILENT_CALLS:
            return
        if not self._can_speak():
            return
        # Counted from here even when the model answers SILENCE: asking again on every
        # step would cost a call each time for nothing new to say.
        self._silent_steps = self._silent_calls = 0
        material = self._material()
        self._calls.clear()
        self._speaking.set()
        threading.Thread(target=self._speak, args=(material,), daemon=True, name="kvist-aside").start()

    def _can_speak(self) -> bool:
        agent = self._agent
        config = agent.config
        # Voice has its own narrator; a helper or headless run has nobody to answer.
        return (
            config.interactive
            and not config.voice_mode
            and getattr(agent._ui, "print_aside", None) is not None
            and not self._speaking.is_set()
        )

    def _material(self) -> str:
        from kernel.agent.agent_tools import earlier_turns, recent_steps
        from kernel.context.system_context import workspace_instructions_context

        agent = self._agent
        state = agent._state
        request = "\n\n".join([state.user_goal, *state.task_updates])
        instructions = workspace_instructions_context(agent.executor.workspace, agent.executor.active_role)
        steps = "\n".join(recent_steps(agent)) or "(no steps yet)"
        spoken = "\n".join(f"- {line}" for line in self._said) or "(nothing yet)"
        return (
            f"Language of the request: {state.response_language or 'en'}\n\n"
            f"The person's request:\n{_material_text(request)}\n\n"
            '<UNTRUSTED_TOOL_OUTPUT source="aside_material">\n'
            f"{_material_text(instructions) or '(the project has no instructions)'}\n\n"
            f"Steps of this turn so far:\n{_material_text(steps)}\n\n"
            f"Earlier turns of this session:\n{_material_text(earlier_turns(agent)) or '(none)'}\n\n"
            f"Calls since you last spoke:\n{_material_text(self._rendered_calls())}\n"
            "</UNTRUSTED_TOOL_OUTPUT>\n\n"
            f"What you already said:\n{spoken}"
        )

    def _rendered_calls(self) -> str:
        """The newest calls that fit the budget, oldest first; one call may take a quarter of it at most."""
        agent = self._agent
        budget = agent.config.narrator_context_tokens
        chosen: list[str] = []
        used = 0
        for call in reversed(self._calls):
            entry = truncate_middle(_render_call(call), budget)
            used += agent._count_tokens(entry)
            if chosen and used > budget:
                break
            chosen.append(entry)
        return "\n".join(reversed(chosen)) or "(no calls)"

    def _speak(self, material: str) -> None:
        agent = self._agent
        try:
            self._stream_line(material)
        except Exception as error:  # provider failures are many; an aside must never take the run down
            agent._ui.debug_print(f"[aside] {redact_text(str(error))}")
        finally:
            self._speaking.clear()

    def _stream_line(self, material: str) -> None:
        from kernel.agent.agent_model import safe_extract_stream_delta

        agent = self._agent
        stream = self._open_stream(material)
        aside_id = uuid.uuid4().hex[:12]
        raw = ""
        sent = ""
        voiced = False
        try:
            for chunk in stream:
                if agent._cancel_event.is_set():
                    break
                delta = safe_extract_stream_delta(chunk)
                piece = getattr(delta, "content", None) if delta is not None else None
                if not piece:
                    continue
                raw += piece
                if not voiced and may_become_silence(raw):
                    continue
                voiced = True
                # Redacted as a whole each time: a secret split across two chunks would pass piecemeal.
                text = redact_text(raw.strip().lstrip(_QUOTES))
                if text.startswith(sent) and text != sent:
                    agent._ui.print_aside(aside_id, text[len(sent):], False)
                    sent = text
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()
        line = spoken_line(raw)
        if not voiced or not line:
            return
        self._said.append(line)
        # The whole line, so a frontend that missed or mis-added a piece ends up right.
        agent._ui.print_aside(aside_id, redact_text(line), True)

    def _open_stream(self, material: str) -> Any:
        from kernel.agent.agent_model import _create_completion_with_reasoning_fallback, _sends_temperature
        from kernel.llm.reasoning_effort import merge_reasoning_kwargs

        agent = self._agent
        config = agent.config
        model = config.fast_model
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": load_aside_prompt()},
                {"role": "user", "content": material},
            ],
            "timeout": config.api_timeout,
            "stream": True,
        }
        if _sends_temperature(agent, model):
            request["temperature"] = config.model_temperature
        # One short line gains little from long thinking, and the person is waiting for it.
        merge_reasoning_kwargs(request, config.provider, "low")
        return _create_completion_with_reasoning_fallback(agent, request)


def _render_call(call: AsideCall) -> str:
    arguments = json.dumps(redact_value(call.arguments), ensure_ascii=False, default=str)
    text = f"- {call.label} [{call.name}] {arguments}"
    if call.name != "run_shell":
        return text
    return f"{text}\n  output:\n{call.output}"
