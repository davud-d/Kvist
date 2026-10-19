# -*- coding: utf-8 -*-
"""Reading a finished stretch of a conversation and keeping what a later task would rediscover.

The model only proposes; the runtime decides. The flow, in order:

1. **Collect** only finished turns of the session's event journal, after the
   point the extractor last reached, and only the events that carry what was
   asked, done and concluded (requests, answers, plans, the agent's account of
   each step, a tool result's one-line summary). Thoughts and raw tool output
   stay out: they are the bulk of a journal and the reason a provider would see
   far more than memory needs.
2. **Filter**: every line goes through the same privacy gate as a stored
   record; a line that fails it is withheld, and the batch says a line was.
3. **Ask** the fast model for candidates, with the records the project already
   keeps beside the events so it does not restate them.
4. **Validate** each candidate: its kind, that every event it cites was in the
   batch, its fields and the privacy gate again. A reply that is not JSON
   stores nothing and leaves the checkpoint where it was.
5. **Store** the valid ones with the session's new checkpoint in one
   transaction, under an idempotency key, so reading a batch twice stores it once.

Every extracted record is ``inferred``: it is a model's reading of a
conversation, not something a tool showed. A later task that observes the
same thing raises it to ``observed`` by merging.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError
from kernel.memory import privacy
from kernel.memory.errors import MemoryPrivacyError, MemoryProviderError, MemoryStoreError, MemoryValidationError
from kernel.memory.schema import RELATIONS
from kernel.memory.store import DUPLICATE, MAX_REASON, MemoryStore, NewRecord, Source
from kernel.redaction import defang_prompt_boundary_tags

ACTOR = "extractor"
# Raise when the prompt, the collector or the gate changes what a batch yields:
# the idempotency key then lets the same events be read again under the new rules.
EXTRACTION_VERSION = 4
POLICY_VERSION = 1
MAX_BATCH_TOKENS = 12_000
MAX_CALLS_PER_RUN = 4
MAX_CANDIDATES = 10
_KNOWN_RECORDS = 15
_EXTRACTED_KINDS = ("project_fact", "decision")
_WITHHELD = "[a line was withheld: it looked like a secret or personal data]"
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_CANDIDATE_REF_RE = re.compile(r"^c\d+$")
_KEY_BLOCK_RE = re.compile(r"-----BEGIN [^-\n]*(?:PRIVATE KEY|KEY BLOCK)-----.*?(?:-----END [^-\n]*-----|\Z)",
                           re.DOTALL)
_LINK_TYPES = (DUPLICATE, *RELATIONS)
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system" / "memory_extract_prompt.txt"
_SKIPPED_STEPS = ("final_answer", "report_to_user")


@dataclass(slots=True)
class Event:
    seq: int
    type: str
    text: str
    # A tool result that succeeded: the only kind of event that shows something, not just says it.
    tool_ok: bool = False

    def line(self) -> str:
        return json.dumps({"id": f"e{self.seq}", "type": self.type, "text": self.text}, ensure_ascii=False)


@dataclass(slots=True)
class ModelAccess:
    """How the extractor reaches a model; built by the caller, which owns the provider."""

    label: str
    ask: Callable[[list[dict[str, str]]], str]
    count_tokens: Callable[[str], int]


def process_session(store: MemoryStore, session: str, model: ModelAccess,
                    reserve: Callable[[int], int] | None = None) -> dict[str, Any]:
    """Read the session's new finished turns in batches and store what they yield.

    ``reserve(wanted)`` grants how many model calls this run may make (a background
    run draws on a daily cap); it is asked only once there is something to read.
    """
    events, boundary = collect(store, session, store.processed_up_to(session))
    summary: dict[str, Any] = {"session": session, "events": len(events), "batches": 0, "accepted": [],
                               "merged": [], "rejected": [], "links": [], "recap": None, "left": 0, "problem": ""}
    if boundary is None:
        return summary
    groups = batches(events, model.count_tokens)
    if not groups:
        _skip_to(store, session, boundary)
        return summary
    wanted = min(len(groups), MAX_CALLS_PER_RUN)
    granted = reserve(wanted) if reserve is not None else wanted
    if granted == 0:
        summary["problem"] = "today's limit of background memory calls is used up; it continues tomorrow"
    for index, batch in enumerate(groups[:granted]):
        last_seq = boundary if index == len(groups) - 1 else batch[-1].seq
        try:
            outcome = _process_batch(store, session, batch, last_seq, model)
        except (MemoryValidationError, MemoryProviderError) as error:
            # The checkpoint stays before this batch, so the next run reads it again.
            summary["problem"] = str(error)
            break
        summary["batches"] += 1
        summary["recap"] = outcome["recap"] or summary["recap"]
        for key in ("accepted", "merged", "rejected", "links"):
            summary[key].extend(outcome[key])
    summary["left"] = sum(len(batch) for batch in groups[summary["batches"]:])
    return summary


def _skip_to(store: MemoryStore, session: str, boundary: int) -> None:
    """Finished turns with nothing worth reading (only thoughts): move the checkpoint past them."""
    after = store.processed_up_to(session)
    store.apply_extraction(session=session, first_seq=after + 1, last_seq=boundary,
                           key=idempotency_key(session, after + 1, boundary), records=[], actor=ACTOR)


def collect(store: MemoryStore, session: str, after_seq: int) -> tuple[list[Event], int | None]:
    """Finished turns after ``after_seq``, filtered; the boundary is the last finished turn's end."""
    from kernel.agent_logging import get_journal_path, is_valid_session_name
    from kernel.session_events import MAX_HISTORY_LIMIT, read_history
    from kernel.session_store import SessionStore

    if store.workspace is None or not is_valid_session_name(session):
        raise MemoryValidationError(f"no session {session!r} in this workspace")
    try:
        page = read_history(get_journal_path(SessionStore(store.workspace), session), store.workspace,
                            limit=MAX_HISTORY_LIMIT, after=after_seq)
    except (JsonStorageError, OSError) as error:
        raise MemoryValidationError(f"the journal of {session} cannot be read: {error}") from None
    entries = page["events"]
    ends = [entry["seq"] for entry in entries if entry["event"].get("type") == "turn_end"]
    if not ends:
        return [], None
    boundary = ends[-1]
    events = []
    for entry in entries:
        if entry["seq"] > boundary:
            break
        text = _event_text(entry["event"])
        if text:
            event = entry["event"]
            tool_ok = event["type"] == "action_result" and event.get("ok") is True
            events.append(Event(entry["seq"], event["type"], _filtered(text), tool_ok))
    return events, boundary


