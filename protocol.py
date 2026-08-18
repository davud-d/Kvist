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


@dataclass(slots=True)
class Action:
    type: ActionType
    args: dict[str, Any]


@dataclass(slots=True)
class AgentCommand:
    thought: str
    plan_step: str
    progress: int
    user_message: str
    action: Action
    step_summary: str = ""
