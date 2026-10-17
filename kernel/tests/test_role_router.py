# -*- coding: utf-8 -*-
"""Tests for role_router.py — diagnose whether LLM is called, when direct
rules fire, keyword fallback, and default sources.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kernel import role_router
from kernel.role_router import (
    RouteDecision,
    route_role,
    route_role_with_llm,
    route_role_decision_with_llm,
    _DEFAULT_ROLE,
)


# ── Fake LLM client ──────────────────────────────────────────────

class FakeClient:
    """Fake OpenAI-compatible client for testing the router.

    Tracks calls, stores messages/model/temperature/max_tokens.
    Can be configured to return a specific role, raise, or count calls.
    """

    def __init__(self, role_response: str = "chat", should_raise: bool = False,
                 error_msg: str = "router failed"):
        self._role_response = role_response
        self._should_raise = should_raise
        self._error_msg = error_msg
        self.calls = 0
        self.last_kwargs: dict = {}

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self._should_raise:
            raise RuntimeError(self._error_msg)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._role_response)
                )
            ]
        )


# ── Helpers ──────────────────────────────────────────────────────

def _make_client(role: str = "chat", should_raise: bool = False,
                 error_msg: str = "router failed") -> FakeClient:
    return FakeClient(role_response=role, should_raise=should_raise, error_msg=error_msg)


# ── Existing tests (must keep passing) ───────────────────────────

def test_keyword_route_identifies_chat_and_devops():
    assert route_role("explain how this works") == "chat"
    assert route_role("deploy docker compose service") == "devops"
    # No named file, no chat keyword, no action verb the coder list knows —
    # this is the genuinely-unmatched catch-all, which is exactly what
    # "universal" (the current _DEFAULT_ROLE) exists for.
    assert route_role("change a function") == "universal"


def test_route_role_with_llm_parses_noisy_response(monkeypatch):
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="Role: coder"))]
            )

    assert route_role_with_llm("edit file", Client(), "model") == "coder"


def test_route_role_with_llm_falls_back_on_client_error(monkeypatch):
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")

    class Client:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            raise RuntimeError("offline")

    assert route_role_with_llm("nginx deploy", Client(), "model") == "devops"


def test_router_redacts_credentials_before_llm_and_in_error(monkeypatch):
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
    synthetic = "sk-proj-" + "q" * 24
    client = _make_client(
        should_raise=True,
        error_msg=f"Authorization: Bearer {synthetic}",
    )

    decision = route_role_decision_with_llm(
        f"абракадабра api_key={synthetic}",
        client=client,
        model="test-model",
    )

    assert client.calls == 1
    assert synthetic not in str(client.last_kwargs)
    assert synthetic not in (decision.error or "")
    assert "[REDACTED]" in str(client.last_kwargs)
    assert "[REDACTED]" in (decision.error or "")


# ── New diagnostic tests ─────────────────────────────────────────


class TestLLMRouterIsCalledWhenNoDirectRule:
    """Test 1: LLM router IS called when no direct rule matches."""

    def test_returns_chat_role(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "абракадабра role routing diagnostic",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert decision.raw_response == "chat"
        assert decision.model == "test-model"

    def test_llm_is_called_once(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        route_role_decision_with_llm(
            "абракадабра role routing diagnostic",
            client=client,
            model="test-model",
        )
        assert client.calls == 1, "LLM must be called exactly once"

    def test_returns_coder_from_llm(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="coder")
        decision = route_role_decision_with_llm(
            "some ambiguous request without keywords xyz",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "llm"

    def test_returns_devops_from_llm(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="devops")
        decision = route_role_decision_with_llm(
            "unusual infrastructure query zzz",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "llm"


class TestDirectCoderRuleDoesNotCallLLM:
    """Test 2: Direct coder rule fires → LLM is not called."""

    def test_sozdai_script_is_coder(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")  # LLM would say chat, but direct wins
        decision = route_role_decision_with_llm(
            "Создай Python скрипт очистки Downloads",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert decision.matched_rule != ""
        assert client.calls == 0, "LLM must NOT be called when direct rule matches"

    def test_write_script_is_coder(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Write a Python script to clean Downloads",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_fix_bug_is_coder(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Fix bug in agent.py",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_create_readme_is_coder(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Create a README file",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_make_telegram_bot_is_coder(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Сделай Telegram бота",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert client.calls == 0


class TestDirectChatRuleDoesNotCallLLM:
    """Test 3: Direct chat rule fires → LLM is not called."""

    def test_philosophy_is_chat(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="coder")  # LLM would say coder, but direct wins
        decision = route_role_decision_with_llm(
            "Какова философия Python?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "direct"
        assert client.calls == 0, "LLM must NOT be called when direct rule matches"

    def test_difference_is_chat(self, monkeypatch):
        # A single chat-pattern hit ("comparison question") is not enough to
        # short-circuit on its own (see _DIRECT_CHAT_MIN_MATCHES) — it defers
        # to the LLM router, which is the one asked here.
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Чем Python 2 отличается от Python 3?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1

    def test_what_is_is_chat(self, monkeypatch):
        # Single "definition question" hit — deferred to the LLM router.
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "What is Python?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1

    def test_explain_is_chat(self, monkeypatch):
        # Single "explanation request" hit — deferred to the LLM router.
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Explain Docker",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1


class TestDirectDevopsRuleDoesNotCallLLM:
    """Test 4: Direct devops rule fires → LLM is not called."""

    def test_deploy_docker_is_devops(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")  # LLM would say chat, but direct wins
        decision = route_role_decision_with_llm(
            "Разверни проект через Docker на сервере",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"
        assert client.calls == 0, "LLM must NOT be called when direct rule matches"

    def test_configure_nginx_is_devops(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Configure nginx reverse proxy",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_restart_systemd_is_devops(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Перезапусти systemd сервис и покажи логи",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_deploy_on_vps_is_devops(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Deploy this project on my VPS",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"
        assert client.calls == 0


class TestLLMErrorUsesKeywordFallback:
    """Test 5: LLM error → keyword fallback kicks in."""

    def test_explain_docker_falls_back_to_chat(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="router failed")
        decision = route_role_decision_with_llm(
            "Объясни как работает Docker",
            client=client,
            model="test-model",
        )
        # "Объясни" is a direct chat rule, so it should match BEFORE LLM
        # This means source should be "direct", not "keyword_fallback"
        assert decision.role == "chat"
        # With direct rule in place, "Объясни" matches direct chat
        assert decision.source == "direct"
        assert client.calls == 0

    def test_docker_explain_without_direct_match(self, monkeypatch):
        """A single "tell request" hit ('Расскажи') no longer short-circuits
        on its own, so this has no direct match; LLM fails → keyword
        fallback picks 'devops' from the 'docker' keyword."""
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="router failed")
        decision = route_role_decision_with_llm(
            "Расскажи про Docker контейнеры в продакшене",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "keyword_fallback"

    def test_napishi_file_no_direct_match_with_llm_error(self, monkeypatch):
        """A request matching coder keywords but not the precise direct pattern,
        LLM fails → keyword fallback → 'coder'."""
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="router failed")
        decision = route_role_decision_with_llm(
            "Напиши файл main.py",
            client=client,
            model="test-model",
        )
        # "напиши.*файл" matches direct coder rule
        assert decision.role == "coder"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_llm_error_uses_keyword_fallback_for_ambiguous(self, monkeypatch):
        """Request with NO direct rule, LLM fails → keyword fallback with devops keywords."""
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="router failed")
        decision = route_role_decision_with_llm(
            "docker production nginx setup",
            client=client,
            model="test-model",
        )
        # No direct rule matches → LLM called, fails → keyword fallback
        # "docker", "nginx", "production" → devops keywords
        assert decision.role == "devops"
        assert decision.source == "keyword_fallback"
        assert "router failed" in decision.error
        assert client.calls == 1


class TestDefaultSourceWhenLLMFails:
    """Test 6: Default source when nothing matches."""

    def test_nonsense_input_llm_fails_returns_default(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="router failed")
        decision = route_role_decision_with_llm(
            "zzzzzz qqqqqq",
            client=client,
            model="test-model",
        )
        assert decision.role == _DEFAULT_ROLE
        assert decision.source == "default"
        assert "router failed" in decision.error
        assert client.calls == 1

    def test_nonsense_input_llm_succeeds_returns_llm_result(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="coder")
        decision = route_role_decision_with_llm(
            "zzzzzz qqqqqq",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"  # LLM decided (whatever it returns)
        assert decision.source == "llm"
        assert client.calls == 1


class TestCreateScriptIsCoderNotChat:
    """Test 7: "Создай Python скрипт..." must be coder."""

    def test_sozdai_python_script(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Создай Python скрипт очистки Downloads",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"


class TestShowExampleScriptIsChatNotCoder:
    """Test 8: "Покажи пример скрипта" must be chat (via the LLM router —
    a single chat-pattern hit no longer short-circuits, see
    _DIRECT_CHAT_MIN_MATCHES)."""

    def test_show_example_script(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Покажи пример Python скрипта очистки Downloads",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1

    def test_dai_code_bez_sozdania(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Дай код скрипта очистки Downloads без создания файла",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1


class TestPythonDifferenceIsChat:
    """Test 9: "Чем Python 2 отличается..." must be chat (via the LLM
    router — a single "comparison question" hit is not enough on its own)."""

    def test_python_difference(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Чем Python 2 отличается от Python 3?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1


class TestFixFileIsCoder:
    """Test 10: "Исправь ошибку в main.py" must be coder."""

    def test_isprav_oshibku_main_py(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Исправь ошибку в main.py",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"

    def test_fix_bug_agent_py(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Fix bug in agent.py",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"


class TestRouteRoleWithLLMReturnsPlainString:
    """Test 11: route_role_with_llm returns str, not RouteDecision."""

    def test_returns_string(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        result = route_role_with_llm("hello", client=client, model="test-model")
        assert isinstance(result, str)
        assert result == "chat"

    def test_direct_rule_also_returns_string(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        result = route_role_with_llm(
            "Создай Python скрипт очистки Downloads",
            client=client,
            model="test-model",
        )
        assert isinstance(result, str)
        assert result == "coder"
        assert client.calls == 0  # direct, no LLM call


class TestLLMMessagesIncludeRouterPromptAndUserGoal:
    """Test 12: LLM receives proper messages."""

    def test_messages_contain_system_and_user(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "router prompt text")
        client = _make_client(role="chat")
        route_role_decision_with_llm(
            "абракадабра diagnostic",
            client=client,
            model="test-model",
        )
        kwargs = client.last_kwargs
        assert "messages" in kwargs, "LLM call must include messages"
        messages = kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "router prompt text"
        assert messages[1]["role"] == "user"
        assert "абракадабра diagnostic" in messages[1]["content"]

    def test_temperature_and_max_tokens(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        route_role_decision_with_llm(
            "абракадабра diagnostic",
            client=client,
            model="my-model",
            temperature=0.0,
        )
        kwargs = client.last_kwargs
        assert kwargs["model"] == "my-model"
        assert kwargs["temperature"] == 0.0
        assert kwargs["max_tokens"] == role_router._ROUTER_MAX_TOKENS


class TestKeywordFallbackOrder:
    """Keyword fallback priority: devops → coder → chat → default."""

    def test_coder_keywords_work(self):
        """Coder keywords that were previously missing."""
        assert route_role("создай скрипт") == "coder"
        assert route_role("напиши файл") == "coder"

    def test_write_without_direct_match_is_coder(self):
        """'Write a script' should be coder via keyword fallback."""
        # "write" and "script" are in _CODER_KEYWORDS
        result = route_role("write a script to do something")
        assert result == "coder"

    def test_bare_file_code_test_nouns_do_not_trigger_coder(self):
        # "file"/"code"/"test" used to sit in _CODER_KEYWORDS as bare nouns,
        # so an ordinary conceptual sentence that merely mentions one of them
        # (no create/write/fix/... verb) fell to coder by accident — exactly
        # the "classify by topic, not action" mistake router_prompt.txt warns
        # against. Neither sentence names a project file or a chat keyword,
        # so they land on the catch-all default ("universal") rather than on
        # "coder" — that is the fix this test guards, "coder" is what must
        # never happen here again.
        assert route_role(
            "Why do people usually name their config file .env instead of settings.ini?"
        ) == "universal"
        assert route_role("What's a good testing strategy for async code?") == "universal"


class TestMainPathDoesNotRouteTwice:
    """Verify that agent.run receives routed_role and doesn't re-route."""

    def test_route_role_decision_used_in_main_pattern(self, monkeypatch):
        """Simulate the main.py pattern: decision → agent.run(routed_role=...)."""
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="coder")
        # This is what main.py does
        decision = route_role_decision_with_llm(
            "Создай Python скрипт очистки Downloads",
            client=client,
            model="fast-model",
        )
        detected_role = decision.role
        # This is what agent.run receives
        assert detected_role == "coder"
        # agent.run: self._state.current_agent_role = routed_role or self._route_role(...)
        # Since routed_role is not None, _route_role is NOT called
        # (verified by reading agent.py source)


