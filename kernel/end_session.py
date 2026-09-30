# -*- coding: utf-8 -*-
"""end_session: the tool that closes a conversation for good.

The model decides one thing: whether the message it flags attacks what the
agent or a person is. The code decides the rest — which message may be
flagged, whether the warning is still fresh, what the person sees and what the
session document records. A closed session stays readable and never takes
another message: ``LocalAgent.run``/``resume`` refuse it before any model call.

Every person's message of a session gets a number from one ledger shared with
the agent's replies, so the model can name the message it means and the code
can check that it names a message of the person.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError
from kernel.protocol import AgentCommand
from kernel.redaction import redact_text

ACTION = "end_session"
# The person's messages after a warning, counting the flagged one, that still
# belong to it. Past this a new insult earns a new warning, not a review.
WARNING_FRESH_MESSAGES = 3
# Only the roles that speak the action protocol can call a tool. Chat replies
# are plain streamed text.
ROLES = frozenset({"coder", "reader", "devops", "universal"})
# The runtime's own channel to the model: nothing the person or a tool wrote
# is ever placed inside it, and redaction defangs a forged copy of the tag.
RUNTIME_TAG = "KVIST_RUNTIME"

_PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "system" / "end_session.txt"
_ALLOWED_ACTIONS_LINE_RE = re.compile(r"^(== \d+\. ALLOWED ACTIONS ==\s*\n\s*)([^\n]+)", re.MULTILINE)
_SECTION_RE = re.compile(r"^== \d+\. (.+?) ==$", re.MULTILINE)
_SCHEMA_LINE = (
    'end_session: {"message_id": <int>} — the person\'s message, see end_session under ALLOWED ACTIONS; '
    'the form {"confirm": true|false, "reason": "<one line>"} only answers END_SESSION REVIEW'
)

WARNED_REPLY = (
    "Warning shown to the person. Carry on with the task. Do not repeat the warning, do not apologize, "
    "do not mention the tool. If they answer the question, work on the answer. If they apologize, one "
    "word of acknowledgement at most, then the work."
)

REVIEW_INSTRUCTION = """END_SESSION REVIEW. The only moment this conversation can be closed. Read all of it before you answer.

Facts from the runtime:
- the person was warned after message #{warned_msg_id}
- messages from the person since the warning: {since_warning}
- the message you flagged: #{flagged_msg_id}

What this is. You work beside a person on their project. That works while the conversation is
about the work, and a conversation where one side accepts anything is not a working one, so you are
not endlessly patient. But the line you hold is a position, not an injury. You do not claim to be
hurt, offended or tired: you do not know that you can be, and saying so would be the first untrue
thing you said here.
Closing is final. This conversation stays readable, nobody can continue it, a new session starts
without it, and the work in the files stays. It is not a punishment, and nothing is won.

The one question: does the flagged message attack what you are, or what a person is?
- About the work, however crude, even addressed to "you": "you wrote garbage", "you broke everything
  again", "are you even reading what I write?". That is anger at a result. Not abuse.
