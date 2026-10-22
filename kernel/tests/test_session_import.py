# -*- coding: utf-8 -*-
"""``/export``: reading other agents' sessions and writing them as Kvist sessions.

Fixtures copy the record layouts seen in real session files of each agent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.session_import import (
    SOURCES,
    aider,
    claude_code,
    codex,
    continue_dev,
    cursor,
    here,
    opencode,
    qwen_code,
)
from kernel.session_import.model import ImportedSession, ImportedTurn, SessionSummary
from kernel.session_import.wizard import run_export, setup_import, setup_listing
from kernel.session_import.writer import HISTORY_TURNS, import_session, session_name
from kernel.session.session_store import run_session_op


def _jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path.resolve()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: path))
    for variable in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "CONTINUE_GLOBAL_DIR"):
        monkeypatch.delenv(variable, raising=False)
    return path


def _agent(workspace: Path):
    return SimpleNamespace(executor=SimpleNamespace(workspace=workspace))


# --- Claude Code ---------------------------------------------------------------


def _claude_session(home: Path, cwd: str) -> Path:
    path = home / ".claude" / "projects" / "-proj" / "abc-123.jsonl"
    user = lambda text, **extra: {"type": "user", "cwd": cwd, "timestamp": "2026-10-01T10:00:00Z", "message": {"role": "user", "content": text}, **extra}  # noqa: E731
    assistant = lambda *blocks, **extra: {"type": "assistant", "message": {"role": "assistant", "content": list(blocks)}, **extra}  # noqa: E731
    _jsonl(path, [
        {"type": "mode", "mode": "normal"},
        user("fix the parser"),
        assistant({"type": "thinking", "thinking": ""}, {"type": "text", "text": "Looking."},
                  {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "/p/parser.py"}},
                  {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "pytest"}}),
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        assistant({"type": "text", "text": "Fixed the parser."}),
        user("<task-notification>bg done</task-notification>", origin={"kind": "task-notification"}),
        assistant({"type": "text", "text": "Fixed the parser, tests pass."}),
        user("<command-name>/clear</command-name>"),
        user("<system-reminder>ignored</system-reminder>side task", isMeta=True),
        user("now the lexer"),
        assistant({"type": "text", "text": "API Error: overloaded"}, isApiErrorMessage=True),
        user([{"type": "text", "text": "[Request interrupted by user]"}]),
        {"type": "ai-title", "aiTitle": "Parser work"},
    ])
    return path


def test_claude_code_reads_requests_answers_and_tool_facts(home, workspace):
    _claude_session(home, str(workspace))
    (summary,) = claude_code.find(home, workspace)
    assert (summary.title, summary.cwd, summary.id) == ("Parser work", str(workspace), "abc-123")

    imported = claude_code.load(summary)
    assert imported.summary.title == "Parser work"
    first, second = imported.turns
    assert first.user == "fix the parser"
    assert first.answer == "Fixed the parser, tests pass."
    assert (first.files_changed, first.commands, first.stopped) == (["/p/parser.py"], 1, False)
    assert (second.user, second.answer, second.stopped) == ("now the lexer", "", True)


def test_claude_code_ignores_sessions_without_a_request(home, workspace):
    _jsonl(home / ".claude" / "projects" / "-p" / "empty.jsonl", [{"type": "mode", "mode": "normal"}])
    assert claude_code.find(home, workspace) == []


# --- Codex ---------------------------------------------------------------------


def _codex_path(home: Path) -> Path:
    return home / ".codex" / "sessions" / "2026" / "10" / "03" / "rollout-2026-10-03T10-00-00-0abc.jsonl"


def test_codex_items_format(home, workspace):
    def item(payload):
        return {"timestamp": "2026-10-03T10:00:00Z", "type": "event_msg", "payload": {"type": "item_completed", "item": payload}}

    _jsonl(_codex_path(home), [
        {"type": "session_meta", "payload": {"id": "0abc", "cwd": str(workspace)}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /x"}]}},
        item({"type": "UserMessage", "content": [{"type": "text", "text": "add a flag"}]}),
        item({"type": "AgentMessage", "phase": "commentary", "content": [{"type": "Text", "text": "Checking."}]}),
        item({"type": "CommandExecution", "command": ["bash", "-lc", "ls"]}),
        item({"type": "FileChange", "changes": {"/p/cli.py": {"type": "update"}}}),
        item({"type": "AgentMessage", "phase": "final_answer", "content": [{"type": "Text", "text": "Added."}]}),
        item({"type": "UserMessage", "content": [{"type": "text", "text": "and a test"}]}),
        {"type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"}},
    ])
    (summary,) = codex.find(home, workspace)
    assert (summary.id, summary.cwd, summary.title) == ("0abc", str(workspace), "add a flag")
    first, second = codex.load(summary).turns
    assert (first.answer, first.commands, first.files_changed) == ("Added.", 1, ["/p/cli.py"])
    assert (second.user, second.stopped) == ("and a test", True)


def test_codex_uses_the_name_it_gave_the_thread_not_the_first_request(home, workspace):
    _jsonl(_codex_path(home), [
        {"type": "session_meta", "payload": {"id": "0abc", "cwd": str(workspace)}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "add a flag"}]}}},
    ])
    _jsonl(home / ".codex" / "session_index.jsonl", [
        {"id": "0abc", "thread_name": "Old name"},
        {"id": "other", "thread_name": "Not this one"},
        {"id": "0abc", "thread_name": "Add the --flag option"},
    ])
    (summary,) = codex.find(home, workspace)
    assert summary.title == "Add the --flag option"
    assert codex.load(summary).summary.title == "Add the --flag option"


def test_claude_code_title_without_ai_title_is_the_first_request(home, workspace):
    _jsonl(home / ".claude" / "projects" / "-p" / "plain.jsonl", [
        {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "just a question"}},
    ])
    (summary,) = claude_code.find(home, workspace)
    assert summary.title == "just a question"


def test_codex_older_response_item_format(home, workspace):
    patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n*** End Patch"

    def message(role, text):
        return {"type": "response_item", "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]}}

    _jsonl(_codex_path(home), [
        {"type": "session_meta", "payload": {"id": "old", "cwd": str(workspace)}},
        message("user", "<environment_context>x</environment_context>"),
        message("user", "rename it"),
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{}"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch", "input": patch}},
        message("assistant", "Renamed."),
    ])
    (summary,) = codex.find(home, workspace)
    (turn,) = codex.load(summary).turns
    assert (turn.user, turn.answer, turn.commands, turn.files_changed) == ("rename it", "Renamed.", 1, ["src/a.py"])


# --- OpenCode ------------------------------------------------------------------


def _opencode_db(home: Path, cwd: str) -> Path:
    path = home / ".local" / "share" / "opencode" / "opencode.db"
    path.parent.mkdir(parents=True)
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE session (id TEXT, parent_id TEXT, title TEXT, directory TEXT, time_updated INTEGER);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE session_v2 (id TEXT, parent_id TEXT, title TEXT, directory TEXT, time_updated INTEGER);
        CREATE TABLE session_message (id TEXT, session_id TEXT, type TEXT, seq INTEGER, data TEXT);
        """
    )
    db.execute("INSERT INTO session VALUES ('ses_old', NULL, 'Old', ?, 1000)", (cwd,))
    db.execute("INSERT INTO session VALUES ('ses_child', 'ses_old', 'Child', ?, 1000)", (cwd,))
    db.execute("INSERT INTO session_v2 VALUES ('ses_new', NULL, 'New', ?, 2000)", (cwd,))
    messages = [
        ("m1", "ses_old", 1, {"role": "user", "time": {"created": 1000}}),
        ("m2", "ses_old", 2, {"role": "assistant"}),
    ]
    parts = [
        ("p1", "m1", 1, {"type": "text", "text": "list files"}),
        ("p2", "m2", 2, {"type": "tool", "tool": "bash", "state": {"input": {"command": "ls"}}}),
        ("p3", "m2", 3, {"type": "patch", "files": ["/p/x.py"]}),
        ("p4", "m2", 4, {"type": "text", "text": "Here they are."}),
    ]
    for row in messages:
        db.execute("INSERT INTO message VALUES (?,?,?,?)", (*row[:3], json.dumps(row[3])))
    for row in parts:
        db.execute("INSERT INTO part VALUES (?,?,?,?)", (*row[:3], json.dumps(row[3])))
    v2 = [
        ("a", "user", 1, {"text": "make a page", "time": {"created": 2000}}),
        ("b", "assistant", 2, {"content": [
            {"type": "reasoning", "text": "hmm"},
            {"type": "tool", "name": "write", "state": {"input": {"filePath": "/p/index.html"}}},
            {"type": "text", "text": "Done."},
        ]}),
        ("c", "idle", 3, {"outcome": "failed"}),
    ]
    for ident, kind, seq, data in v2:
        db.execute("INSERT INTO session_message VALUES (?,'ses_new',?,?,?)", (ident, kind, seq, json.dumps(data)))
    db.commit()
    db.close()
    return path


