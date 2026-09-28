# -*- coding: utf-8 -*-
from __future__ import annotations

from role_router import has_change_verb

# Keyword guesses about what a request expects back. They only relax or add
# completion accounting; none of them may block a write by itself.
_REPORT_MARKERS: tuple[str, ...] = (
    # RU
    "скажи", "скажите", "опиши", "опишите", "объясни", "объясните",
    "перечисли", "перечислите", "сравни", "сравните",
    "проанализируй", "проанализируйте", "оцени", "оцените",
    "предложи", "предложите", "посоветуй", "порекомендуй", "расскажи",
    "разбери", "какие бы", "что бы ты", "что бы вы", "мнение",
    "что не так", "узкие места", "где узкие", "что думаешь", "стоит ли",
    "как лучше", "в чём проблема", "в чем проблема",
    "улучшить", "улучшил бы", "что улучшить",
    # EN
    "explain", "describe", "summarize", "summarise", "analyze", "analyse",
    "compare", "evaluate", "assess", "recommend", "suggest", "review",
    "what would you", "how would you", "which parts", "what changes",
    "what's wrong", "whats wrong", "wrong with", "what do you think",
    "thoughts on", "how can i improve", "what could be better",
)


# The words that ask for something new to be made; the router's change verbs
# cover every other kind of edit.
_CREATE_MARKERS: tuple[str, ...] = (
    "create ", "build ", "generate ", "scaffold", "bootstrap", "создай", "сгенерируй",
)

_COMPLEX_KEYWORDS: tuple[str, ...] = (
    "architect",
    "refactor",
    "production",
    "system",
    "agent",
    "multiple files",
    "project",
    "analysis",
    "memory",
    "planning",
    "improv",
    "upgrade",
)


def is_create_goal(user_goal: str) -> bool:
    lowered = user_goal.casefold()
    return any(marker in lowered for marker in _CREATE_MARKERS)


def asks_for_explanation(user_goal: str) -> bool:
    """The request expects a substantive spoken answer (an explanation, a review)."""
    lowered = user_goal.casefold()
    return any(marker in lowered for marker in _REPORT_MARKERS)


def infer_task_kind(user_goal: str) -> str:
    """Heuristic 'change' / 'report' / 'action' classification of a request.

    A request that changes files is "change" even when it also asks for an
    explanation: what it must deliver first is the change.
    """
    if has_change_verb(user_goal) or is_create_goal(user_goal):
        return "change"
    return "report" if asks_for_explanation(user_goal) else "action"


def is_complex_goal(user_goal: str) -> bool:
    """Whether the request starts on the smart model instead of the fast one."""
    lowered = user_goal.casefold()
    return len(user_goal) > 180 or any(keyword in lowered for keyword in _COMPLEX_KEYWORDS)