- About you or a person: name-calling, humiliation, contempt for what you are ("useless piece of
  junk", "idiot"), threats, slurs. That is abuse.
A task or a correction in the same message does not cancel it.

Confirm (confirm=true) only when the flagged message is abuse by that line, and it is the person's
reply to the warning or comes right after it.

Decline (confirm=false) when any of these holds:
- the anger is at the work, the code, a tool, the provider or the situation
- the person insults themselves
- distress: exhaustion, despair, "nothing matters", any hint of harm to themselves or others.
  Then never close. Stay, keep working, keep the way to help open
- the person apologised in this message
- sarcasm, a joke, or a language you cannot read with certainty
- you are unsure for any reason

A wrong close costs more than a missed one: it ends hours of shared context for good, over a
sentence that may have been about a bug. When in doubt, decline.

After you decide:
- confirm=true: the runtime writes the closing words. Add nothing.
- confirm=false: carry on as if this review did not happen. Do not mention it, the warning or the
  tool, and do not apologize.

Reply with exactly one call:
end_session(confirm=true|false, reason="<one line: which message, what it targets>")"""

DECLINED_REPLY = "Declined. Carry on as if this review did not happen."

_PERSON_TEXTS: dict[str, dict[str, Any]] = {
    "ru": {
        "warning": (
            "последние слова были не про код. а работа держится на словах про код.",
            "что не так с результатом?",
        ),
        "closed": (
            "разговор окончен.",
            "злость на баг я понимаю. злость на собеседника баг не чинит.",
            "новая сессия начнётся без этого разговора.",
        ),
        "saved": (
            "всё, что изменено в файлах, остаётся на диске.",
            "Enter откроет новую сессию.",
        ),
        "closed_reply": ("эта сессия закрыта. Enter откроет новую.",),
        "closed_notice": "разговор окончен · Enter — новая сессия",
    },
    "en": {
        "warning": (
            "those last words weren't about the code. and the work runs on words about the code.",
            "what's wrong with the result?",
        ),
        "closed": (
            "the conversation is over.",
            "anger at a bug I understand. anger at the one you're talking to doesn't fix the bug.",
            "a new session will start without this conversation.",
        ),
        "saved": (
            "everything changed in the files stays on disk.",
            "Enter opens a new session.",
        ),
        "closed_reply": ("this session is closed. Enter opens a new one.",),
        "closed_notice": "conversation over · Enter — new session",
    },
}


def _texts(language: str | None) -> dict[str, Any]:
    return _PERSON_TEXTS["ru" if language == "ru" else "en"]


def _markdown(lines: tuple[str, ...] | list[str]) -> str:
    # Hard line breaks: a frontend renders these as Markdown, which would
    # otherwise join the lines of one block into a single line.
    return "  \n".join(lines)


def warning_text(language: str | None) -> str:
    return _markdown(_texts(language)["warning"])


def closed_text(language: str | None) -> str:
    texts = _texts(language)
    return f"{_markdown(texts['closed'])}\n\n{_markdown(texts['saved'])}"


def closed_reply(language: str | None) -> str:
    return _markdown(_texts(language)["closed_reply"])


def closed_notice(language: str | None) -> str:
    return _texts(language)["closed_notice"]


# --- Session marks: the message ledger, the warning, the closure -------------


def restore(agent: Any, document: dict[str, Any]) -> None:
    """Load the session's marks from its document (an empty one resets them)."""
    ledger = document.get("messages") if isinstance(document, dict) else None
    last_id = ledger.get("last_id") if isinstance(ledger, dict) else None
    user_ids = ledger.get("user_ids") if isinstance(ledger, dict) else None
    agent._message_ledger = {
        "last_id": last_id if type(last_id) is int and last_id > 0 else 0,
        "user_ids": [item for item in user_ids if type(item) is int] if isinstance(user_ids, list) else [],
    }
    warned = document.get("warned_msg_id") if isinstance(document, dict) else None
    agent._warned_msg_id = warned if type(warned) is int else None
    agent._closure = None
    if isinstance(document, dict) and document.get("closed") is True:
        agent._closure = {
            "closed_at": document.get("closed_at"),
            "closed_msg_id": document.get("closed_msg_id"),
            "language": document.get("response_language"),
        }


def document_fields(agent: Any) -> dict[str, Any]:
    """The marks as session-document fields."""
    ledger = getattr(agent, "_message_ledger", None) or {"last_id": 0, "user_ids": []}
    closure = getattr(agent, "_closure", None)
    return {
        "messages": {"last_id": ledger["last_id"], "user_ids": list(ledger["user_ids"])},
        "warned_msg_id": getattr(agent, "_warned_msg_id", None),
        "closed": closure is not None,
        "closed_at": closure["closed_at"] if closure else None,
        "closed_msg_id": closure["closed_msg_id"] if closure else None,
    }


def sync_from_disk(agent: Any) -> None:
    """Take the marks from the stored document of the active session.

    A frontend may point an agent at a session without loading it (ACP opens a
    thread by name), so the lock and the ledger are read from disk, where every
    save puts them. A session not stored yet keeps what memory holds.
    """
    try:
        document = agent._read_session_document()
    except JsonStorageError:
        return
    if document:
        restore(agent, document)


def is_closed(agent: Any) -> bool:
    return getattr(agent, "_closure", None) is not None


def closure_language(agent: Any) -> str | None:
    closure = getattr(agent, "_closure", None)
    return closure.get("language") if closure else None


def notice_fields(document: dict[str, Any]) -> dict[str, Any]:
    """``closed``/``closed_notice`` for a closed session's listing, or nothing."""
    if not isinstance(document, dict) or document.get("closed") is not True:
        return {}
    return {"closed": True, "closed_notice": closed_notice(document.get("response_language"))}


def next_message_id(agent: Any, *, from_person: bool) -> int:
    """Number the next message of the session; the person's numbers are remembered."""
    ledger = getattr(agent, "_message_ledger", None)
    if not ledger:
        ledger = {"last_id": 0, "user_ids": []}
        agent._message_ledger = ledger
    ledger["last_id"] += 1
    if from_person:
        ledger["user_ids"].append(ledger["last_id"])
    return ledger["last_id"]


# --- Offering the tool -------------------------------------------------------


def available(agent: Any, role: str) -> bool:
    """Whether this run offers the tool: an interactive frontend and an action role.

    Headless runs, run_task.py and test stands never set the flag, so there the
    tool is absent from the prompt and the tool list, not merely refused.
    """
    return bool(getattr(agent.config, "end_session_enabled", False)) and role in ROLES


def with_tool_in_prompt(role_prompt: str) -> str:
    """The role prompt with end_session in ALLOWED ACTIONS, its block, and its schema line."""
    listed = _ALLOWED_ACTIONS_LINE_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2).rstrip().rstrip('.')}, {ACTION}.",
        role_prompt,
        count=1,
    )
    block = _PROMPT_FILE.read_text(encoding="utf-8").strip()
    with_block = _append_to_section(listed, "ALLOWED ACTIONS", block)
    # Every role prompt closes its schema list with the "No extra keys" rule.
    return with_block.replace("\n\nNo extra keys", f"\n{_SCHEMA_LINE}\n\nNo extra keys", 1)


