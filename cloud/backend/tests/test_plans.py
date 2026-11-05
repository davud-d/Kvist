# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kvist_cloud import plans


def test_plan_table_matches_the_offer():
    trial, start, pro, maximum = (plans.get_plan(name) for name in ("trial", "start", "pro", "max"))
    assert (trial.hours, trial.trial_days, trial.converts_to, trial.background_minutes) == (5, 7, "start", 0)
    assert (start.price_usd, start.hours, start.disk_gb, start.background_minutes) == (15, 50, 20, 30)
    assert (pro.price_usd, pro.hours, pro.vcpu, pro.memory_gb, pro.max_parallel_tasks) == (39, 150, 4, 8, 3)
    assert (pro.command_minutes, pro.background_minutes) == (120, 120)
    assert (maximum.price_usd, maximum.hours, maximum.disk_gb, maximum.max_parallel_tasks) == (99, 450, 150, 5)
    assert (maximum.command_minutes, maximum.background_minutes) == (360, 480)


def test_trial_is_first_in_the_catalog():
    assert [row["id"] for row in plans.public_catalog()] == ["trial", "start", "pro", "max"]


def test_unknown_plan_is_an_error():
    with pytest.raises(KeyError):
        plans.get_plan("enterprise")
