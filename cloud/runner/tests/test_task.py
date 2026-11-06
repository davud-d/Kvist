# -*- coding: utf-8 -*-
"""The runner's task process against the fake Kvist: relaying, scrubbing, pauses and limits."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from kvist_runner import guard
from kvist_runner.config import RunnerConfig
from kvist_runner.task import Limits, TaskProcess
from kvist_runner.workspace import WorkspaceError, prepare

FAKE_KVIST = Path(__file__).with_name("fake_kvist.py")
KEY = "sk-runner-canary-0123456789abcdef"


@pytest.fixture(autouse=True)
def no_systemd(monkeypatch):
    monkeypatch.setattr(guard, "_systemd", False)


def config(tmp_path) -> RunnerConfig:
    return RunnerConfig(
        machine_id=1, gateway_url="ws://unused", token="t" * 20, root=tmp_path,
        kvist_command=(sys.executable, str(FAKE_KVIST)), heartbeat_seconds=0.1,
    )


def limits(**overrides) -> Limits:
    values = {"command_seconds": 60.0, "background_seconds": 0.0, "vcpu": 2, "memory_gb": 4,
              "hours_grace_seconds": 300.0, "provider_outage_seconds": 300.0}
    values.update(overrides)
    return Limits(**values)


class Recorder:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.changes = 0

    def emit(self, message: dict) -> None:
        self.messages.append(message)

    def change(self) -> None:
        self.changes += 1

    def events(self, kind: str | None = None) -> list[dict]:
        found = [m["event"] for m in self.messages if m["type"] == "event"]
        return [e for e in found if kind is None or e.get("type") == kind]

    def exit(self) -> dict:
        return next(m for m in self.messages if m["type"] == "task_exit")


def make(tmp_path, goal: str, recorder: Recorder, **limit_overrides) -> TaskProcess:
    return TaskProcess(
        task_id=5, goal=goal, repo_url="", resume=False,
        env={"AGENT_LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": KEY},
        limits=limits(**limit_overrides), config=config(tmp_path), emit=recorder.emit, on_change=recorder.change,
    )


async def run(task: TaskProcess, timeout: float = 20.0) -> None:
    await asyncio.wait_for(task.run(), timeout)


async def test_quick_task_relays_events_and_exits(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "quick", recorder)
    await run(task)
    kinds = [event["type"] for event in recorder.events()]
    assert kinds[0] == "hello" and "final" in kinds and kinds[-1] == "turn_end"
    assert recorder.exit()["reason"] == "finished"
    assert (tmp_path / "work" / "task-5" / "hello.txt").read_text() == "hello from the cloud\n"
    # running went on at hello and off at the end: the heartbeat was asked for both changes
    assert recorder.changes >= 2 and task.running is False


async def test_the_key_is_scrubbed_from_relayed_lines(tmp_path):
    recorder = Recorder()
    await run(make(tmp_path, "leak", recorder))
    message = recorder.events("message")[0]
    assert message["markdown"] == "my key is [redacted]"
    assert KEY not in repr(recorder.messages)


async def test_the_key_reaches_kvist_only_through_its_environment(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "quick", recorder)
    await run(task)
    assert task._model_env is None and task._secrets == []
    assert KEY not in (tmp_path / "logs" / "task-5.log").read_text()


async def test_wrong_key_pauses_the_task(tmp_path):
    recorder = Recorder()
    await run(make(tmp_path, "auth-error", recorder))
    reasons = [m["reason"] for m in recorder.messages if m["type"] == "task_reason"]
    assert reasons == ["invalid_key"]
    assert recorder.exit()["reason"] == "invalid_key"


async def test_provider_outage_pauses_after_the_window(tmp_path):
    recorder = Recorder()
    await run(make(tmp_path, "rate-limit", recorder, provider_outage_seconds=0.6))
    assert recorder.exit()["reason"] == "provider_unavailable"
    assert recorder.events("final")[-1]["paused"] is True


async def test_hours_exhausted_pauses_at_a_safe_point(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "hang", recorder)
    runner = asyncio.create_task(run(task))
    await asyncio.sleep(0.8)
    await task.control("hours_exhausted")
    await runner
    assert recorder.exit()["reason"] == "hours_exhausted"
    assert recorder.events("final")[-1]["paused"] is True


async def test_a_command_that_ignores_the_pause_is_cancelled_after_the_grace(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "stubborn", recorder, hours_grace_seconds=0.5)
    runner = asyncio.create_task(run(task))
    await asyncio.sleep(0.8)
    await task.control("hours_exhausted")
    await runner
    assert recorder.events("task_state")[-1]["state"] == "cancelled"
    assert recorder.exit()["reason"] == "hours_exhausted"


async def test_command_time_limit_stops_the_task(tmp_path):
    recorder = Recorder()
    await run(make(tmp_path, "long-command", recorder, command_seconds=0.5))
    assert recorder.exit()["reason"] == "command_time_limit"


async def test_waiting_for_an_answer_is_not_running(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "ask", recorder)
    runner = asyncio.create_task(run(task))
    for _ in range(100):
        if recorder.events("input_request"):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)
    assert task.running is False
    await task.answer({"request_id": "r1", "approved": True})
    await runner
    assert recorder.events("final")[-1]["outcome"] == "done"


async def test_background_job_keeps_kvist_until_it_ends(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "bg 1", recorder, background_seconds=30.0)
    await run(task)
    kinds = [event["type"] for event in recorder.events()]
    assert kinds.index("turn_end") < kinds.index("job_finished")
    assert recorder.exit()["reason"] == "finished"


async def test_background_window_closes_kvist(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "bg 30", recorder, background_seconds=0.5)
    started = asyncio.get_running_loop().time()
    await run(task)
    assert asyncio.get_running_loop().time() - started < 15


def test_systemd_wrapper(monkeypatch):
    monkeypatch.setattr(guard, "_systemd", True)
    argv = guard.with_machine_limits(["kvist", "--x"], vcpu=4, memory_gb=8)
    assert argv[:4] == ["systemd-run", "--user", "--scope", "-q"]
    assert "MemoryMax=8G" in argv and "CPUQuota=400%" in argv and argv[-2:] == ["kvist", "--x"]
    monkeypatch.setattr(guard, "_systemd", False)
    assert guard.with_machine_limits(["kvist"], 2, 4) == ["kvist"]


@pytest.mark.parametrize(
    ("event", "trouble"),
    [
        ({"type": "error", "message": "Authentication failed (HTTP 401)"}, "auth"),
        ({"type": "error", "message": "Rate limit exceeded: too many requests"}, "transient"),
        ({"type": "status", "is_error": True, "user_message": "Server error (HTTP 503): busy"}, "transient"),
        ({"type": "status", "is_error": False, "user_message": "HTTP 503"}, ""),
        ({"type": "message", "markdown": "401 tests passed"}, ""),
    ],
)
def test_provider_trouble(event, trouble):
    assert guard.provider_trouble(event) == trouble


async def test_clone_rules(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    (origin / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(origin), "add", "."], check=True)
    subprocess.run(["git", "-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
                   check=True)
    with pytest.raises(WorkspaceError):
        await prepare(tmp_path / "work", 1, f"file://{origin}", allow_local=False)
    folder = await prepare(tmp_path / "work", 2, f"file://{origin}", allow_local=True)
    assert (folder / "README.md").read_text() == "hi\n"
    with pytest.raises(WorkspaceError) as failure:
        await prepare(tmp_path / "work", 3, f"file://{tmp_path}/missing", allow_local=True)
    assert failure.value.reason == "clone_failed"


async def test_the_next_message_goes_into_the_same_conversation(tmp_path):
    """A second run of Kvist switches back into the first run's session before it takes the message."""
    first = Recorder()
    await run(make(tmp_path, "say first words", first))
    name = next(e["session"] for e in first.events("hello"))

    second = Recorder()
    follow_up = make(tmp_path, "say and again", second)
    follow_up.session = name
    await run(follow_up)
    reply = [e for e in second.events("message") if e.get("source") == "reply"]
    assert reply and "I remember 1 earlier messages: say first words" in reply[0]["markdown"]
    assert [e["session"] for e in second.events("session")] == [name]


async def test_a_conversation_that_cannot_be_reopened_says_so_and_goes_on(tmp_path):
    recorder = Recorder()
    task = make(tmp_path, "say hello again", recorder)
    task.session = "no-such-session"
    await run(task)
    notices = [e["markdown"] for e in recorder.events("message") if e.get("source") == "notice"]
    assert notices and "earlier conversation" in notices[0]
    assert recorder.events("final") or any("remember 0" in e.get("markdown", "") for e in recorder.events("message"))
