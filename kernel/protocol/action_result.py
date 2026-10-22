# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any

from kernel.sandbox.redaction import redact_text

MAX_OUTPUT_CHARS = 4000
MAX_SUMMARY_CHARS = 120

_TRUNCATION_MARKER = "\n[... {omitted} characters omitted ...]\n"
# The runtime's answer to an action it did not run because an earlier action of
# the same response failed (agent_runtime._SKIPPED_AFTER_FAILED_WRITE).
SKIPPED_ERROR = "Skipped: an earlier action in this response failed."
SKIPPED_SUMMARY = "Skipped: an earlier action failed"


def feedback_skipped(error: str) -> bool:
    return error == SKIPPED_ERROR


def build_action_result(
    step: int,
    action_type: str,
    args: dict[str, Any],
    feedback: dict[str, Any],
    *,
    call_id: str = "",
    batch_id: str = "",
) -> dict[str, Any]:
    """Describe one executed action for a frontend, from real executor data.

    ``feedback`` is what ``execute_command`` produced (``ok`` + ``data`` or
    ``error``). Summary and output come from the fields the executor actually
    returns for each action type; nothing is inferred from the model's words.
    """
    ok = bool(feedback.get("ok"))
    data = feedback.get("data")
    data = data if isinstance(data, dict) else {}
    error = str(feedback.get("error") or "")

    return {
        "step": step,
        "action_type": action_type,
        "ok": ok,
        "summary": _summary(action_type, args, data, ok, error),
        "output": _output(action_type, data, ok, error),
        "exit_code": _exit_code(action_type, data),
        "path": _path(action_type, args, data),
        "first_line": _first_line(action_type, data, ok),
        "lines_added": _line_count(action_type, data, ok, "lines_added"),
        "lines_removed": _line_count(action_type, data, ok, "lines_removed"),
        "patch": _patch(action_type, feedback, ok),
        "call_id": call_id,
        "batch_id": batch_id,
    }


def _plural(count: int, noun: str, many: str = "") -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {many or noun + 's'}"


def _summary(
    action_type: str, args: dict[str, Any], data: dict[str, Any], ok: bool, error: str
) -> str:
    if feedback_skipped(error):
        return SKIPPED_SUMMARY
    if action_type == "run_shell":
        return _shell_summary(data, error)
    if action_type in {"job_output", "job_stop"} and ok:
        return _one_line(f"{data.get('job_id', 'job')}: {data.get('state', '')}")
    if not ok:
        return _one_line(f"Failed: {error or 'action failed'}")
    return _one_line(_ok_summary(action_type, args, data))


def _ok_summary(action_type: str, args: dict[str, Any], data: dict[str, Any]) -> str:
    if action_type == "read_file":
        suffix = " (truncated)" if data.get("truncated") else ""
        if "total_lines" in data:
            return f"Read lines {data['start_line']}-{data['end_line']} of {data['total_lines']}{suffix}"
        return f"Read {_plural(len(str(data.get('content', '')).splitlines()), 'line')}{suffix}"
    if action_type == "read_multiple_files":
        items = data.get("files", [])
        missing = sum(1 for item in items if "error" in item)
        note = f", {missing} not found" if missing else ""
        return f"Read {_plural(len(items) - missing, 'file')}{note}"
    if action_type == "write_file":
        return f"Wrote {_plural(len(str(args.get('content', '')).splitlines()), 'line')}"
    if action_type == "append_file":
        return f"Appended {_plural(len(str(args.get('content', '')).splitlines()), 'line')}"
    if action_type == "replace_in_file":
        return f"Replaced {_plural(int(data.get('occurrences_replaced', 0)), 'occurrence')}"
    if action_type == "list_dir":
        return _plural(len(data.get("items", [])), "entry", "entries")
    if action_type == "list_tree":
        suffix = " (truncated)" if data.get("truncated") else ""
        return f"Listed {_plural(len(str(data.get('tree', '')).splitlines()), 'line')}{suffix}"
    if action_type == "find_files":
        suffix = " (truncated)" if data.get("truncated") else ""
        return _plural(len(data.get("matches", [])), "match", "matches") + suffix
    if action_type == "search_code":
        suffix = " (truncated)" if data.get("truncated") else ""
        return _plural(len(data.get("results", [])), "match", "matches") + suffix
    if action_type == "git_status":
        return _git_status_summary(data)
    if action_type == "git_diff":
        return _git_diff_summary(data)
    if action_type == "web_search":
        return _plural(len(data.get("results", [])), "result")
    if action_type == "fetch_url":
        suffix = " (truncated)" if data.get("truncated") else ""
        return f"Fetched {_plural(len(str(data.get('content', ''))), 'character')}{suffix}"
    if action_type == "delegate_task":
        return _delegation_summary(data)
    if action_type == "skill_call":
        return f"{data.get('skill', 'skill')}.{data.get('tool', '')}: {data.get('status', 'done')} in {data.get('duration_seconds', 0)} s"
    if action_type == "ask_user":
        return f"Answer: {data.get('status', '')}"
    if action_type == "wait" and data.get("wakes_at"):
        return f"Wakes at {data['wakes_at']}"
    if action_type == "schedule":
        return _schedule_summary(data)
    if action_type == "skills" and data.get("attached") and not isinstance(data.get("attached"), list):
        return f"Attached {data['attached']} {data.get('version', '')}".strip()
    if action_type == "skills" and data.get("detached"):
        return f"Detached {data['detached']}"
    if action_type == "helper" and data.get("agent_id"):
        return f"{data['agent_id']}: {data.get('state', '')}".strip()
    return "Done"


