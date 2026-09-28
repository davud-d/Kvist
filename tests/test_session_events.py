from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import session_events
from executor import ExecutionResult
from session_events import JOURNAL_SUFFIX, SessionJournal
from session_store import run_session_op
from tests.conftest import FakeLLMClient, requires_posix


def _journal_file(agent, session: str | None = None) -> Path:
    name = session or agent.current_session
    return agent.executor.workspace / ".kvist" / "sessions" / f"{name}{JOURNAL_SUFFIX}"


def _events(agent, session: str | None = None) -> list[dict]:
    page = run_session_op(
        agent.executor.workspace, "history", session or agent.current_session
    )
    return [entry["event"] for entry in page["events"]]


def _types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def _scripted_run(agent, responses: list[dict]) -> str:
    queue = iter(responses)
    agent._call_action_model = lambda prompt: json.dumps(next(queue))
    return agent.run("write the notes and check them", routed_role="coder")


def _step(action_type: str, args: dict, plan_step: str = "edit") -> dict:
    return {
        "thought": f"do {action_type}",
        "plan_step": plan_step,
        "user_message": f"running {action_type}",
        "action": {"type": action_type, "args": args},
    }


def _coder_agent(agent_factory):
    agent = agent_factory(max_steps=10)
    real_execute = agent.executor.execute

    def execute(command):
        if command.action.type != "run_shell":
            return real_execute(command)
        return ExecutionResult(
            True,
            "run_shell",
            {"stdout": "12 passed\n", "stderr": "", "returncode": 0},
        )

    agent.executor.execute = execute
    return agent


def _chat_agent(agent_factory, monkeypatch, answer: str, **overrides):
    agent = agent_factory(stream=False, **overrides)
    agent.client = FakeLLMClient(content=answer)
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")
    return agent


def test_agent_turn_journals_commands_files_and_the_full_answer(agent_factory):
    agent = _coder_agent(agent_factory)
    answer = "All done. " + "detail " * 100  # far beyond the 200 chars kept in session_history
    result = _scripted_run(
        agent,
        [
            _step("write_file", {"path": "notes.txt", "content": "alpha\nbeta\n"}),
            _step("read_file", {"path": "notes.txt"}, "verify"),
            _step("run_shell", {"command": ["pytest", "-q"]}, "verify"),
            _step("final_answer", {"message": answer}, "done"),
        ],
    )

    events = _events(agent)
    assert events[0] == {"type": "user", "text": "write the notes and check them"}
    assert events[-1] == {"type": "turn_end"}
    assert _types(events)[1] == "route"

    steps = [e for e in events if e["type"] == "step"]
    results = [e for e in events if e["type"] == "action_result"]
    assert [e["action_type"] for e in steps][:3] == ["write_file", "read_file", "run_shell"]
    assert steps[0]["action_args"]["content"] == "alpha\nbeta\n"

    written = results[0]
    assert (written["path"], written["first_line"], written["ok"]) == ("notes.txt", 1, True)
    shell_step = steps[2]
    assert shell_step["action_args"]["command"] == ["pytest", "-q"]
    shell = results[2]
    assert (shell["exit_code"], shell["ok"]) == (0, True)
    assert "12 passed" in shell["output"]

    final = [e for e in events if e["type"] == "final"]
    assert final and final[-1]["message"] == result
    assert len(final[-1]["message"]) > 200


def test_journal_keeps_event_order_and_numbers_turns(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "first answer")
    agent.run("one", routed_role="chat")
    agent.client = FakeLLMClient(content="second answer")
    agent.run("two", routed_role="chat")

    page = run_session_op(agent.executor.workspace, "history", agent.current_session)
    seqs = [entry["seq"] for entry in page["events"]]
    turns = [entry["turn"] for entry in page["events"]]
    assert seqs == sorted(set(seqs))
    assert turns == sorted(turns)
    assert set(turns) == {1, 2}
    users = [e["text"] for e in _events(agent) if e["type"] == "user"]
    assert users == ["one", "two"]


def test_chat_turn_journals_route_reply_and_end(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "Hello **world**")
    agent.run("hello", routed_role="chat")

    events = _events(agent)
    assert _types(events)[0] == "user"
    assert "route" in _types(events)
    replies = [e["markdown"] for e in events if e["type"] == "message"]
    assert replies == ["Hello **world**"]
    assert events[-1]["type"] == "turn_end"
    assert not {"status", "live_start", "live_stop", "stream_chunk", "action_output"} & set(
        _types(events)
    )


