# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ActionType = Literal[
    "final_answer",
    "refuse",
    "read_file",
    "read_multiple_files",
    "write_file",
    "append_file",
    "replace_in_file",
    "list_dir",
    "list_tree",
    "find_files",
    "search_code",
    "git_status",
    "git_diff",
    "run_shell",
    "web_search",
    "fetch_url",
    "delegate_task",
    "report_to_user",
]

# run_shell timeout bounds shared by the parser and the terminal. The widest
# per-profile ceiling is the dev profile's; the terminal clamps again to the
# profile actually active, which the model cannot know.
MIN_SHELL_TIMEOUT_SECONDS = 1.0
MAX_SHELL_TIMEOUT_SECONDS = 300.0


@dataclass(slots=True)
class Action:
    type: ActionType
    args: dict[str, Any]
    # Short user-facing label for the banner's Action row; the model's
    # user_message is shared by a whole batch, so it cannot name one action.
    description: str = ""


@dataclass(slots=True)
class AgentCommand:
    thought: str
    plan_step: str
    user_message: str
    action: Action
    step_summary: str = ""


@dataclass(slots=True)
class ToolCallRequest:
    """One call as the model wrote it, before validation.

    ``error`` is set when the call could not be read at all (broken JSON, no
    name); such a call is answered with the error instead of running.
    """

    call_id: str
    name: str
    arguments: dict[str, Any] | None
    error: str = ""


@dataclass(slots=True)
class ModelResponse:
    """A response of the v2 contract: words to the person and zero or more calls."""

    text: str
    calls: list[ToolCallRequest]