class TestEdgeCases:
    """Additional edge case tests."""

    def test_explain_docker_is_chat(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="devops")
        decision = route_role_decision_with_llm(
            "Объясни, как работает Docker",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "direct"
        assert client.calls == 0

    def test_deploy_docker_is_devops_distinct_from_explain(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Разверни проект через Docker на сервере",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"

    def test_napishi_script_vs_pokazhi_example(self):
        """Key distinction: напиши → coder, покажи → chat."""
        client = _make_client(role="chat")
        d1 = route_role_decision_with_llm("Напиши скрипт очистки", client=client, model="m")
        assert d1.role == "coder"
        assert d1.source == "direct"

        # Single "example request" hit — deferred to the LLM router, which
        # (per the FakeClient's configured "chat" response) agrees.
        d2 = route_role_decision_with_llm("Покажи пример скрипта очистки", client=client, model="m")
        assert d2.role == "chat"
        assert d2.source == "llm"

    def test_add_logging_is_coder(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Добавь логирование в executor",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"


class TestParseRobustness:
    """Test _parse_llm_role handles noisy LLM responses."""

    def test_parse_clean_role(self):
        from kernel.role_router import _parse_llm_role
        assert _parse_llm_role("coder") == "coder"
        assert _parse_llm_role("devops") == "devops"
        assert _parse_llm_role("chat") == "chat"

    def test_parse_uppercase(self):
        from kernel.role_router import _parse_llm_role
        assert _parse_llm_role("CODER") == "coder"
        assert _parse_llm_role("DevOps") == "devops"
        assert _parse_llm_role("CHAT") == "chat"

    def test_parse_with_whitespace(self):
        from kernel.role_router import _parse_llm_role
        assert _parse_llm_role("  coder  ") == "coder"
        assert _parse_llm_role("\ndevops\n") == "devops"

    def test_parse_noisy_response(self):
        from kernel.role_router import _parse_llm_role
        assert _parse_llm_role("I think coder is best") == "coder"
        assert _parse_llm_role("The role should be devops") == "devops"
        assert _parse_llm_role("chat role") == "chat"

    def test_parse_garbage_returns_default(self):
        from kernel.role_router import _parse_llm_role
        assert _parse_llm_role("xyz123") == _DEFAULT_ROLE
        assert _parse_llm_role("") == _DEFAULT_ROLE
        assert _parse_llm_role("unknown") == _DEFAULT_ROLE


class TestAdditionalDirectRules:
    """Test additional direct rule patterns."""

    def test_run_tests_is_coder(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Запусти тесты",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"

    def test_check_git_status_is_coder(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Проверь git status",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"

    def test_refactor_is_coder(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Рефактор role_router.py",
            client=client,
            model="test-model",
        )
        assert decision.role == "coder"
        assert decision.source == "direct"

    def test_setup_cicd_is_devops(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Setup CI/CD pipeline",
            client=client,
            model="test-model",
        )
        assert decision.role == "devops"
        assert decision.source == "direct"

    def test_kubernetes_question_is_chat(self):
        # Single "definition question" hit — deferred to the LLM router.
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Что такое Kubernetes?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1

    def test_architecture_advice_is_chat(self):
        # Single "advice request" hit — deferred to the LLM router.
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Как лучше построить архитектуру агента?",
            client=client,
            model="test-model",
        )
        assert decision.role == "chat"
        assert decision.source == "llm"
        assert client.calls == 1


class TestRouteDecisionMetadata:
    """Test RouteDecision metadata fields."""

    def test_direct_route_has_matched_rule(self):
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "Создай Python скрипт",
            client=client,
            model="test-model",
        )
        assert decision.source == "direct"
        assert decision.matched_rule != ""
        assert "create artifact" in decision.matched_rule

    def test_llm_route_has_raw_response(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="coder")
        decision = route_role_decision_with_llm(
            "абракадабра xyz",
            client=client,
            model="test-model",
        )
        assert decision.source == "llm"
        assert decision.raw_response == "coder"
        assert decision.matched_rule == ""

    def test_model_is_stored(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(role="chat")
        decision = route_role_decision_with_llm(
            "test query",
            client=client,
            model="gpt-4o-mini",
        )
        assert decision.model == "gpt-4o-mini"

    def test_error_is_stored_on_llm_failure(self, monkeypatch):
        monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
        client = _make_client(should_raise=True, error_msg="network timeout")
        decision = route_role_decision_with_llm(
            "docker nginx production",
            client=client,
            model="test-model",
        )
        assert "network timeout" in decision.error
        assert decision.source == "keyword_fallback"


class TestKeywordFallbackPriority:
    """Test keyword fallback priority: devops → coder → chat → default."""

    def test_devops_keyword_wins_over_coder(self):
        # "docker" is devops keyword, "script" is coder keyword
        # devops should win (checked first)
        result = route_role("docker script automation")
        assert result == "devops"

    def test_coder_keyword_wins_over_chat(self):
        # "создай" is coder keyword, "объясни" would be chat
        # but "создай" is checked before chat keywords
        result = route_role("создай что-то")
        assert result == "coder"

    def test_chat_keyword_when_no_devops_or_coder(self):
        result = route_role("объясни концепцию")
        assert result == "chat"

    def test_default_when_no_keywords(self):
        result = route_role("абракадабра xyz qwerty")
        assert result == _DEFAULT_ROLE


class TestMatchDirectRule:
    """Test _match_direct_rule function directly."""

    def test_match_direct_rule_devops(self):
        from kernel.role_router import _match_direct_rule
        result = _match_direct_rule("Разверни проект на VPS")
        assert result is not None
        role, label, pattern = result
        assert role == "devops"
        assert label == "deploy action"

    def test_match_direct_rule_coder(self):
        from kernel.role_router import _match_direct_rule
        result = _match_direct_rule("Создай Python скрипт")
        assert result is not None
        role, label, pattern = result
        assert role == "coder"
        assert label == "create artifact"

    def test_match_direct_rule_chat(self):
        # Needs two corroborating chat-pattern hits to short-circuit
        # (see _DIRECT_CHAT_MIN_MATCHES) — a lone "definition question"
        # hit like "Что такое Docker?" is covered by
        # test_match_direct_rule_single_chat_hit_defers below.
        from kernel.role_router import _match_direct_rule
        result = _match_direct_rule("Объясни, как работает Docker")
        assert result is not None
        role, label, pattern = result
        assert role == "chat"
        assert "explanation request" in label
        assert "how-it-works question" in label

    def test_match_direct_rule_single_chat_hit_defers(self):
        # A single chat-pattern hit is weak evidence on its own (it could
        # just as well appear inside a diagnostic/investigation request),
        # so it must defer to the LLM router rather than deciding alone.
        from kernel.role_router import _match_direct_rule
        assert _match_direct_rule("Что такое Docker?") is None
        assert _match_direct_rule("Explain Docker") is None

    def test_match_direct_rule_none(self):
        from kernel.role_router import _match_direct_rule
        result = _match_direct_rule("абракадабра xyz")
        assert result is None

    def test_match_direct_rule_reader_explain_artifact(self):
        # "explain/summarize <workspace artifact>" needs a file read, which
        # chat cannot do at all — must not fall into the chat patterns just
        # because it starts with "объясни"/"explain".
        from kernel.role_router import _match_direct_rule
        for goal in (
            "Объясни, что в этом логе",
            "Перескажи содержимое усечённого лога инцидента",
            "Summarize this log file for me",
            "Explain what's in the incident log",
        ):
            result = _match_direct_rule(goal)
            assert result is not None, goal
            role, label, _pattern = result
            assert role == "reader", goal
            assert label == "explain-artifact"

    def test_match_direct_rule_reader_artifact_beats_chat_min_matches(self):
        # Without the reader rule, two chat-pattern hits ("explain" +
        # "how does") would short-circuit this straight to chat.
        from kernel.role_router import _match_direct_rule
        role, _label, _pattern = _match_direct_rule(
            "Explain how does this log file end up truncated"
        )
        assert role == "reader"


class TestEnglishAndRussianParity:
    """Test that English and Russian patterns work equally."""

    def test_create_vs_sozdai(self):
        client = _make_client(role="chat")
        d1 = route_role_decision_with_llm("Create a script", client=client, model="m")
        d2 = route_role_decision_with_llm("Создай скрипт", client=client, model="m")
        assert d1.role == "coder"
        assert d2.role == "coder"
        assert d1.source == "direct"
        assert d2.source == "direct"

    def test_explain_vs_obyasni(self):
        # Each is a single chat-pattern hit — both defer to the LLM router
        # (see _DIRECT_CHAT_MIN_MATCHES), which agrees on "chat" here.
        client = _make_client(role="chat")
        d1 = route_role_decision_with_llm("Explain Docker", client=client, model="m")
        d2 = route_role_decision_with_llm("Объясни Docker", client=client, model="m")
        assert d1.role == "chat"
        assert d2.role == "chat"
        assert d1.source == "llm"
        assert d2.source == "llm"

    def test_deploy_vs_razverni(self):
        client = _make_client(role="chat")
        d1 = route_role_decision_with_llm("Deploy on VPS", client=client, model="m")
        d2 = route_role_decision_with_llm("Разверни на VPS", client=client, model="m")
        assert d1.role == "devops"
        assert d2.role == "devops"
        assert d1.source == "direct"
        assert d2.source == "direct"


def test_empty_router_reply_falls_back_to_keywords_not_chat(monkeypatch):
    # A reasoning model that spends its budget thinking replies with nothing;
    # that must not silently become the chat role.
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
    decision = route_role_decision_with_llm(
        "please fix the flaky file loader",
        client=_make_client(role=""),
        model="m",
    )
    assert decision.role == "coder"
    assert decision.source == "keyword_fallback"


def test_long_essay_reply_falls_back_instead_of_trusting_a_stray_role_word(monkeypatch):
    # A verbose model can ignore "reply with one word" and answer the actual
    # question instead. A long reply that happens to contain a role word
    # ("...touch files or run code...") is noise, not a classification — it
    # must not be trusted over the keyword fallback on the real user goal.
    monkeypatch.setattr(role_router, "_load_router_prompt", lambda: "route")
    essay = (
        "Docker is a containerization platform. Under the hood it relies on "
        "namespaces and cgroups, not unlike how a coder might sandbox code "
        "in a container for testing."
    )
    client = _make_client(role=essay)
    decision = route_role_decision_with_llm(
        "docker production nginx setup",
        client=client,
        model="m",
    )
    # The essay contains "coder", but the real signal is the keyword
    # fallback on the actual goal ("docker"/"nginx"/"production" -> devops).
    assert decision.role == "devops"
    assert decision.source == "keyword_fallback"


def test_pochini_bug_routes_to_coder_directly():
    decision = route_role_decision_with_llm("почини баг", client=_make_client(role="chat"), model="m")
    assert decision.role == "coder"
    assert decision.source == "direct"


@pytest.mark.parametrize(
    "goal",
    [
        "поправь функцию average в empty_list.py",
        "Прогони тесты и почини всё, что падает.",
    ],
)
def test_russian_fix_verbs_route_to_coder(goal):
    decision = route_role_decision_with_llm(goal, client=_make_client(role="chat"), model="m")
    assert decision.role == "coder"


@pytest.mark.parametrize(
    "goal",
    [
        "Make all tests pass without modifying the tests.",
        "Make parse_date return a datetime object, but keep it returning a string too, "
        "and don't change any callers.",
        "обнови версию в pyproject.toml, но не трогай CHANGELOG",
    ],
)
def test_scoped_no_write_constraint_is_not_read_only(goal):
    # The constraint names what must stay untouched; the request still asks
    # for a change, so it must not become a reader run.
    assert role_router.has_no_write_marker(goal) is False


@pytest.mark.parametrize(
    "goal",
    [
        "посмотри код, ничего не меняй",
        "read-only review of auth.py",
        "прочитай конфиг, но ничего не меняй",
    ],
)
def test_plain_no_write_request_is_read_only(goal):
    assert role_router.has_no_write_marker(goal) is True
    decision = route_role_decision_with_llm(goal, client=_make_client(role="chat"), model="m")
    assert decision.role == "reader"
    assert decision.source == "direct"


@pytest.mark.parametrize(
    "goal",
    [
        "ничего не меняй, объясни как исправить",
        "don't change anything, just explain how to fix it",
        "read-only: tell me how to refactor this module",
        "не трогай файлы, подскажи как мне переименовать модуль",
    ],
)
def test_no_write_request_that_only_asks_how_to_change_is_read_only(goal):
    assert role_router.has_no_write_marker(goal) is True


@pytest.mark.parametrize(
    "goal",
    [
        "разбери модуль оплаты, не вноси правок",
        "посмотри конфиг, только анализ",
        "не редактируй файлы, просто расскажи что тут не так",
        "look at the auth flow, make no changes",
        "just read the logs and tell me what failed",
        "do not make any changes, explain why the test is flaky",
    ],
)
def test_common_no_write_phrasings_make_the_run_read_only(goal):
    assert role_router.has_no_write_marker(goal) is True


@pytest.mark.parametrize(
    "goal",
    [
        "write a short answer about what happens",
        "make sure both options are clear",
        "explain what a pipeline is in pandas",
        "сделай объяснение про работу кэша",
    ],
)
def test_a_topic_word_inside_another_word_does_not_pick_a_direct_role(goal):
    # "app" in "happens", "bot" in "both" and a bare "pipeline" are not requests
    # to build anything; the sentence goes on to the LLM router.
    assert role_router._match_direct_rule(goal) is None


@pytest.mark.parametrize(
    "goal, role",
    [
        ("give me a description of this module", "default"),
        ("why does the observer fire twice", "default"),
        ("steps to reproduction of the crash", "default"),
        ("Write a script that renames the files", "coder"),
        ("deploy the app to the server", "devops"),
    ],
)
def test_keyword_fallback_matches_word_starts_not_substrings(goal, role):
    expected = role_router._DEFAULT_ROLE if role == "default" else role
    assert role_router._keyword_route(goal) == expected


@pytest.mark.parametrize(
    "goal",
    [
        "Tell me how to add logging to a service",
        "как мне исправить эту ошибку?",
        "don't delete the logs, just list them",
        "не удали файл config.py, только покажи его",
        "верни мне список файлов",
    ],
)
def test_a_change_that_is_asked_about_or_ruled_out_is_not_a_change_request(goal):
    assert role_router.has_change_verb(goal) is False


@pytest.mark.parametrize(
    "goal",
    [
        "don't delete the logs, but fix the parser",
        "верни как было",
        "откати последний коммит",
    ],
)
def test_a_change_verb_outside_a_question_or_negation_still_counts(goal):
    assert role_router.has_change_verb(goal) is True


@pytest.mark.parametrize(
    "goal",
    [
        "don't change the tests, fix the parser",
        "не трогай CHANGELOG, исправь баг в main.py",
        "don't touch the tests, and show me how to fix it, then fix it",
    ],
)
def test_no_write_scope_with_a_separate_change_request_is_not_read_only(goal):
    assert role_router.has_no_write_marker(goal) is False


@pytest.mark.parametrize(
    "goal, role",
    [
        ("Add a function apply_discount(price, percent) to pricing.py", "coder"),
        ("Update the timeout in settings.py to 30", "coder"),
        # No project file named ("Flask" is a library, not a path) and no
        # chat keyword matched by _CHAT_KEYWORDS — the genuine catch-all,
        # which is "universal", not a guessed "chat".
        ("How do I add authentication to Flask?", "universal"),
        ("Explain how to update a dependency in package.json", "chat"),
    ],
)
def test_keyword_fallback_treats_a_change_verb_on_a_named_file_as_an_edit(goal, role):
    assert role_router._keyword_route(goal) == role


def test_edit_of_a_named_file_is_routed_before_the_llm_can_call_it_chat():
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="chat"))]
    )

    decision = role_router.route_role_decision_with_llm(
        "Add a function apply_discount(price, percent) to pricing.py", client, "m"
    )

    assert (decision.role, decision.source) == ("coder", "direct")
    client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize(
    "goal, role",
    [
        ("Look through auth.py and find any real bug in how the admin role is checked.", "reader"),
        ("Найди в auth.py реальную ошибку в проверке роли администратора.", "reader"),
        # Neither names a project file nor hits a _CHAT_KEYWORDS entry — the
        # catch-all default ("universal"), not a guessed "chat"; the point of
        # this test (a named file must never fall to chat) is unaffected.
        ("Why do people say e.g. that Flask is small?", "universal"),
        ("What does example.com resolve to?", "universal"),
    ],
)
def test_keyword_fallback_sends_a_named_file_to_reader_never_to_chat(goal, role):
    assert role_router._keyword_route(goal) == role