def test_streamed_chat_reply_is_journaled_once_at_its_final_text(agent_factory, monkeypatch):
    agent = agent_factory(stream=True)
    agent.client = FakeLLMClient(stream_chunks=["Hel", "lo ", "there"])
    monkeypatch.setattr("agent_model._load_role_prompt", lambda role: "chat prompt")
    agent.run("hi", routed_role="chat")

    events = _events(agent)
    commits = [e for e in events if e["type"] == "stream_commit"]
    assert [c["text"] for c in commits] == ["Hello there"]
    assert "stream_chunk" not in _types(events)


def test_thoughts_are_journaled_as_their_last_snapshot(tmp_path):
    journal = SessionJournal(tmp_path, lambda: tmp_path / ".kvist" / "sessions" / "t.events.jsonl", print)
    ui = session_events.JournalingUI(_Quiet(), journal)

    journal.begin_turn("think")
    ui.set_thinking("first idea")
    ui.set_thinking("first idea, refined")
    ui.end_thinking()
    journal.end_turn()

    page = session_events.read_history(
        tmp_path / ".kvist" / "sessions" / "t.events.jsonl", tmp_path
    )
    events = [entry["event"] for entry in page["events"]]
    assert _types(events) == [
        "user",
        "reasoning_start",
        "reasoning_chunk",
        "reasoning_commit",
        "turn_end",
    ]
    assert events[2]["text"] == "first idea, refined"
    assert events[1]["stream_id"] == events[2]["stream_id"] == events[3]["stream_id"]


class _Quiet:
    """A UI that shows nothing and offers no optional capability."""

    def print_plan(self, *args, **kwargs):
        pass

    def print_step_header(self, *args, **kwargs):
        pass

    def print_final(self, *args, **kwargs):
        pass

    def print_user_markdown(self, *args, **kwargs):
        pass

    def print_result(self, *args, **kwargs):
        pass

    def print_error(self, *args, **kwargs):
        pass

    def print_sandbox_notice(self, *args, **kwargs):
        pass