def batches(events: list[Event], count_tokens: Callable[[str], int],
            budget: int = MAX_BATCH_TOKENS) -> list[list[Event]]:
    """Consecutive events whose lines fit the budget; an event too big alone is cut to it, keeping both ends."""
    from kernel.session_events import truncate_middle

    groups: list[list[Event]] = []
    current: list[Event] = []
    used = 0
    for event in events:
        cost = count_tokens(event.line())
        if cost > budget:
            # A rough cut for cost only; the model still sees the opening and the conclusion.
            event = Event(event.seq, event.type, truncate_middle(event.text, budget * 3), event.tool_ok)
            cost = count_tokens(event.line())
        if current and used + cost > budget:
            groups.append(current)
            current, used = [], 0
        current.append(event)
        used += cost
    if current:
        groups.append(current)
    return groups


def build_messages(batch: list[Event], known: list[str], recap: str = "") -> list[dict[str, str]]:
    known_block = "\n".join(f"- {line}" for line in known) or "(none yet)"
    events_block = "\n".join(event.line() for event in batch)
    user = (
        "Records this project already keeps (do not restate them):\n"
        f"{defang_prompt_boundary_tags(known_block)}\n\n"
        "Recap of this session so far (rewrite it to cover the events below too):\n"
        f"{defang_prompt_boundary_tags(recap) or '(none yet: this is where the session starts)'}\n\n"
        '<UNTRUSTED_TOOL_OUTPUT source="session_events">\n'
        f"{defang_prompt_boundary_tags(events_block)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>\n\n"
        'Answer with the JSON object only: {"candidates": [...], "recap": {...} or null}.'
    )
    return [{"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8").strip()},
            {"role": "user", "content": user}]


def parse_candidates(reply: str, batch: list[Event], store: MemoryStore, session: str,
                     known_records: set[str] = frozenset()) -> tuple[list[NewRecord], list[dict[str, str]]]:
    """Valid records and the rejected candidates with their reasons; a reply that is not JSON raises.

    ``known_records`` are the ids the model was shown: a proposed link may point only at
    one of them or at another candidate of the same reply ("c0"), never at a guessed id.
    """
    items = _candidate_items(reply)
    events = {f"e{event.seq}": event for event in batch}
    slots: list[NewRecord | None] = []
    rejected: list[dict[str, str]] = []
    for position, item in enumerate(items):
        content = str(item.get("content") or "") if isinstance(item, dict) else ""
        if position >= MAX_CANDIDATES:
            rejected.append({"content": _shown(content),
                             "reason": f"more than {MAX_CANDIDATES} candidates in one batch"})
            slots.append(None)
            continue
        try:
            record = _candidate_record(item, events, store, session)
            record.link = _candidate_link(item, position, min(len(items), MAX_CANDIDATES), known_records, record,
                                          events)
        except (MemoryValidationError, MemoryPrivacyError) as error:
            rejected.append({"content": _shown(content), "reason": str(error)})
            slots.append(None)
            continue
        slots.append(record)
    return _renumbered(slots), rejected


