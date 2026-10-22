# -*- coding: utf-8 -*-
"""Waits, one-time and recurring schedules (``kernel.agent.scheduler``) and the turns their time starts."""
from __future__ import annotations

from datetime import datetime

import pytest

from kernel.protocol.parser import CommandValidationError, validate_action_args
from kernel.agent.scheduler import ScheduleError, Scheduler, build_repeat, next_after, parse_at


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.moment = start.timestamp()

    def __call__(self) -> float:
        return self.moment

    def at(self, moment: datetime) -> None:
        self.moment = moment.timestamp()


NOW = datetime(2026, 10, 17, 9, 0)  # a Saturday


@pytest.fixture
def clock():
    return _Clock(NOW)


@pytest.fixture
def scheduler(tmp_path, clock):
    # The timer thread is left off so the tests move the clock and claim occurrences themselves.
    scheduler = Scheduler(tmp_path, clock=clock)
    scheduler._listener = lambda: None
    return scheduler


def test_daily_time_comes_today_when_still_ahead_and_tomorrow_when_passed():
    repeat = build_repeat({"time": "10:00"}, NOW)
    assert next_after(repeat, NOW) == datetime(2026, 10, 17, 10, 0)
    assert next_after(repeat, datetime(2026, 10, 17, 10, 0)) == datetime(2026, 10, 18, 10, 0)


def test_weekdays_skip_the_other_days():
    repeat = build_repeat({"time": "10:00", "weekdays": ["fri", "mon"]}, NOW)
    assert next_after(repeat, NOW) == datetime(2026, 10, 19, 10, 0)
    assert next_after(repeat, datetime(2026, 10, 19, 10, 0)) == datetime(2026, 10, 23, 10, 0)


def test_every_n_days_counts_from_the_day_it_was_set():
    repeat = build_repeat({"time": "08:00", "every_days": 3}, NOW)
    first = next_after(repeat, NOW)
    assert first == datetime(2026, 10, 20, 8, 0)
    assert next_after(repeat, first) == datetime(2026, 10, 23, 8, 0)


@pytest.mark.parametrize(
    "args",
    [
        {"every_minutes": 2},
        {"every_minutes": 30, "time": "10:00"},
        {"time": "25:00"},
        {"weekdays": ["mon"]},
        {"time": "10:00", "weekdays": ["mon"], "every_days": 2},
    ],
)
def test_an_inconsistent_recurrence_is_refused(args):
    with pytest.raises(ScheduleError):
        build_repeat(args, NOW)


def test_a_time_in_the_past_is_refused_with_the_current_time():
    with pytest.raises(ScheduleError, match="2026-10-17T09:00"):
        parse_at("2026-10-16T09:00", NOW)


def test_a_one_time_schedule_fires_once_and_is_removed(scheduler, clock):
    scheduler.add("check the limit", {"at": "2026-10-17T14:00"})
    clock.at(datetime(2026, 10, 17, 13, 59))
    assert not scheduler._take_due()
    clock.at(datetime(2026, 10, 17, 14, 0))
    assert scheduler._take_due()
    assert "check the limit" in scheduler.take_note()
    assert scheduler.list()["schedules"] == []


def test_a_recurring_schedule_advances_to_the_next_occurrence(scheduler, clock):
    scheduler.add("collect AI agent news", {"time": "10:00"})
    clock.at(datetime(2026, 10, 17, 10, 0))
    assert scheduler._take_due()
    [entry] = scheduler.list()["schedules"]
    assert entry["next_run"] == "2026-10-18 10:00"
    assert entry["runs"] == 1


def test_an_occurrence_missed_while_closed_fires_once_and_says_it_was_late(scheduler, clock):
    scheduler.add("weekly digest", {"time": "10:00", "weekdays": ["mon"]})
    clock.at(datetime(2026, 10, 27, 12, 0))
    assert scheduler._take_due()
    note = scheduler.take_note()
    assert "Kvist was not running" in note
    assert scheduler.list()["schedules"][0]["next_run"] == "2026-11-02 10:00"


