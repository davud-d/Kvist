# -*- coding: utf-8 -*-
"""Pointer paths that do not look like a script: no two are alike, the last point is exact.

Pure functions (no X11), so the shape of a movement can be tested without a screen. A path is a list of
``(x, y, delay)``: the pointer goes to ``(x, y)``, then the caller waits ``delay`` seconds.
"""
from __future__ import annotations

import math
import random

MIN_SECONDS = 0.18
MAX_SECONDS = 1.1
# A human hand needs about this much time per doubling of distance (Fitts); the constants are rounded.
_BASE_SECONDS = 0.12
_PER_BIT_SECONDS = 0.11


def move_duration(distance: float, rng: random.Random, *, slow: bool = False) -> float:
    """Seconds for a movement over ``distance`` pixels; every call differs by a few percent to tens of percent."""
    seconds = _BASE_SECONDS + _PER_BIT_SECONDS * math.log2(1.0 + distance / 24.0)
    seconds *= rng.uniform(0.8, 1.25) * (1.6 if slow else 1.0)
    return min(max(seconds, MIN_SECONDS), MAX_SECONDS)


def pointer_path(start: tuple[int, int], end: tuple[int, int], rng: random.Random, *, slow: bool = False
                 ) -> list[tuple[int, int, float]]:
    """A curved path from ``start`` to exactly ``end``: eased start and stop, a bend to one side that is
    different every time, a speed that varies along the way and a small hand tremor that dies out near the target."""
    (x0, y0), (x1, y1) = start, end
    distance = math.hypot(x1 - x0, y1 - y0)
    if distance < 1.5:
        return [(x1, y1, 0.0)]
    duration = move_duration(distance, rng, slow=slow)
    steps = max(6, int(duration * rng.uniform(70, 130)))
    normal = ((y0 - y1) / distance, (x1 - x0) / distance)
    bend = distance * rng.uniform(-0.12, 0.12)
    # The two control points sit at uneven fractions, so the curve is not a symmetric arc.
    c1 = _offset(x0, y0, x1, y1, rng.uniform(0.2, 0.4), normal, bend * rng.uniform(0.5, 1.2))
    c2 = _offset(x0, y0, x1, y1, rng.uniform(0.6, 0.85), normal, bend * rng.uniform(0.2, 0.9))
    wobble = rng.uniform(0.4, 1.6)
    phase = rng.uniform(0, math.tau)
    points: list[tuple[int, int, float]] = []
    previous_time = 0.0
    previous_clock = 0.0
    for index in range(1, steps + 1):
        progress = index / steps
        eased = _min_jerk(progress)
        # Speed drifts: the clock is bent a little, never backwards.
        drift = 0.015 * math.sin(progress * math.tau * rng.uniform(1.0, 2.0) + phase) * math.sin(math.pi * progress)
        clock = min(max(eased + drift, previous_clock), 1.0)
        previous_clock = clock
        bx, by = _bezier((x0, y0), c1, c2, (x1, y1), clock)
        fade = math.sin(math.pi * min(progress, 1.0)) ** 0.5 if progress < 1 else 0.0
        bx += rng.gauss(0, 0.35) * wobble * fade
        by += rng.gauss(0, 0.35) * wobble * fade
        elapsed = duration * eased
        delay = max(0.0, (elapsed - previous_time) * rng.uniform(0.7, 1.3))
        previous_time = elapsed
        points.append((round(bx), round(by), delay))
    points[-1] = (x1, y1, 0.0)
    return _without_repeats(points)


def settle_pause(rng: random.Random) -> float:
    """The beat between arriving and pressing: a hand does not click on the frame it stops."""
    return rng.uniform(0.04, 0.16)


def click_hold(rng: random.Random) -> float:
    return rng.uniform(0.045, 0.12)


def double_click_gap(rng: random.Random) -> float:
    return rng.uniform(0.06, 0.15)


def typing_delay(rng: random.Random, previous: str, char: str) -> float:
    """Per-key delay: quicker inside a word, a little longer after a space or punctuation."""
    base = rng.uniform(0.012, 0.045)
    if previous in " .,;:!?\n":
        base += rng.uniform(0.01, 0.05)
    if char == "\n":
        base += rng.uniform(0.02, 0.06)
    return base


def _min_jerk(t: float) -> float:
    return t * t * t * (10 + t * (-15 + 6 * t))


def _bezier(p0, p1, p2, p3, t: float) -> tuple[float, float]:
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def _offset(x0, y0, x1, y1, fraction: float, normal: tuple[float, float], bend: float) -> tuple[float, float]:
    return (x0 + (x1 - x0) * fraction + normal[0] * bend, y0 + (y1 - y0) * fraction + normal[1] * bend)


def _without_repeats(points: list[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Drop a step that lands on the pixel before it, keeping its time so the pace does not change."""
    result: list[tuple[int, int, float]] = []
    carried = 0.0
    for x, y, delay in points:
        if result and (x, y) == (result[-1][0], result[-1][1]):
            carried += delay
            continue
        if result and carried:
            px, py, pd = result[-1]
            result[-1] = (px, py, pd + carried)
            carried = 0.0
        result.append((x, y, delay))
    if carried and result:
        px, py, pd = result[-1]
        result[-1] = (px, py, pd + carried)
    return result
