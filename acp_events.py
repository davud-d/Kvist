# -*- coding: utf-8 -*-
"""Kvist frontend events -> ACP session updates.

One direction only, no I/O: ``AcpEventTranslator.translate`` takes a
``frontend_protocol`` event and returns the ACP ``session/update`` payloads to
send. The ACP transport owns the connection; this module owns the mapping so
the mapping can be tested without a client.

Two shapes have to be reconciled. ACP message and thought chunks are
append-only deltas, while Kvist's ``stream_chunk`` / ``reasoning_chunk`` /
``action_output`` events are cumulative snapshots of one buffer. The
translator therefore keeps the last snapshot it saw per ``stream_id`` (or per
``call_id``) and emits only the new suffix. When a snapshot no longer extends
the previous one the buffer was reset upstream, so the whole new snapshot is
emitted and the buffer is replaced — never a silent duplicate of text the
client already has.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp import helpers, schema

# ACP ToolKind values. Kvist's ActionType set is larger than ACP's, so several
# actions share a kind; the action name still travels in the tool call title.
_ACTION_KINDS: dict[str, str] = {
    "read_file": "read",
    "read_multiple_files": "read",
    "write_file": "edit",
    "append_file": "edit",
    "replace_in_file": "edit",
    "list_dir": "read",
    "list_tree": "read",
    "find_files": "search",
    "search_code": "search",
    "git_status": "read",
    "git_diff": "read",
    "run_shell": "execute",
    "web_search": "search",
    "fetch_url": "fetch",
    "delegate_task": "other",
}

# FinalEvent.outcome values that mean the model refused rather than finished.
_REFUSAL_OUTCOMES = frozenset({"refused", "refusal"})


def action_tool_kind(action_type: str) -> str:
    """ACP tool kind for a Kvist action type, ``other`` when it has no kind."""
    return _ACTION_KINDS.get(action_type, "other")


@dataclass(slots=True)
class _StreamState:
    """Last cumulative snapshot seen for one ``stream_id``/``call_id``."""

    text: str = ""


@dataclass(slots=True)
class AcpUpdate:
    """One outbound ACP ``session/update`` payload.

    ``update`` is a schema object the transport passes to
    ``Client.session_update``. ``kind`` names it for tests and logging; it is
    never sent on the wire.
    """

    kind: str
    update: Any


@dataclass(slots=True)
class AcpEventTranslator:
    """Translate Kvist frontend events into ACP session updates.

    Stateful per turn: call :meth:`begin_turn` before the first event of a
    prompt and :meth:`end_turn` after the last one. The buffers that turn
    established are dropped at ``end_turn`` so two turns can never share a
    snapshot.
    """

    _streams: dict[str, _StreamState] = field(default_factory=dict)
    _thoughts: dict[str, _StreamState] = field(default_factory=dict)
    _outputs: dict[str, _StreamState] = field(default_factory=dict)
    _tools: set[str] = field(default_factory=set)
    _committed_bodies: set[str] = field(default_factory=set)
    _plan_entries: list[tuple[str, str]] = field(default_factory=list)
    _usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0})
    _stopped_by_user: bool = False
    _hit_step_budget: bool = False
    _refused: bool = False
    _errors: list[str] = field(default_factory=list)
    # Absolute workspace root for ACP tool-call locations; empty keeps paths raw.
    workspace_root: str = ""

    # -- turn lifecycle -----------------------------------------------------

    def begin_turn(self) -> None:
        """Start a turn: every buffer is fresh."""
        self._streams.clear()
        self._thoughts.clear()
        self._outputs.clear()
        self._tools.clear()
        self._committed_bodies.clear()
        self._plan_entries.clear()
        self._usage = {"input": 0, "output": 0, "total": 0}
        self._stopped_by_user = False
        self._hit_step_budget = False
        self._refused = False
        self._errors.clear()

    def end_turn(self) -> None:
        """Drop per-turn buffers so a later turn cannot inherit a snapshot."""
        self._streams.clear()
        self._thoughts.clear()
        self._outputs.clear()
        self._tools.clear()

    # -- outcomes ----------------------------------------------------------

    @property
    def usage(self) -> dict[str, int]:
        """Token totals accumulated this turn, for ``PromptResponse.usage``."""
        return dict(self._usage)

    @property
    def errors(self) -> list[str]:
        """Error messages the turn produced, in arrival order."""
        return list(self._errors)

    def stop_reason(self) -> str:
        """ACP ``stopReason`` for the turn that just ended."""
        if self._stopped_by_user:
            return "cancelled"
        if self._refused:
            return "refusal"
        if self._hit_step_budget:
            return "max_turn_requests"
        return "end_turn"

    # -- mapping -----------------------------------------------------------

    def translate(self, event: Any) -> list[AcpUpdate]:
        """Map one Kvist event to zero or more ACP session updates."""
        handler = getattr(self, f"_on_{getattr(event, 'type', '')}", None)
        if handler is None:
            return []
        return handler(event)

    def _text(self, text: str) -> schema.TextContentBlock:
        return helpers.text_block(text)

    def _delta(self, buffers: dict[str, _StreamState], key: str, snapshot: str) -> str:
        """New suffix of a cumulative snapshot, or the whole snapshot after a reset."""
        state = buffers.get(key)
        if state is not None and snapshot.startswith(state.text):
            suffix = snapshot[len(state.text) :]
            state.text = snapshot
            return suffix
        buffers[key] = _StreamState(snapshot)
        return snapshot

    # Message-bearing events share one rule: only text the client has not been
    # shown goes out. `message` and `final` are whole bodies rather than
    # snapshots, so they are deduplicated against committed stream bodies.

    def _agent_text(self, body: str) -> list[AcpUpdate]:
        if not body:
            return []
        if body in self._committed_bodies:
            return []
        self._committed_bodies.add(body)
        return [AcpUpdate("agent_message_chunk", helpers.update_agent_message(self._text(body)))]

    def _on_message(self, event: Any) -> list[AcpUpdate]:
        return self._agent_text(event.markdown)

    def _on_user(self, event: Any) -> list[AcpUpdate]:
        """A stored prompt, replayed so a reloaded thread shows both sides.

        Only the session journal carries this type — a live turn's prompt is
        already on the client — so it can never duplicate a prompt in Zed.
        """
        text = str(getattr(event, "text", "") or "")
        if not text:
            return []
        return [AcpUpdate("user_message_chunk", helpers.update_user_message(self._text(text)))]

    def _on_final(self, event: Any) -> list[AcpUpdate]:
        updates = self._agent_text(event.message)
        if str(getattr(event, "outcome", "done")) in _REFUSAL_OUTCOMES:
            self._refused = True
        return updates

    def _on_stream_start(self, event: Any) -> list[AcpUpdate]:
        self._streams.setdefault(event.stream_id, _StreamState())
        return []

    def _on_stream_chunk(self, event: Any) -> list[AcpUpdate]:
        suffix = self._delta(self._streams, event.stream_id, event.text)
        if not suffix:
            return []
        self._committed_bodies.add(event.text)
        return [AcpUpdate("agent_message_chunk", helpers.update_agent_message(self._text(suffix)))]

    def _on_stream_commit(self, event: Any) -> list[AcpUpdate]:
        suffix = self._delta(self._streams, event.stream_id, event.text)
        if not suffix:
            return []
        self._committed_bodies.add(event.text)
        return [AcpUpdate("agent_message_chunk", helpers.update_agent_message(self._text(suffix)))]

    def _on_reasoning_start(self, event: Any) -> list[AcpUpdate]:
        self._thoughts.setdefault(event.stream_id, _StreamState())
        return []

    def _on_reasoning_chunk(self, event: Any) -> list[AcpUpdate]:
        suffix = self._delta(self._thoughts, event.stream_id, event.text)
        if not suffix:
            return []
        return [AcpUpdate("agent_thought_chunk", helpers.update_agent_thought(self._text(suffix)))]

    def _on_reasoning_commit(self, event: Any) -> list[AcpUpdate]:
        _ = event
        return []

    # Plans -----------------------------------------------------------------

    def _on_plan(self, event: Any) -> list[AcpUpdate]:
        self._plan_entries = [(str(step), "pending") for step in event.plan]
        return [AcpUpdate("plan", helpers.update_plan(self._plan_entries_rendered()))]

    def _plan_entries_rendered(self) -> list[schema.PlanEntry]:
        return [
            helpers.plan_entry(content, priority="medium", status=status)  # type: ignore[arg-type]
            for content, status in self._plan_entries
        ]

    def _on_step(self, event: Any) -> list[AcpUpdate]:
        call_id = self._tool_call_id(event)
        kind = action_tool_kind(getattr(event, "action_type", "") or "")
        title = self._tool_title(event)
        args = getattr(event, "action_args", None)
        raw_input = dict(args) if isinstance(args, dict) else None
        locations = self._locations(getattr(event, "action_args", None))
        self._tools.add(call_id)
        start = helpers.start_tool_call(
            call_id,
            title=title,
            kind=kind,  # type: ignore[arg-type]
            status="in_progress",  # type: ignore[arg-type]
            locations=locations,
            raw_input=raw_input,
        )
        return [AcpUpdate("tool_call", start)]

    def _on_action_output(self, event: Any) -> list[AcpUpdate]:
        call_id = self._tool_call_id(event)
        suffix = self._delta(self._outputs, call_id, str(event.output or ""))
        if not suffix:
            return []
        if call_id not in self._tools:
            # Output can precede the step event when a call id is minted late;
            # open the tool call so the update has a start to attach to.
            self._tools.add(call_id)
            return [
                AcpUpdate(
                    "tool_call",
                    helpers.start_tool_call(
                        call_id,
                        title=call_id,
                        kind="other",  # type: ignore[arg-type]
                        status="in_progress",  # type: ignore[arg-type]
                        raw_output=suffix,
                    ),
                )
            ]
        return [
            AcpUpdate(
                "tool_call_update",
                helpers.update_tool_call(
                    call_id, status="in_progress", raw_output=str(event.output or "")  # type: ignore[arg-type]
                ),
            )
        ]

    def _on_action_result(self, event: Any) -> list[AcpUpdate]:
        call_id = self._tool_call_id(event)
        body = str(getattr(event, "output", "") or "")
        suffix = self._delta(self._outputs, call_id, body)
        status = "completed" if getattr(event, "ok", True) else "failed"
        title = self._tool_title(event)
        locations = []
        path = getattr(event, "path", None)
        if isinstance(path, str) and path:
            line = getattr(event, "first_line", None)
            locations = [
                helpers.ToolCallLocation(
                    path=self._safe_location(path),
                    line=line if isinstance(line, int) else None,
                )
            ]
        raw_output = body or suffix or None
        if call_id not in self._tools:
            self._tools.add(call_id)
            start = helpers.start_tool_call(
                call_id,
                title=title,
                kind=action_tool_kind(getattr(event, "action_type", "") or ""),
                status=status,  # type: ignore[arg-type]
                locations=locations,
                raw_output=raw_output,
            )
            return [AcpUpdate("tool_call", start)]
        return [
            AcpUpdate(
                "tool_call_update",
                helpers.update_tool_call(
                    call_id,
                    title=title,
                    status=status,  # type: ignore[arg-type]
                    locations=locations,
                    raw_output=raw_output,
                ),
            )
        ]

    # Metadata --------------------------------------------------------------

    def _on_session_title(self, event: Any) -> list[AcpUpdate]:
        return [
            AcpUpdate(
                "session_info_update",
                helpers.SessionInfoUpdate(
                    title=str(event.title), sessionUpdate="session_info_update"  # type: ignore[call-arg]
                ),
            )
        ]

    def _on_usage(self, event: Any) -> list[AcpUpdate]:
        self._usage["input"] += int(getattr(event, "input_tokens", 0) or 0)
        self._usage["output"] += int(getattr(event, "output_tokens", 0) or 0)
        self._usage["total"] = self._usage["input"] + self._usage["output"]
        limit = int(getattr(event, "context_limit", 0) or 0)
        used = int(getattr(event, "context_tokens", 0) or 0)
        if limit <= 0:
            return []
        return [
            AcpUpdate(
                "usage_update",
                schema.UsageUpdate(used=used, size=limit, sessionUpdate="usage_update"),
            )
        ]

    def _on_error(self, event: Any) -> list[AcpUpdate]:
        text = str(event.message or "")
        self._errors.append(text)
        if not text:
            return []
        return [AcpUpdate("agent_message_chunk", helpers.update_agent_message(self._text(text)))]

    def _on_status(self, event: Any) -> list[AcpUpdate]:
        # The stop budget is reported through the status channel rather than a
        # dedicated event: `run_loop` answers a limit-reached prompt there.
        status = str(getattr(event, "status", "") or "")
        if "budget" in status or "limit reached" in status or "бюджет" in status:
            self._hit_step_budget = True
        return []

    # -- helpers -----------------------------------------------------------

    def note_user_cancel(self) -> None:
        """Record that the client cancelled the turn."""
        self._stopped_by_user = True

    def _tool_call_id(self, event: Any) -> str:
        call_id = str(getattr(event, "call_id", "") or "")
        if call_id:
            return call_id
        step = int(getattr(event, "step", 0) or 0)
        batch = str(getattr(event, "batch_id", "") or "")
        return f"kvist-step-{step}-{batch or 'single'}"

    def _tool_title(self, event: Any) -> str:
        description = str(getattr(event, "action_description", "") or "").strip()
        if description:
            return description[:17]
        summary = str(getattr(event, "summary", "") or "").strip()
        if summary:
            return summary[:60]
        action_type = str(getattr(event, "action_type", "") or "").strip()
        return action_type or "tool"

    def _locations(self, action_args: Any) -> list[schema.ToolCallLocation]:
        if not isinstance(action_args, dict):
            return []
        locations: list[schema.ToolCallLocation] = []
        for key in ("path", "file_path", "target", "destination"):
            value = action_args.get(key)
            if isinstance(value, str) and value:
                locations.append(helpers.ToolCallLocation(path=self._safe_location(value)))
        paths = action_args.get("paths")
        if isinstance(paths, list):
            for value in paths:
                if isinstance(value, str) and value:
                    locations.append(helpers.ToolCallLocation(path=self._safe_location(value)))
        return locations

    def _safe_location(self, path: str) -> str:
        """Absolute path for an ACP location, or the raw string when unknown.

        ACP locations are absolute. Kvist paths are workspace-relative by
        design, so a location is only emitted when the transport has supplied
        the workspace root (``workspace_root``); otherwise the raw relative
        string is dropped rather than presented as a path the client would
        resolve against the wrong directory.
        """
        root = self.workspace_root
        if not root:
            return path

        candidate = Path(path)
        if candidate.is_absolute():
            return str(candidate)
        return str(Path(root) / candidate)