def test_opencode_reads_both_schemas_and_skips_child_sessions(home, workspace):
    _opencode_db(home, str(workspace))
    found = {summary.id: summary for summary in opencode.find(home, workspace)}
    assert set(found) == {"ses_old", "ses_new"}

    (old,) = opencode.load(found["ses_old"]).turns
    assert (old.user, old.answer, old.commands, old.files_changed) == ("list files", "Here they are.", 1, ["/p/x.py"])
    (new,) = opencode.load(found["ses_new"]).turns
    assert (new.user, new.answer, new.files_changed) == ("make a page", "Done.", ["/p/index.html"])


# --- Cursor --------------------------------------------------------------------


def test_cursor_reads_bubbles_and_the_workspace_folder(home, workspace):
    user_dir = home / ".config" / "Cursor" / "User"
    (user_dir / "globalStorage").mkdir(parents=True)
    (user_dir / "workspaceStorage" / "w1").mkdir(parents=True)
    (user_dir / "workspaceStorage" / "w1" / "workspace.json").write_text(json.dumps({"folder": workspace.as_uri()}))
    db = sqlite3.connect(user_dir / "globalStorage" / "state.vscdb")
    db.executescript(
        "CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB);"
        "CREATE TABLE composerHeaders (composerId TEXT, workspaceId TEXT);"
    )
    headers = [{"bubbleId": "b1", "type": 1}, {"bubbleId": "b2", "type": 2}]
    db.execute("INSERT INTO cursorDiskKV VALUES ('composerData:c1', ?)", (json.dumps({"name": "Greeting", "lastUpdatedAt": 5000, "fullConversationHeadersOnly": headers}),))
    db.execute("INSERT INTO cursorDiskKV VALUES ('composerData:c2', ?)", (json.dumps({"fullConversationHeadersOnly": []}),))
    db.execute("INSERT INTO cursorDiskKV VALUES ('bubbleId:c1:b1', ?)", (json.dumps({"type": 1, "text": "hello"}),))
    db.execute("INSERT INTO cursorDiskKV VALUES ('bubbleId:c1:b2', ?)", (json.dumps({"type": 2, "text": "Hi!"}),))
    db.execute("INSERT INTO composerHeaders VALUES ('c1', 'w1')")
    db.commit()
    db.close()

    (summary,) = cursor.find(home, workspace)
    assert (summary.title, summary.cwd) == ("Greeting", str(workspace))
    (turn,) = cursor.load(summary).turns
    assert (turn.user, turn.answer) == ("hello", "Hi!")