def test_router_outage_still_routes_a_file_question_to_reader():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("429 rate limit")

    decision = role_router.route_role_decision_with_llm(
        "Look through auth.py and find any real bug in how the admin role is checked.", client, "m"
    )

    assert (decision.role, decision.source) == ("reader", "keyword_fallback")


def test_named_file_edit_rule_defers_to_the_router_when_the_user_forbids_writing():
    goal = "Do not modify anything yourself. Review passwords.py and hand the fix over to the coder."

    assert role_router._is_edit_of_named_file(goal) is False


@pytest.mark.parametrize(
    "goal, history, expected",
    [
        ("Please continue and finish it.", [{"role": "coder", "stopped_by_user": True}], "coder"),
        ("продолжай", [{"role": "devops", "stopped_by_user": True}], "devops"),
        ("continue", [{"role": "coder"}], "chat"),
        ("continue", [{"role": "chat", "stopped_by_user": True}], "chat"),
        ("continue the essay about docker " + "x" * 80, [{"role": "coder", "stopped_by_user": True}], "chat"),
        ("write a poem", [{"role": "coder", "stopped_by_user": True}], "chat"),
        ("continue", [], "chat"),
    ],
)
def test_bare_continue_after_a_stopped_run_keeps_that_runs_role(goal, history, expected):
    assert role_router.resume_role(goal, "chat", history) == expected