def test_a_paused_schedule_does_not_fire_and_resumes_at_the_next_future_time(scheduler, clock):
    scheduler.add("daily", {"time": "10:00"})
    schedule_id = scheduler.list()["schedules"][0]["id"]
    scheduler.update(schedule_id, {"paused": True})
    clock.at(datetime(2026, 10, 18, 11, 0))
    assert not scheduler._take_due()
    scheduler.update(schedule_id, {"paused": False})
    assert scheduler.list()["schedules"][0]["next_run"] == "2026-10-19 10:00"


def test_update_replaces_the_timing_and_cancel_removes_the_schedule(scheduler):
    scheduler.add("daily", {"time": "10:00"})
    schedule_id = scheduler.list()["schedules"][0]["id"]
    updated = scheduler.update(schedule_id, {"task": "weekly", "time": "07:30", "weekdays": ["fri"]})["updated"]
    assert (updated["task"], updated["repeat"], updated["next_run"]) == ("weekly", "fri at 07:30", "2026-10-23 07:30")
    scheduler.cancel(schedule_id)
    assert scheduler.list()["schedules"] == []
    with pytest.raises(ScheduleError):
        scheduler.cancel(schedule_id)


def test_two_cores_on_one_workspace_run_an_occurrence_once(tmp_path, clock):
    first, second = Scheduler(tmp_path, clock=clock), Scheduler(tmp_path, clock=clock)
    first.add("once only", {"at": "2026-10-17T10:00"})
    clock.at(datetime(2026, 10, 17, 10, 0))
    assert first._take_due() is True
    assert second._take_due() is False


def test_a_wait_fires_in_memory_and_needs_a_core_that_can_wake(tmp_path, clock, scheduler):
    scheduler.start_wait(600, "limit resets")
    clock.at(datetime(2026, 10, 17, 9, 10))
    assert scheduler._take_due()
    assert "wait over" in scheduler.take_note()
    assert not (tmp_path / ".kvist" / "schedules.json").exists()
    with pytest.raises(ScheduleError, match="nothing can wake"):
        Scheduler(tmp_path, clock=clock).start_wait(60, "x")


def test_waits_end_with_the_conversation(scheduler):
    scheduler.start_wait(600, "limit resets")
    scheduler.clear_waits()
    assert scheduler.list()["schedules"] == []


def test_schedule_arguments_are_validated_by_the_parser():
    validate_action_args("schedule", {"op": "add", "task": "x", "time": "10:00", "weekdays": ["fri"]})
    validate_action_args("wait", {"seconds": 90, "reason": "x"})
    for action, args in [
        ("schedule", {"op": "add", "task": "x"}),
        ("schedule", {"op": "cancel"}),
        ("schedule", {"op": "add", "task": "x", "time": "10:00", "weekdays": ["someday"]}),
        ("wait", {"seconds": 0, "reason": "x"}),
        ("wait", {"seconds": 5}),
    ]:
        with pytest.raises(CommandValidationError):
            validate_action_args(action, args)


def test_a_woken_turn_gets_the_recorded_task(agent_factory, clock):
    prompts: list[str] = []
    agent = agent_factory(max_steps=4, stream=False)
    agent._state.native_tools = False
    agent._state.current_agent_role = "coder"
    agent._call_action_model = lambda prompt: prompts.append(prompt) or "Готово."
    agent.scheduler = Scheduler(agent.executor.workspace, clock=clock)
    agent.wake_listener = lambda: None
    try:
        agent.scheduler.start_wait(600, "retry the deploy")
        assert not agent.wake_due()
        clock.at(datetime(2026, 10, 17, 9, 10))
        agent.scheduler._take_due()
        assert agent.wake_due()
        assert agent.wake() == "Готово."
    finally:
        agent.wake_listener = None
    assert "retry the deploy" in prompts[0]
    assert not agent.wake_due()