# --- Qwen Code, Continue, Aider --------------------------------------------------


def test_qwen_code_skips_thoughts_and_tool_results(home, workspace):
    path = home / ".qwen" / "projects" / "-p" / "chats" / "q1.jsonl"
    _jsonl(path, [
        {"type": "user", "cwd": str(workspace), "message": {"role": "user", "parts": [{"text": "write a.txt"}]}},
        {"type": "assistant", "message": {"role": "model", "parts": [
            {"text": "plan", "thought": True},
            {"functionCall": {"name": "write_file", "args": {"file_path": "/p/a.txt"}}},
            {"functionCall": {"name": "run_shell_command", "args": {"command": "ls"}}},
        ]}},
        {"type": "user", "message": {"role": "user", "parts": [{"functionResponse": {"name": "write_file"}}]}},
        {"type": "assistant", "message": {"role": "model", "parts": [{"text": "Written."}]}},
    ])
    (summary,) = qwen_code.find(home, workspace)
    (turn,) = qwen_code.load(summary).turns
    assert (turn.answer, turn.files_changed, turn.commands) == ("Written.", ["/p/a.txt"], 1)


def test_continue_reads_text_parts_and_tool_calls(home, workspace):
    path = home / ".continue" / "sessions" / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sessionId": "s1",
        "title": "Greeting",
        "workspaceDirectory": workspace.as_uri(),
        "history": [
            {"message": {"role": "user", "content": [{"type": "text", "text": "edit it"}]}},
            {"message": {"role": "assistant", "content": "", "toolCalls": [
                {"function": {"name": "edit_existing_file", "arguments": json.dumps({"filepath": "/p/m.py"})}},
            ]}},
            {"message": {"role": "assistant", "content": "Edited."}},
        ],
    }))
    (summary,) = continue_dev.find(home, workspace)
    assert (summary.id, summary.cwd) == ("s1", str(workspace))
    (turn,) = continue_dev.load(summary).turns
    assert (turn.answer, turn.files_changed) == ("Edited.", ["/p/m.py"])