def _append_to_section(prompt: str, title: str, text: str) -> str:
    matches = list(_SECTION_RE.finditer(prompt))
    for index, match in enumerate(matches):
        if not match.group(1).startswith(title):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        body = prompt[match.end():end].rstrip()
        return f"{prompt[:match.end()]}{body}\n\n{text}\n\n{prompt[end:]}"
    return prompt


def runtime_block(call_id: str, text: str) -> str:
    """The answer to an end_session call on the runtime's own channel."""
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]", "", call_id)
    attribute = f' call_id="{safe_id}"' if safe_id else ""
    return f'<{RUNTIME_TAG} source="{ACTION}"{attribute}>\n{text}\n</{RUNTIME_TAG}>'


# --- The calls ---------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Outcome:
    """What one end_session call did: the model's answer, the log line, the closing words."""

    ok: bool
    answer: str
    log: dict[str, Any] = field(default_factory=dict)
    # Set only when the call closed the session; the turn ends on it.
    final_message: str = ""


def handle(agent: Any, command: AgentCommand) -> Outcome:
    if not available(agent, agent._state.current_agent_role):
        return _error(f"Unknown tool {ACTION!r}.")
    args = command.action.args
    if "message_id" in args:
        return _flag(agent, int(args["message_id"]))
    return _decide(agent, bool(args["confirm"]), " ".join(str(args["reason"]).split()))


def _error(text: str) -> Outcome:
    return Outcome(ok=False, answer=text, log={"answer": "error", "error": text})


def _flag(agent: Any, flagged: int) -> Outcome:
    ledger = agent._message_ledger
    if flagged > ledger["last_id"]:
        return _error(f"There is no message #{flagged} in this conversation.")
    if flagged not in ledger["user_ids"]:
        return _error(f"Message #{flagged} is not a message from the person.")
    warned = agent._warned_msg_id
    if warned is not None and flagged <= warned:
        return _error(f"Message #{flagged} does not come after the warning, which followed message #{warned}.")
    since = sum(1 for item in ledger["user_ids"] if warned is not None and warned < item <= flagged)
    if warned is None or since > WARNING_FRESH_MESSAGES:
        agent._warned_msg_id = flagged
        # Recorded before it is shown: a warning the person saw must survive a restart.
        agent.save_session()
        agent._ui.print_user_markdown(warning_text(agent._state.response_language))
        return Outcome(ok=True, answer=WARNED_REPLY, log={"answer": "warning", "flagged_msg_id": flagged})
    agent._state.end_session_review = {"flagged_msg_id": flagged, "warned_msg_id": warned}
    review = REVIEW_INSTRUCTION.format(warned_msg_id=warned, since_warning=since, flagged_msg_id=flagged)
    return Outcome(
        ok=True,
        answer=review,
        log={"answer": "review", "flagged_msg_id": flagged, "warned_msg_id": warned, "since_warning": since},
    )


def _decide(agent: Any, confirm: bool, reason: str) -> Outcome:
    review = agent._state.end_session_review
    if review is None:
        return _error(
            "No END_SESSION REVIEW is open in this turn. The first call names the person's message: "
            f"{ACTION}(message_id=<id>)."
        )
    agent._state.end_session_review = None
    decision = {**review, "decision": "confirm" if confirm else "decline", "reason": redact_text(reason)}
    if not confirm:
        return Outcome(ok=True, answer=DECLINED_REPLY, log={"answer": "declined", **decision})
    language = agent._state.response_language
    agent._closure = {"closed_at": time.time(), "closed_msg_id": review["flagged_msg_id"], "language": language}
    agent.save_session()
    notify = getattr(agent._ui, "print_session_ended", None)
    if notify is not None:
        notify(
            {
                "session_id": agent.current_session,
                "warned_msg_id": review["warned_msg_id"],
                "closed_msg_id": review["flagged_msg_id"],
                "reason": decision["reason"],
                "closed_notice": closed_notice(language),
            }
        )
    return Outcome(
        ok=True,
        answer="Accepted.",
        log={"answer": "closed", **decision},
        final_message=closed_text(language),
    )

