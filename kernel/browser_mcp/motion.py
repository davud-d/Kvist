# -*- coding: utf-8 -*-
"""How a hand moves a mouse: a slightly bowed path, minimum-jerk speed, a small overshoot on long reaches."""
from __future__ import annotations

import math
import random

STEP_MS = 16


def travel_ms(distance: float) -> float:
    """Fitts-like: long reaches take longer, but not proportionally."""
    return min(1100.0, 140.0 + 115.0 * math.log2(1.0 + distance / 12.0))


def _min_jerk(t: float) -> float:
    return t * t * t * (10.0 - 15.0 * t + 6.0 * t * t)


def _bezier(p0, p1, p2, t: float) -> tuple[float, float]:
    u = 1.0 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def _segment(start, end, rng: random.Random, bow: float) -> list[tuple[float, float]]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    distance = math.hypot(dx, dy)
    steps = max(2, round(travel_ms(distance) / STEP_MS))
    # The control point sits off the straight line: wrists swing in arcs.
    normal = (-dy / distance, dx / distance) if distance else (0.0, 0.0)
    offset = distance * bow * rng.choice((-1.0, 1.0))
    control = (start[0] + dx * 0.5 + normal[0] * offset, start[1] + dy * 0.5 + normal[1] * offset)
    return [_bezier(start, control, end, _min_jerk(i / steps)) for i in range(1, steps + 1)]


def path(start: tuple[float, float], end: tuple[float, float], rng: random.Random | None = None) -> list[tuple[float, float]]:
    """Points to visit every STEP_MS after ``start``; the last one is exactly ``end``."""
    rng = rng or random.Random()
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance < 2:
        return [end]
    if distance < 300:
        return _segment(start, end, rng, rng.uniform(0.04, 0.14))
    # A long reach lands a few pixels past the target and corrects back.
    over = rng.uniform(4.0, 9.0) / distance
    beyond = (end[0] + (end[0] - start[0]) * over, end[1] + (end[1] - start[1]) * over)
    return _segment(start, beyond, rng, rng.uniform(0.04, 0.12)) + _segment(beyond, end, rng, 0.0)