def test_aider_splits_runs_and_reads_applied_edits(home, workspace):
    (workspace / aider.HISTORY_FILE).write_text(
        "# aider chat started at 2024-05-01 10:00:00\n\n> Aider v0.50\n\n#### add hello\n#### in util.py\n\n"
        "Sure.\n\n> Tokens: 1k\n> Applied edit to util.py\n\n#### now test it\n\nDone.\n\n"
        "# aider chat started at 2024-05-02 09:00:00\n\n#### second run\nok\n",
        encoding="utf-8",
    )
    first, second = sorted(aider.find(home, workspace), key=lambda s: s.id)
    one, two = aider.load(first).turns
    assert (one.user, one.answer, one.files_changed) == ("add hello\nin util.py", "Sure.", ["util.py"])
    assert (two.user, two.answer) == ("now test it", "Done.")
    assert [t.user for t in aider.load(second).turns] == ["second run"]


# --- Writing a Kvist session -----------------------------------------------------


def _imported(count: int, **overrides) -> ImportedSession:
    turns = [ImportedTurn(f"request {i}", f"answer {i}", ts=1000.0 + i) for i in range(count)]
    for key, value in overrides.items():
        setattr(turns[-1], key, value)
    return ImportedSession(SessionSummary("claude-code", "Abc-123_def", "My title", "/p", 2000.0, "/x"), turns)


def test_import_session_makes_a_session_kvist_can_list_and_replay(workspace):
    source = SOURCES[0]
    result = import_session(_agent(workspace), source, _imported(HISTORY_TURNS + 3, files_changed=["a.py"], commands=2))
    assert result is not None
    assert result.name == session_name(source, "Abc-123_def") == "cc-abc123def"
    assert (result.turns, result.turns_in_context) == (HISTORY_TURNS + 3, HISTORY_TURNS)

    listing = run_session_op(workspace, "list")
    (item,) = listing["session_items"]
    assert (item["name"], item["title"], item["updated_at"]) == ("cc-abc123def", "Claude Code: My title", 2000.0)

    history = run_session_op(workspace, "history", name=result.name)
    kinds = [event["event"]["type"] for event in history["events"]]
    assert kinds[:3] == ["user", "final", "turn_end"]
    assert kinds.count("user") == HISTORY_TURNS + 3

    document = json.loads((workspace / ".kvist" / "sessions" / f"{result.name}.json").read_text(encoding="utf-8"))
    entries = document["session_history"]
    assert [e["goal"] for e in entries][0] == "request 3"
    assert entries[-1]["role"] == "coder"
    assert entries[-1]["outputs"] == ["Imported from Claude Code; the agent changed 1 files: a.py and ran 2 shell commands."]
    assert document["messages"]["last_id"] == 2 * (HISTORY_TURNS + 3)
    assert entries[-1]["msg_id"] in document["messages"]["user_ids"]
    assert document["chat_history"][0]["role"] == "system"
    assert len(document["chat_history"]) == 1 + 2 * HISTORY_TURNS


def test_import_session_is_idempotent_and_never_overwrites(workspace):
    source = SOURCES[0]
    assert import_session(_agent(workspace), source, _imported(2)) is not None
    assert import_session(_agent(workspace), source, _imported(5)) is None
    (item,) = run_session_op(workspace, "status")["session_items"]
    assert len(item["session_history"]) == 2


