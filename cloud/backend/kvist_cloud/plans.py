# -*- coding: utf-8 -*-
"""Plans as constants. Prices are whole US dollars a month; hours are per billing period."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Plan:
    id: str
    price_usd: int
    hours: int
    vcpu: int
    memory_gb: int
    disk_gb: int
    max_parallel_tasks: int
    command_minutes: int
    # How long background jobs may outlive the agent's answer; 0 = killed with the turn.
    background_minutes: int
    # Trial only: its length and the plan it turns into.
    trial_days: int = 0
    converts_to: str = ""

    @property
    def seconds(self) -> int:
        return self.hours * 3600


TRIAL = Plan("trial", 0, 5, 2, 4, 10, 1, 30, 0, trial_days=7, converts_to="start")
START = Plan("start", 15, 50, 2, 4, 20, 1, 30, 30)
PRO = Plan("pro", 39, 150, 4, 8, 50, 3, 120, 120)
MAX = Plan("max", 99, 450, 4, 8, 150, 5, 360, 480)

PLANS: dict[str, Plan] = {plan.id: plan for plan in (TRIAL, START, PRO, MAX)}
PAID_PLANS = (START, PRO, MAX)

# One-off top-up sold as "buy more hours": one pack, one price, current period only.
HOUR_PACK_HOURS = 10
HOUR_PACK_PRICE_USD = 5


def get_plan(plan_id: str) -> Plan:
    plan = PLANS.get(plan_id)
    if plan is None:
        raise KeyError(f"unknown plan: {plan_id}")
    return plan


def public_catalog() -> list[dict[str, object]]:
    """The plan table the pricing page renders; the trial comes first because it is the default."""
    return [
        {
            "id": plan.id,
            "price_usd": plan.price_usd,
            "hours": plan.hours,
            "vcpu": plan.vcpu,
            "memory_gb": plan.memory_gb,
            "disk_gb": plan.disk_gb,
            "max_parallel_tasks": plan.max_parallel_tasks,
            "command_minutes": plan.command_minutes,
            "background_minutes": plan.background_minutes,
            "trial_days": plan.trial_days,
            "converts_to": plan.converts_to,
        }
        for plan in (TRIAL, START, PRO, MAX)
    ]