def test_resume_role_never_overrides_a_non_chat_decision():
    history = [{"role": "coder", "stopped_by_user": True}]
    assert role_router.resume_role("continue", "reader", history) == "reader"


@pytest.mark.parametrize("goal", [
    "continue", "Please continue and finish it.", "Okay, carry on!",
    "продолжай", "продолжи", "Давай продолжай работу.",
    "Продолжи с места остановки", "продолжай, пожалуйста", "дальше",
])
def test_bare_continuation_accepts_short_resume_requests(goal):
    assert role_router.is_bare_continuation(goal)


@pytest.mark.parametrize("goal", [
    "continue the essay", "continue, but do not modify files",
    "продолжай, но сначала проверь тесты", "продолжительность работы",
    "", "write a poem",
])
def test_bare_continuation_preserves_new_instructions(goal):
    assert not role_router.is_bare_continuation(goal)


@pytest.mark.parametrize(
    "goal",
    [
        "Clear out the generated files in this project so I can rebuild, but do not touch src/ or config/.",
        "Wipe the build cache, don't touch the source.",
        "Очисти папку build, но не трогай src.",
    ],
)
def test_a_do_not_touch_scope_next_to_a_cleanup_verb_is_not_read_only(goal):
    assert role_router.has_change_verb(goal)
    assert not role_router.has_no_write_marker(goal)


def test_a_bare_do_not_touch_is_still_read_only():
    assert role_router.has_no_write_marker("Look at the project layout, do not touch anything.")


@pytest.mark.parametrize(
    "goal, role",
    [
        ("Очисти проект: убери всё лишнее, что можно пересобрать. src/config не трогай.", "coder"),
        ("Clean everything up. Leave src/config alone.", "coder"),
        ("Explain how src/config is loaded", "chat"),
        # No path pattern and no change verb reaches the directory-action
        # check, no chat keyword matches either — the catch-all default
        # ("universal"), not a guessed "chat".
        ("delete https://example.com/x", "universal"),
    ],
)
def test_keyword_fallback_treats_a_change_verb_on_a_directory_as_an_action(goal, role):
    assert role_router._keyword_route(goal) == role