def _schedule_summary(data: dict[str, Any]) -> str:
    if data.get("cancelled"):
        return f"Cancelled {data['cancelled']}"
    if "schedules" in data:
        return _plural(len(data["schedules"]), "schedule")
    entry = data.get("scheduled") or data.get("updated") or {}
    return f"{entry.get('id', 'Schedule')}: next {entry.get('next_run', '')} ({entry.get('repeat', '')})".strip()


def _shell_summary(data: dict[str, Any], error: str) -> str:
    if data.get("denied"):
        return "Command denied"
    if data.get("job_id"):
        return f"Started background job {data['job_id']}"
    if data.get("timed_out"):
        return "Command timed out"
    code = data.get("returncode")
    if code is None:
        return _one_line(f"Command failed: {error}" if error else "Command failed")
    return f"Command exited with code {code}"


def _git_status_summary(data: dict[str, Any]) -> str:
    if not data.get("git_available"):
        return "Not a git repository"
    lines = [line for line in str(data.get("stdout", "")).splitlines() if line.strip()]
    changed = max(0, len(lines) - 1)
    return "Working tree clean" if changed == 0 else _plural(changed, "changed file")


def _git_diff_summary(data: dict[str, Any]) -> str:
    stdout = str(data.get("stdout", ""))
    if not stdout.strip():
        return "No changes"
    files = sum(1 for line in stdout.splitlines() if line.startswith("diff --git"))
    if files == 0:
        return "Diff available"
    return _plural(files, "file") + " in diff"


def _delegation_summary(data: dict[str, Any]) -> str:
    results = data.get("results", [])
    if not results:
        return "Handoff recorded, nothing run"
    succeeded = sum(1 for item in results if item.get("ok"))
    return f"{succeeded} of {len(results)} commands succeeded"


def _output(action_type: str, data: dict[str, Any], ok: bool, error: str) -> str:
    text = _raw_output(action_type, data, ok, error)
    return _truncate(redact_text(text))


def _raw_output(action_type: str, data: dict[str, Any], ok: bool, error: str) -> str:
    if action_type in {"run_shell", "git_status", "git_diff"}:
        streams = _join_streams(data.get("stdout"), data.get("stderr"))
        return streams or ("" if ok else error)
    if action_type == "delegate_task":
        return _delegation_output(data) or ("" if ok else error)
    if action_type in {"job_output", "job_stop"} and ok:
        return str(data.get("output") or "")
    if action_type == "skill_call" and ok:
        return json.dumps(data.get("result"), ensure_ascii=False, indent=1)[:4000]
    return "" if ok else error


def _join_streams(stdout: Any, stderr: Any) -> str:
    parts = [str(part).rstrip() for part in (stdout, stderr) if part]
    return "\n".join(part for part in parts if part)


def _delegation_output(data: dict[str, Any]) -> str:
    blocks = []
    for item in data.get("results", []):
        head = f"$ {item.get('command', '')}"
        body = _join_streams(item.get("stdout"), item.get("stderr")) or str(
            item.get("error") or ""
        )
        blocks.append(f"{head}\n{body}" if body else head)
    return "\n".join(blocks)


def _exit_code(action_type: str, data: dict[str, Any]) -> int | None:
    if action_type != "run_shell":
        return None
    code = data.get("returncode")
    return code if isinstance(code, int) else None


_FILE_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}


def _path(action_type: str, args: dict[str, Any], data: dict[str, Any]) -> str | None:
    if action_type not in _FILE_WRITE_ACTIONS:
        return None
    path = data.get("path") or args.get("path")
    return redact_text(str(path)) if path else None


def _first_line(action_type: str, data: dict[str, Any], ok: bool) -> int | None:
    if action_type not in _FILE_WRITE_ACTIONS or not ok:
        return None
    line = data.get("first_line")
    return line if isinstance(line, int) and line >= 1 else None


def _line_count(action_type: str, data: dict[str, Any], ok: bool, key: str) -> int | None:
    if action_type not in _FILE_WRITE_ACTIONS or not ok:
        return None
    count = data.get(key)
    return count if isinstance(count, int) and count >= 0 else None


def _patch(action_type: str, feedback: dict[str, Any], ok: bool) -> str | None:
    if action_type not in _FILE_WRITE_ACTIONS or not ok:
        return None
    patch = feedback.get("patch")
    return patch if isinstance(patch, str) and patch else None


def _one_line(text: str) -> str:
    collapsed = " ".join(redact_text(text).split())
    if len(collapsed) <= MAX_SUMMARY_CHARS:
        return collapsed
    return collapsed[: MAX_SUMMARY_CHARS - 1] + "…"


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    # Keep both ends: the command line is at the top, but the failure that
    # matters in a long build or test log is almost always at the bottom.
    half = MAX_OUTPUT_CHARS // 2
    omitted = len(text) - 2 * half
    return text[:half] + _TRUNCATION_MARKER.format(omitted=omitted) + text[-half:]