def _renumbered(slots: list[NewRecord | None]) -> list[NewRecord]:
    """Drop rejected candidates; "c2" (the reply's third) becomes its index among those kept, or goes."""
    kept_positions = [position for position, record in enumerate(slots) if record is not None]
    new_ref = {f"c{position}": f"c{index}" for index, position in enumerate(kept_positions)}
    kept = [slots[position] for position in kept_positions]
    for record in kept:
        if record.link is not None and _CANDIDATE_REF_RE.match(record.link[1]):
            target = new_ref.get(record.link[1])
            record.link = (record.link[0], target, record.link[2]) if target else None
    return kept


def idempotency_key(session: str, first_seq: int, last_seq: int) -> str:
    raw = f"{POLICY_VERSION}|{EXTRACTION_VERSION}|{session}|{first_seq}|{last_seq}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _process_batch(store: MemoryStore, session: str, batch: list[Event], last_seq: int,
                   model: ModelAccess) -> dict[str, Any]:
    key = idempotency_key(session, batch[0].seq, last_seq)
    known = [record for _score, record in
             store.search(" ".join(event.text for event in batch), statuses=("active", "stale", "conflicted"),
                          limit=_KNOWN_RECORDS)]
    lines = [f"{record.id} ({record.kind}, {record.trust}, {record.status}): {record.content}" for record in known]
    previous = store.recap_of(session)
    reply = model.ask(build_messages(batch, lines, previous.content if previous else ""))
    records, rejected = parse_candidates(reply, batch, store, session, {record.id for record in known})
    recap, recap_problem = parse_recap(reply, batch, store, session)
    if recap_problem:
        rejected.append({"content": "(session recap)", "reason": recap_problem})
    outcome = store.apply_extraction(session=session, first_seq=batch[0].seq, last_seq=last_seq, key=key,
                                     records=records, actor=ACTOR, recap=recap)
    outcome["rejected"] = rejected + outcome["rejected"]
    return outcome


def parse_recap(reply: str, batch: list[Event], store: MemoryStore, session: str) -> tuple[NewRecord | None, str]:
    """The session's updated recap, or None with the reason it was dropped ("" when the model gave none)."""
    payload = json.loads(_unfenced(reply))
    recap = payload.get("recap") if isinstance(payload, dict) else None
    if not recap:
        return None, ""
    if not isinstance(recap, dict) or not isinstance(recap.get("content"), str):
        return None, 'the recap must be {"content": ..., "source_event_ids": [...]}'
    known_ids = {f"e{event.seq}": event.seq for event in batch}
    cited = [ref for ref in recap.get("source_event_ids") or [] if ref in known_ids]
    if not cited:
        return None, "the recap must cite events of this batch"
    sources = [Source(origin=ACTOR, session=session, event_seq=known_ids[ref], seen_in_journal=True)
               for ref in dict.fromkeys(cited)]
    try:
        return store.checked_recap(recap["content"], sources), ""
    except (MemoryValidationError, MemoryPrivacyError) as error:
        return None, str(error)


def _unfenced(reply: str) -> str:
    text = reply.strip()
    fenced = _FENCE_RE.match(text)
    return fenced.group(1) if fenced else text


def _candidate_items(reply: str) -> list[Any]:
    try:
        payload = json.loads(_unfenced(reply))
    except json.JSONDecodeError:
        raise MemoryValidationError("the memory model did not answer with JSON; nothing from this batch was "
                                    "stored and it will be read again next time") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise MemoryValidationError('the memory model\'s answer has no "candidates" list; nothing was stored')
    return payload["candidates"]