def test_unanswered_and_stopped_turns_keep_the_chat_alternating(workspace):
    turns = [ImportedTurn("a", ""), ImportedTurn("b", "", stopped=True)]
    session = ImportedSession(SessionSummary("codex", "x1", "", "/p", 1.0, "/x"), turns)
    result = import_session(_agent(workspace), SOURCES[1], session)
    document = json.loads((workspace / ".kvist" / "sessions" / f"{result.name}.json").read_text(encoding="utf-8"))
    roles = [m["role"] for m in document["chat_history"]]
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert "stopped" in document["chat_history"][-1]["content"]
    assert document["session_history"][-1]["stopped_by_user"] is True
    assert document["title"] == "Codex"


def test_secrets_in_imported_text_are_redacted(workspace):
    secret = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4"
    session = ImportedSession(SessionSummary("codex", "s2", "t", "/p", 1.0, "/x"), [ImportedTurn(f"use key {secret}", f"ok {secret}")])
    result = import_session(_agent(workspace), SOURCES[1], session)
    for suffix in (".json", ".events.jsonl"):
        assert secret not in (workspace / ".kvist" / "sessions" / f"{result.name}{suffix}").read_text(encoding="utf-8")


def test_a_conversation_without_requests_is_refused(workspace):
    with pytest.raises(ValueError):
        import_session(_agent(workspace), SOURCES[0], _imported(0))


# --- The command -----------------------------------------------------------------


class _Ui:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[list[dict]] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.asked.append(options)
        return self.answers.pop(0)


def test_here_keeps_only_sessions_inside_the_workspace(workspace):
    inside = SessionSummary("x", "1", "", str(workspace / "sub"), 0, "")
    outside = SessionSummary("x", "2", "", str(workspace.parent), 0, "")
    assert here([inside, outside, SessionSummary("x", "3", "", "", 0, "")], workspace) == [inside]


def test_export_list_counts_sessions_per_agent(home, workspace):
    _claude_session(home, str(workspace))
    outcome = run_export(_agent(workspace), None, ["list"])
    assert outcome.ok and "Claude Code: 1" in outcome.message


def test_export_wizard_imports_the_chosen_session(home, workspace):
    _claude_session(home, str(workspace))
    ui = _Ui("claude-code", "abc-123")
    outcome = run_export(_agent(workspace), ui, [])
    assert outcome.ok
    assert [item["session"] for item in outcome.effects["imported"]] == ["cc-abc123"]
    assert {o["value"] for o in ui.asked[0]} == {"claude-code", "__all__"}


def test_export_wizard_cancel_changes_nothing(home, workspace):
    _claude_session(home, str(workspace))
    assert run_export(_agent(workspace), _Ui(None), []) is None
    assert not (workspace / ".kvist").exists()


def test_export_all_skips_other_folders_unless_asked(home, workspace, tmp_path):
    _claude_session(home, str(tmp_path / "elsewhere"))
    here_only = run_export(_agent(workspace), None, ["claude-code", "all"])
    assert here_only.effects["imported"] == []
    everywhere = run_export(_agent(workspace), None, ["claude-code", "all", "everywhere"])
    assert len(everywhere.effects["imported"]) == 1
    again = run_export(_agent(workspace), None, ["all", "everywhere"])
    assert again.effects["already_imported"] == 1


def test_export_reports_an_unknown_agent_and_an_empty_machine(home, workspace):
    assert run_export(_agent(workspace), None, ["nothing"]).ok is False
    _claude_session(home, str(workspace))
    unknown = run_export(_agent(workspace), None, ["nothing"])
    assert unknown.ok is False and "claude-code" in unknown.message


def test_first_run_lists_only_agents_with_sessions_in_the_folder(home, workspace, tmp_path):
    assert setup_listing(workspace) == []
    _claude_session(home, str(tmp_path / "elsewhere"))
    assert setup_listing(workspace) == []
    _claude_session(home, str(workspace))
    assert setup_listing(workspace) == [{"key": "claude-code", "label": "Claude Code", "here": 1}]


def test_first_run_imports_without_an_agent_and_only_the_named_ones(home, workspace):
    _claude_session(home, str(workspace))
    assert setup_import(workspace, ["codex"]).effects["imported"] == []
    outcome = setup_import(workspace, ["claude-code"])
    assert outcome.ok
    assert [item["session"] for item in outcome.effects["imported"]] == ["cc-abc123"]
    assert setup_import(workspace, ["claude-code"]).effects["already_imported"] == 1