def test_errors_and_user_stop_are_journaled(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "e.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(_Quiet(), journal)

    journal.begin_turn("go")
    ui.print_error("model exploded")
    journal.mark_stopped()
    journal.end_turn()

    events = [e["event"] for e in session_events.read_history(path, tmp_path)["events"]]
    assert {"type": "error", "message": "model exploded"} in events
    assert events[-1] == {"type": "turn_end", "stopped_by_user": True}


def test_secrets_never_reach_the_journal_file(agent_factory, monkeypatch):
    secret = "sk-proj-" + "d" * 30
    agent = _chat_agent(agent_factory, monkeypatch, f"your key is api_key={secret}")
    agent.run(f"remember api_key={secret}", routed_role="chat")

    raw = _journal_file(agent).read_text(encoding="utf-8")
    assert raw.strip()
    assert secret not in raw
    assert "[REDACTED]" in raw


def test_written_file_content_is_redacted_in_the_journal(agent_factory):
    secret = "sk-proj-" + "e" * 30
    agent = _coder_agent(agent_factory)
    _scripted_run(
        agent,
        [
            _step("write_file", {"path": "cfg.txt", "content": f"api_key={secret}\n"}),
            _step("read_file", {"path": "cfg.txt"}, "verify"),
            _step("final_answer", {"message": "done"}, "done"),
        ],
    )

    assert secret not in _journal_file(agent).read_text(encoding="utf-8")


def test_oversized_fields_are_cut_with_a_marker_and_keep_their_structure(
    agent_factory,
):
    agent = _coder_agent(agent_factory)
    big = "line of code\n" * 5_000  # 65 000 chars
    _scripted_run(
        agent,
        [
            _step("write_file", {"path": "big.txt", "content": big}),
            _step("read_file", {"path": "big.txt"}, "verify"),
            _step("final_answer", {"message": "done"}, "done"),
        ],
    )

    step = next(e for e in _events(agent) if e["type"] == "step")
    content = step["action_args"]["content"]
    assert len(content) < session_events.MAX_FIELD_CHARS + 100
    assert content.startswith("line of code")
    assert "characters omitted ...]" in content
    assert step["action_args"]["path"] == "big.txt"


def test_answers_get_a_higher_ceiling_than_other_fields(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "a.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(_Quiet(), journal)
    answer = "x" * 60_000

    journal.begin_turn("q")
    ui.print_final(answer, [])
    journal.end_turn()

    events = [e["event"] for e in session_events.read_history(path, tmp_path)["events"]]
    assert next(e for e in events if e["type"] == "final")["message"] == answer


def test_events_per_turn_are_capped_but_the_outcome_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(session_events, "MAX_EVENTS_PER_TURN", 5)
    path = tmp_path / ".kvist" / "sessions" / "c.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(_Quiet(), journal)

    journal.begin_turn("flood")
    for index in range(20):
        ui.print_sandbox_notice(f"notice {index}")
    ui.print_final("the answer", [])
    journal.end_turn()

    events = [e["event"] for e in session_events.read_history(path, tmp_path)["events"]]
    assert sum(1 for e in events if e["type"] == "sandbox_notice") <= 4
    assert next(e for e in events if e["type"] == "final")["message"] == "the answer"
    notice = next(e for e in events if e["type"] == "journal_notice")
    assert notice["omitted_events"] > 0
    assert events[-1]["type"] == "turn_end"


def test_old_turns_are_dropped_whole_when_the_session_grows(tmp_path, monkeypatch):
    monkeypatch.setattr(session_events, "MAX_SESSION_BYTES", 6_000)
    monkeypatch.setattr(session_events, "KEEP_SESSION_BYTES", 3_000)
    path = tmp_path / ".kvist" / "sessions" / "g.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(_Quiet(), journal)

    for turn in range(12):
        journal.begin_turn(f"turn {turn}")
        ui.print_final("y" * 400, [])
        journal.end_turn()

    page = session_events.read_history(path, tmp_path)
    events = [entry["event"] for entry in page["events"]]
    users = [e["text"] for e in events if e["type"] == "user"]
    assert users[-1] == "turn 11"
    assert "turn 0" not in users
    assert page["journal_bytes"] < 6_000 + 1_500
    notice = next(e for e in events if e["type"] == "journal_notice")
    assert notice["omitted_turns"] >= 1
    # Whole turns only: every kept turn still opens with its user event and ends with turn_end.
    assert events[1]["type"] == "user"
    assert events[-1]["type"] == "turn_end"


def test_session_turn_count_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(session_events, "MAX_SESSION_TURNS", 4)
    monkeypatch.setattr(session_events, "KEEP_SESSION_TURNS", 2)
    path = tmp_path / ".kvist" / "sessions" / "n.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)

    for turn in range(7):
        journal.begin_turn(f"turn {turn}")
        journal.emit(_Event({"type": "message", "markdown": "m"}))
        journal.end_turn()

    users = [
        e["event"]["text"]
        for e in session_events.read_history(path, tmp_path)["events"]
        if e["event"]["type"] == "user"
    ]
    assert users[-1] == "turn 6"
    assert len(users) <= 4


class _Event:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def to_dict(self) -> dict:
        return dict(self._payload)


def test_history_pages_back_from_the_newest_events(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "p.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    for turn in range(3):
        journal.begin_turn(f"turn {turn}")
        journal.end_turn()
        journal.begin_turn(f"again {turn}")
        journal.emit(_Event({"type": "message", "markdown": str(turn)}))
        journal.end_turn()

    everything = session_events.read_history(path, tmp_path)["events"]
    newest = session_events.read_history(path, tmp_path, limit=4)
    assert [e["seq"] for e in newest["events"]] == [e["seq"] for e in everything[-4:]]
    assert newest["has_more"] is True

    older = session_events.read_history(
        path, tmp_path, limit=4, before=newest["events"][0]["seq"]
    )
    assert older["events"][-1]["seq"] == newest["events"][0]["seq"] - 1
    assert older["total_events"] == len(everything)

    first = session_events.read_history(path, tmp_path, limit=1000)
    assert first["has_more"] is False


def test_history_op_validates_its_arguments(agent_factory):
    agent = agent_factory()
    agent.new_session("feature")
    workspace = agent.executor.workspace

    empty = run_session_op(workspace, "history", "feature")
    assert (empty["ok"], empty["events"], empty["has_more"]) == (True, [], False)

    with pytest.raises(ValueError, match="Unknown session"):
        run_session_op(workspace, "history", "ghost")
    with pytest.raises(ValueError, match="requires a session name"):
        run_session_op(workspace, "history")
    with pytest.raises(ValueError, match="limit"):
        run_session_op(workspace, "history", "feature", limit=0)
    with pytest.raises(ValueError, match="cursor"):
        run_session_op(workspace, "history", "feature", before=0)


def test_a_session_saved_before_journals_existed_reads_as_empty_history(tmp_path):
    sessions = tmp_path / ".kvist" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "old.json").write_text(
        json.dumps(
            {
                "version": 1,
                "session": "old",
                "updated_at": 1.0,
                "session_history": [{"goal": "old work", "role": "coder", "result": "ok"}],
                "chat_history": [],
                "checkpoint": None,
            }
        ),
        encoding="utf-8",
    )

    history = run_session_op(tmp_path, "history", "old")
    status = run_session_op(tmp_path, "status")

    assert history["events"] == [] and history["total_events"] == 0
    item = status["session_items"][0]
    assert item["title"] == "old work"
    assert item["journal_bytes"] == 0


def test_status_keeps_histories_and_list_leaves_them_out(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "an answer")
    agent.run("a question", routed_role="chat")
    workspace = agent.executor.workspace

    status = run_session_op(workspace, "status")
    listing = run_session_op(workspace, "list")

    full = status["session_items"][0]
    light = listing["session_items"][0]
    assert full["session_history"] and full["chat_history"]
    assert "session_history" not in light and "chat_history" not in light
    assert light["title"] == full["title"] == "a question"
    assert light["journal_bytes"] == full["journal_bytes"] > 0
    assert listing["active_session"] == status["active_session"]


def test_journal_never_reaches_the_model_prompt(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "first")
    agent.run("marker-question", routed_role="chat")
    agent.client = FakeLLMClient(content="second")
    agent.run("follow up", routed_role="chat")

    sent = str(agent.client.requests)
    assert "journal_notice" not in sent and '"seq"' not in sent


def test_deleting_a_session_removes_its_journal(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("old-work")
    agent.run("work", routed_role="chat")
    journal = _journal_file(agent)
    assert journal.exists()
    agent.new_session("other")

    agent._delete_session("old-work")

    assert not journal.exists()
    with pytest.raises(ValueError, match="Unknown session"):
        run_session_op(agent.executor.workspace, "history", "old-work")


def test_delete_op_removes_the_journal_too(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("old-work")
    agent.run("work", routed_role="chat")
    journal = _journal_file(agent)
    agent.new_session("other")

    run_session_op(agent.executor.workspace, "delete", "old-work")

    assert not journal.exists()


def test_reset_op_removes_the_journal_of_the_active_session(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("feature")
    agent.run("work", routed_role="chat")
    journal = _journal_file(agent)
    assert journal.exists()

    run_session_op(agent.executor.workspace, "reset")

    assert not journal.exists()
    assert run_session_op(agent.executor.workspace, "history", "feature")["events"] == []


def test_new_session_keeps_the_previous_journal(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("first")
    agent.run("work", routed_role="chat")
    first = _journal_file(agent)

    agent.new_session("second")

    assert first.exists()
    assert _events(agent, "first")[0]["type"] == "user"
    assert run_session_op(agent.executor.workspace, "history", "second")["events"] == []
    agent.run("more", routed_role="chat")
    assert [e["text"] for e in _events(agent, "second") if e["type"] == "user"] == ["more"]


def test_creating_a_session_discards_a_stale_journal_of_the_same_name(agent_factory):
    agent = agent_factory()
    stale = agent.executor.workspace / ".kvist" / "sessions" / f"reborn{JOURNAL_SUFFIX}"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text('{"seq":1,"turn":1,"ts":0,"event":{"type":"user","text":"ghost"}}\n')

    agent.new_session("reborn")

    assert run_session_op(agent.executor.workspace, "history", "reborn")["events"] == []


def test_an_untouched_auto_session_leaves_no_journal(agent_factory):
    agent = agent_factory()
    first, _ = agent.start_fresh_session()
    agent.start_fresh_session()

    sessions = agent.executor.workspace / ".kvist" / "sessions"
    assert not (sessions / f"{first}.json").exists()
    assert not list(sessions.glob(f"*{JOURNAL_SUFFIX}"))


def test_a_session_with_a_journal_is_not_dropped_as_empty(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    first, _ = agent.start_fresh_session()
    agent.run("work", routed_role="chat")
    agent._session_history = []
    agent._chat_history = []
    agent.save_session()

    agent.start_fresh_session()

    assert (agent.executor.workspace / ".kvist" / "sessions" / f"{first}.json").exists()


def test_a_damaged_journal_does_not_break_reading_or_the_session_list(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("feature")
    agent.run("work", routed_role="chat")
    journal = _journal_file(agent)
    good = journal.read_text(encoding="utf-8")
    journal.write_text("not json at all\n" + good + '{"seq": 99, "tur', encoding="utf-8")

    history = run_session_op(agent.executor.workspace, "history", "feature")
    assert history["skipped_lines"] == 2
    assert history["events"][0]["event"]["type"] == "user"
    assert run_session_op(agent.executor.workspace, "status")["warnings"] == []

    agent.client = FakeLLMClient(content="next")
    assert agent.run("again", routed_role="chat") == "next"
    users = [e["text"] for e in _events(agent, "feature") if e["type"] == "user"]
    assert users == ["work", "again"]


def test_an_unusable_journal_never_stops_a_turn(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("feature")
    reports: list[str] = []
    agent._ui.debug_print = reports.append
    target = agent.executor.workspace / "elsewhere.jsonl"
    target.write_text("", encoding="utf-8")
    _journal_file(agent).symlink_to(target)

    assert agent.run("work", routed_role="chat") == "answer"

    assert any("Session journal disabled" in message for message in reports)
    assert target.read_text(encoding="utf-8") == ""


@requires_posix
def test_journal_file_is_private_to_the_owner(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.run("work", routed_role="chat")

    assert os.stat(_journal_file(agent)).st_mode & 0o077 == 0


def test_a_torn_last_line_does_not_swallow_the_next_event(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "answer")
    agent.new_session("feature")
    agent.run("first", routed_role="chat")
    journal = _journal_file(agent)
    journal.write_bytes(journal.read_bytes().rstrip(b"\n") + b'{"seq": 7')  # crash mid-write

    agent.client = FakeLLMClient(content="second")
    agent.run("second", routed_role="chat")

    history = run_session_op(agent.executor.workspace, "history", "feature")
    assert history["skipped_lines"] == 1
    assert [e["text"] for e in _events(agent, "feature") if e["type"] == "user"] == [
        "first",
        "second",
    ]


def test_resumed_turn_is_journaled_with_its_goal(agent_factory):
    agent = _coder_agent(agent_factory)
    agent._save_checkpoint("finish the report", 1)
    agent._call_action_model = lambda prompt: json.dumps(
        _step("final_answer", {"message": "resumed and done"}, "done")
    )

    agent.resume()

    events = _events(agent)
    assert events[0] == {"type": "user", "text": "finish the report", "resumed": True}
    assert events[-1]["type"] == "turn_end"


def test_resume_without_a_checkpoint_writes_nothing(agent_factory):
    agent = agent_factory()

    assert agent.resume() is None

    assert not list((agent.executor.workspace / ".kvist").rglob(f"*{JOURNAL_SUFFIX}"))


def test_token_usage_is_folded_into_one_event_per_turn(tmp_path):
    path = tmp_path / ".kvist" / "sessions" / "u.events.jsonl"
    journal = SessionJournal(tmp_path, lambda: path, print)
    ui = session_events.JournalingUI(_Quiet(), journal)

    journal.begin_turn("count")
    ui.print_final("counted", [])
    ui.print_usage({"input_tokens": 100, "output_tokens": 10, "model": "m", "kind": "step", "step": 1})
    ui.print_usage({"input_tokens": 50, "output_tokens": 5, "model": "m", "kind": "step", "step": 2})
    journal.end_turn()

    usage = [e["event"] for e in session_events.read_history(path, tmp_path)["events"] if e["event"]["type"] == "usage"]
    assert len(usage) == 1
    assert (usage[0]["input_tokens"], usage[0]["output_tokens"]) == (150, 15)


def test_journaling_ui_passes_capabilities_and_settings_through(tmp_path):
    class Real(_Quiet):
        verbose = False

    real = Real()
    ui = session_events.JournalingUI(real, SessionJournal(tmp_path, lambda: tmp_path / "x", print))

    assert not hasattr(ui, "ask_choice")
    real.ask_choice = lambda title, options: "finish"
    assert ui.ask_choice("t", []) == "finish"
    ui.verbose = True
    assert real.verbose is True


def test_history_op_cli_round_trip_in_subprocess(agent_factory, monkeypatch):
    agent = _chat_agent(agent_factory, monkeypatch, "cli answer")
    agent.new_session("cli-session")
    agent.run("cli question", routed_role="chat")
    root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment.update({"AGENT_SANDBOX_MODE": "workspace", "AGENT_SANDBOX_NET": "deny"})

    def cli(*arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(root / "main.py"), "--workspace",
             str(agent.executor.workspace), *arguments],
            cwd=root, env=environment, capture_output=True, text=True, timeout=60,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        assert completed.returncode == (0 if payload["ok"] else 1)
        return payload

    page = cli("--session-op", "history", "--session-name", "cli-session", "--history-limit", "2")
    assert page["ok"] is True and len(page["events"]) == 2 and page["has_more"] is True
    older = cli("--session-op", "history", "--session-name", "cli-session",
                "--history-before", str(page["events"][0]["seq"]))
    assert older["events"][-1]["seq"] < page["events"][0]["seq"]
    assert "session_history" not in cli("--session-op", "list")["session_items"][0]
    assert cli("--session-op", "history", "--session-name", "ghost")["ok"] is False
