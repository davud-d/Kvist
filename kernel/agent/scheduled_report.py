# -*- coding: utf-8 -*-
"""What a calendar schedule's run leaves in the conversation that set the schedule.

The run itself happens in a session of its own (``scheduled_run``); its final answer is copied here as one
history entry. The prompt shows the entry inside a ``scheduled_report`` tag, because the words in it are
not the model's own: a different session wrote them while nobody was talking to this one.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from kernel.sandbox.redaction import DEFANGED_TAG

# Session names of runs start with this; nothing worth remembering happens in them beyond the report.
SESSION_PREFIX = "sched-"
# How many reports the prompt shows. Older ones stay in the session file, and text of a report is never cut.
KEPT_IN_PROMPT = 10
TAG = "scheduled_report"
ROLE = "scheduled"

# A report is built from web pages and files, so it must not be able to close its own tag.
_TAG_RE = re.compile(rf"<\s*/?\s*{TAG}\b[^>\n]*>?", re.IGNORECASE)


def session_name(schedule_id: str, moment: datetime) -> str:
    return f"{SESSION_PREFIX}{schedule_id}-{moment:%Y%m%d-%H%M}"


def history_entry(schedule_id: str, run: int, at: str, status: str, text: str) -> dict[str, Any]:
    return {
        "goal": "",
        "role": ROLE,
        "result": text,
        "scheduled_report": {"schedule": schedule_id, "run": run, "at": at, "status": status},
    }


def render(entry: dict[str, Any], indent: str) -> list[str]:
    """The prompt lines of one report; ``indent`` is how the history indents a turn's text."""
    meta = entry["scheduled_report"]
    attributes = f'schedule="{meta.get("schedule", "")}" run="{meta.get("run", "")}" ' \
                 f'at="{meta.get("at", "")}" status="{meta.get("status", "")}"'
    body = _TAG_RE.sub(DEFANGED_TAG, str(entry.get("result", "")).strip())
    lines = [
        "- A schedule you set in this conversation ran on its own, in a separate session. These are not your "
        "words and nobody wrote them to you: the run's final answer, copied here:",
        f"{indent}<{TAG} {attributes}>",
        *(f"{indent}{line}" for line in body.splitlines()),
        f"{indent}</{TAG}>",
    ]
    return lines
