# -*- coding: utf-8 -*-
"""Which working styles the executors of a best-of run take, in order.

Executors that all think alike produce one answer N times. Each gets a different working style
(the agent styles of ``kernel/prompts/styles/agents``), so their results differ in how they trade
speed against care. The ladder is fixed and ordered: a run of N executors uses the first N.
"""
from __future__ import annotations

from kernel.config.user_config import get_style_options, style_slug

MIN_EXECUTORS = 2
MAX_EXECUTORS = 6
DEFAULT_EXECUTORS = 4

STYLE_LADDER = ("senior-dev", "minimalist", "paranoid", "fast-and-dirty", "perfectionist", "architect")


def _both(english: str, russian: str) -> str:
    return f"{english} / {russian}"


def styles_for(count: int) -> tuple[str, ...]:
    """The first ``count`` styles of the ladder; ``count`` is 2 to 6."""
    if type(count) is not int or not MIN_EXECUTORS <= count <= MAX_EXECUTORS:
        raise ValueError(_both(
            f"Best-of runs {MIN_EXECUTORS} to {MAX_EXECUTORS} executors, not {count!r}",
            f"Best-of запускает от {MIN_EXECUTORS} до {MAX_EXECUTORS} исполнителей, а не {count!r}",
        ))
    return STYLE_LADDER[:count]


def style_name(slug: str) -> str:
    """The name the session document and the ``/style`` picker use for a ladder slug (``fast-and-dirty`` -> ``Fast-and-dirty``)."""
    for name in get_style_options():
        if style_slug(name) == slug:
            return name
    raise ValueError(_both(f"No agent style {slug!r}", f"Нет стиля агента {slug!r}"))
