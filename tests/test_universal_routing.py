# -*- coding: utf-8 -*-
"""Phase 3 of the universal-role work: router changes and their tests.

Two things changed in role_router.py:
  - the ultimate fallback (used when nothing else — not a direct rule, not
    the LLM, not a keyword — could classify the request) is now "universal"
    instead of "chat" (role_router._DEFAULT_ROLE);
  - a new deterministic direct rule fires "universal" for a request that
    mixes an explanation verb with a change verb ("explain X and fix it",
    "объясни ... и почини ...").

Everything else in the deterministic pipeline (_match_direct_rule's
devops/coder/reader branches, the >=2-hit chat pattern) is untouched. The
snapshot below pins that: every one of these confidently-classified
requests must resolve to exactly the role it did before this work, via the
same direct rule.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import role_router
from role_router import _match_direct_rule, _parse_llm_role, _response_names_role


# ── Snapshot: requests that must keep routing exactly as before ──────────

_SNAPSHOT_UNCHANGED: tuple[tuple[str, str], ...] = (
    ("Fix the bug in main.py", "coder"),
    ("создай скрипт для очистки", "coder"),
    ("напиши файл README.md", "coder"),
    ("Refactor role_router.py", "coder"),
    ("Deploy this project on my VPS", "devops"),
    ("разверни проект на сервере", "devops"),
    ("restart the systemd service and check logs", "devops"),
    ("Configure nginx reverse proxy", "devops"),
    ("ничего не меняй, покажи git status", "reader"),
    ("Найди конфиг nginx, прочитай и скажи что улучшить, но ничего не меняй", "reader"),
)


@pytest.mark.parametrize("goal, expected_role", _SNAPSHOT_UNCHANGED)
def test_confident_direct_rule_routing_is_unchanged(goal, expected_role):
    """These are all still decided by _match_direct_rule alone (no LLM, no
    keyword fallback, no default) — the part of the router this work did not
    touch for devops/coder/reader."""
    match = _match_direct_rule(goal)
    assert match is not None, f"{goal!r} no longer matches a direct rule at all"
    assert match[0] == expected_role


# ── New: requests that now land on "universal" ────────────────────────────

_NOW_UNIVERSAL_DIRECT: tuple[str, ...] = (
    # The new mixed explain+fix direct rule.
    "Объясни, как работает auth.py, и почини баг в нём",
    "Explain how auth.py works and fix the bug in it",
)

_NOW_UNIVERSAL_KEYWORD_FALLBACK: tuple[str, ...] = (
    # These already existed as regression tests for _keyword_route's
    # catch-all (see test_role_router.py) — no named file, no chat keyword,
    # no coder/devops keyword, so they fall through every specific check to
    # the ultimate default, which used to be "chat" and is now "universal".
    "change a function",
    "How do I add authentication to Flask?",
    "Why do people say e.g. that Flask is small?",
    "What does example.com resolve to?",
    "delete https://example.com/x",
    "Why do people usually name their config file .env instead of settings.ini?",
)


@pytest.mark.parametrize("goal", _NOW_UNIVERSAL_DIRECT)
def test_mixed_explain_and_fix_routes_directly_to_universal(goal):
    match = _match_direct_rule(goal)
    assert match is not None
    assert match[0] == "universal"


@pytest.mark.parametrize("goal", _NOW_UNIVERSAL_KEYWORD_FALLBACK)
def test_keyword_fallback_catch_all_routes_to_universal(goal):
    assert _match_direct_rule(goal) is None, f"{goal!r} now matches a direct rule unexpectedly"
    assert role_router._keyword_route(goal) == "universal"


def test_eight_or_more_universal_examples_are_covered():
    """router_rules requires at least 8 examples that now go to universal."""
    total = len(_NOW_UNIVERSAL_DIRECT) + len(_NOW_UNIVERSAL_KEYWORD_FALLBACK)
    assert total >= 8


def test_ten_or_more_unchanged_examples_are_covered():
    """router_rules requires at least 10 examples that keep their old route."""
    assert len(_SNAPSHOT_UNCHANGED) >= 10


# ── LLM router: response parsing and threshold, no real API calls ────────


class _FakeRouterClient:
    def __init__(self, content: str):
        self._content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


def test_llm_router_reply_of_universal_is_recognized_and_parsed():
    assert _response_names_role("universal") is True
    assert _parse_llm_role("universal") == "universal"
    assert _parse_llm_role("Role: universal") == "universal"
    assert _parse_llm_role("**universal**") == "universal"


def test_route_role_decision_with_llm_accepts_a_universal_verdict(monkeypatch):
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
    client = _FakeRouterClient("universal")

    decision = role_router.route_role_decision_with_llm(
        "мне тут в коде что-то не нравится, но не знаю что", client, "m"
    )

    assert decision.role == "universal"
    assert decision.source == "llm"


def test_a_noisy_llm_reply_mentioning_universal_among_others_is_rejected_by_length_cap():
    """_response_names_role caps reply length so a full-prose answer that
    happens to use the word "universal" is not mistaken for a classification
    — same protection the other four role words already had."""
    long_reply = (
        "This library aims to be a universal solution for many problems, "
        "and honestly that is a much longer answer than a router should give."
    )
    assert _response_names_role(long_reply) is False