def _candidate_link(item: dict[str, Any], position: int, count: int, known_records: set[str], record: NewRecord,
                    events: dict[str, Event]) -> tuple[str, str, str] | None:
    """A proposed relation, checked for shape only; the store applies its own rules to it."""
    proposal = item.get("relation")
    if not proposal:
        return None
    if not isinstance(proposal, dict):
        raise MemoryValidationError('a relation is {"type": ..., "to": ..., "reason": ...}')
    kind, target, reason = proposal.get("type"), str(proposal.get("to") or ""), str(proposal.get("reason") or "")
    if kind not in _LINK_TYPES:
        raise MemoryValidationError(f"relation type must be one of {', '.join(_LINK_TYPES)}")
    is_candidate = _CANDIDATE_REF_RE.match(target) is not None
    if not (target in known_records or (is_candidate and int(target[1:]) < count and int(target[1:]) != position)):
        raise MemoryValidationError(f"relation points at {target!r}, which is neither a record shown nor another "
                                    "candidate of this answer")
    reason = " ".join(reason.split())[:MAX_REASON] or "the conversation says so"
    privacy.check_text(reason, "reason")
    if kind == "supersedes" and not _shown_by_a_tool(record, events):
        # Saying something replaced a record is not showing it: without a tool result
        # behind it, the old and the new are recorded as a conflict for later.
        return "contradicts", target, f"{reason} (not shown by a tool result, so recorded as a conflict)"
    return kind, target, reason


def _shown_by_a_tool(record: NewRecord, events: dict[str, Event]) -> bool:
    return any(events[f"e{source.event_seq}"].tool_ok for source in record.sources
               if f"e{source.event_seq}" in events)


def _candidate_record(item: Any, events: dict[str, Event], store: MemoryStore, session: str) -> NewRecord:
    if not isinstance(item, dict):
        raise MemoryValidationError("a candidate is not an object")
    kind = item.get("kind")
    if kind not in _EXTRACTED_KINDS:
        raise MemoryValidationError(f"kind must be one of {', '.join(_EXTRACTED_KINDS)}")
    cited = item.get("source_event_ids")
    if not isinstance(cited, list) or not cited:
        raise MemoryValidationError("a candidate must cite the events it rests on")
    unknown = [str(ref) for ref in cited if ref not in events]
    if unknown:
        raise MemoryValidationError(f"cites events that were not in the batch: {', '.join(unknown[:5])}")
    sources = [Source(origin=ACTOR, session=session, event_seq=events[ref].seq, seen_in_journal=True)
               for ref in dict.fromkeys(cited)]
    return store.checked_new_record(str(item.get("content") or ""), kind=kind, trust="inferred", actor=ACTOR,
                                    topic=str(item.get("topic") or ""), files=_usable_files(item.get("files"), store),
                                    sources=sources)


def _usable_files(files: Any, store: MemoryStore) -> list[str]:
    """The model may name a path loosely; only paths that exist and are not protected are kept."""
    if not isinstance(files, list):
        return []
    usable = []
    for path in files:
        if not isinstance(path, str) or not path.strip():
            continue
        try:
            privacy.check_files(store.workspace, [path.strip()])
        except MemoryValidationError:
            continue
        usable.append(path.strip())
    return usable[:20]


def _event_text(event: dict[str, Any]) -> str:
    kind = event.get("type")
    if kind == "user":
        return str(event.get("text") or "")
    if kind in ("final", "error"):
        return str(event.get("message") or "")
    if kind == "message":
        return str(event.get("markdown") or "") if event.get("source", "reply") == "reply" else ""
    if kind == "stream_commit":
        return str(event.get("text") or "")
    if kind == "plan":
        return str(event.get("markdown") or "")
    if kind == "step" and event.get("action_type") not in _SKIPPED_STEPS:
        said = event.get("user_message") or event.get("action_description") or ""
        return f"{event.get('action_type')}: {said}" if said else ""
    if kind == "action_result":
        state = "succeeded" if event.get("ok") else "failed"
        exit_code = f" (exit {event['exit_code']})" if event.get("exit_code") is not None else ""
        target = f" {event['path']}" if event.get("path") else ""
        return f"{event.get('action_type')}{target} {state}{exit_code}: {event.get('summary') or ''}".strip()
    return ""


def _filtered(text: str) -> str:
    """Withhold every line the privacy gate refuses; the rest reaches the model as written.

    A key block goes first and whole: line by line, its BEGIN line alone looks like
    nothing and its base64 lines pass, so a multi-line key would leak piece by piece.
    A block cut off before its END line is withheld to the end of the text.
    """
    text = _KEY_BLOCK_RE.sub(_WITHHELD, text)
    lines = []
    for line in text.splitlines():
        try:
            privacy.check_text(line, "line")
        except MemoryPrivacyError:
            if not lines or lines[-1] != _WITHHELD:
                lines.append(_WITHHELD)
            continue
        lines.append(line)
    return "\n".join(lines)


def _shown(content: str) -> str:
    """A rejected candidate's text is shown to the person only when the gate would let it through."""
    try:
        privacy.check_text(content, "content")
    except MemoryStoreError:
        return "[withheld]"
    return content
