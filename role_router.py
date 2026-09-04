# -*- coding: utf-8 -*-
"""Semantic LLM-based router for determining agent role.

Uses fast_model (cheap model, e.g. gpt-4o-mini) to classify
the user request into one of the roles: coder, devops, chat.

Priority (first-match-wins):
  1. explicit read-only    (no-write marker → reader, beats topic)
  2. direct devops action  (deterministic regex)
  3. direct coder action   (deterministic regex)
  4. direct chat question  (deterministic regex)
  5. LLM router            (fast_model call)
  6. keyword fallback      (LLM error → lightweight keyword match)
  7. default               ("chat")

Fallback: keyword-based classification if LLM is unavailable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redaction import redact_text, redact_value

_VALID_ROLES = frozenset({"coder", "devops", "chat", "reader"})
_DEFAULT_ROLE = "chat"

# Explicit "do not modify" phrasing. When present, the request is read-only no
# matter what the router or the model would otherwise pick — this is the hard
# guarantee (a tool-level gate, not a prompt line) that an inspection task
# cannot mutate the workspace.
_NO_WRITE_MARKERS: tuple[str, ...] = (
    "ничего не меняй", "ничего не меняйте",
    "не изменяй", "не изменяйте",
    "не меняй", "не меняйте",
    "без изменений", "не трогай", "не трогайте",
    "только прочитай", "только прочитайте", "только просмотри",
    "read-only", "read only", "readonly",
    "don't modify", "dont modify", "do not modify",
    "don't change", "do not change",
    "don't edit", "do not edit",
    "don't touch", "do not touch",
    "without changes", "without making changes", "without modifying",
)


def has_no_write_marker(text: str) -> bool:
    """True when the user explicitly forbids modifying anything."""
    lowered = text.casefold()
    return any(marker in lowered for marker in _NO_WRITE_MARKERS)

_ROUTER_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "system" / "router_prompt.txt"
)
_ROUTER_PROMPT_CACHE: str | None = None


def _load_router_prompt() -> str:
    """Loads and caches the system prompt for the router."""
    global _ROUTER_PROMPT_CACHE
    if _ROUTER_PROMPT_CACHE is None:
        _ROUTER_PROMPT_CACHE = _ROUTER_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return _ROUTER_PROMPT_CACHE


# ── RoleRouteResult dataclass ──────────────────────────────────

@dataclass
class RoleRouteResult:
    """Observable routing result — shows exactly where the role came from.

    Attributes:
        role:         Final role ("coder" | "devops" | "chat").
        source:       How the decision was made:
                      "direct" / "llm" / "keyword_fallback" / "default".
        raw_response: Raw LLM response text (empty if LLM was not called).
        matched_rule: The direct rule pattern that fired (empty if no match).
        error:        Error message if LLM call failed.
        model:        Model name passed to the LLM router.
    """
    role: str = _DEFAULT_ROLE
    source: str = "default"
    raw_response: str = ""
    matched_rule: str = ""
    error: str = ""
    model: str = ""


# Backward-compatible alias
RouteDecision = RoleRouteResult


# ── Direct deterministic rules (before LLM) ──────────────────────
# Priority: devops → coder → chat (most specific → most general)

_DIRECT_DEVOPS_PATTERNS: tuple[tuple[str, str], ...] = (
    # Russian: service status / inspection
    ("проверь\\s+.*(?:статус|состояние).*\\b(?:systemd|systemctl|nginx|apache|docker|сервис\\w*|служб\\w*|демон\\w*)\\b", "service status"),
    ("(?:статус|состояние)\\s+.*\\b(?:systemd|systemctl|nginx|apache|docker|сервис\\w*|служб\\w*|демон\\w*)\\b", "service status"),

    # English: service status / inspection
    ("check\\s+.*(?:status|state).*\\b(?:systemd|systemctl|service\\w*|daemon\\w*|nginx|apache|docker)\\b", "service status"),
    ("(?:status|state)\\s+.*\\b(?:systemd|systemctl|service\\w*|daemon\\w*|nginx|apache|docker)\\b", "service status"),

    # Russian
    ("разверни.*(?:docker|сервер|vps|проект|приложени)", "deploy action"),
    ("задеплой", "deploy action"),
    ("настрой.*(?:nginx|apache|сервер|server|proxy|firewall|ssl|certbot)", "infra config"),
    ("перезапусти.*(?:сервис|systemd|служб|systemctl|демон)", "service restart"),
    ("запусти.*docker.*(?:compose|контейнер)", "docker compose"),
    ("(?:ci/cd|cicd|pipeline|пайплайн)", "ci/cd"),
    # English
    ("deploy.*(?:docker|server|vps|project|app)", "deploy action"),
    ("configure.*(?:nginx|apache|server|proxy|firewall|ssl|certbot)", "infra config"),
    ("restart.*(?:service|systemd|systemctl|daemon)", "service restart"),
    ("run.*docker.*(?:compose|container)", "docker compose"),
    ("setup.*(?:ci/cd|cicd|pipeline)", "ci/cd"),
)

_DIRECT_CODER_PATTERNS: tuple[tuple[str, str], ...] = (
    # Russian: creation verbs + artifact
    ("(?:создай|напиши|сделай|сгенерируй|закоди|разработай)\\s.*(?:скрипт|файл|бот|README|readme|программ|приложени|код|модуль)", "create artifact"),
    ("исправь\\s.*(?:ошибк|баг|проблем|issue)", "fix action"),
    ("добавь\\s.*логирование", "add logging"),
    ("рефактор", "refactor"),
    ("прочитай\\s.*\\.py\\b", "read project file"),
    ("запусти\\s.*тесты?", "run tests"),
    ("собери\\s.*(?:проект|project)", "build project"),
    ("проверь\\s+.*(?:\\bgit\\b|git\\s+status|git\\s+diff|\\bdiff\\b)", "git check"),
    # English: creation verbs + artifact
    ("(?:create|write|make|generate|code|develop)\\s.*(?:script|file|bot|README|readme|program|app|code|module)", "create artifact"),
    ("fix\\s.*(?:bug|error|issue|problem)", "fix action"),
    ("add\\s.*logging", "add logging"),
    ("refactor", "refactor"),
    ("read\\s.*\\.py\\b", "read project file"),
    ("run\\s.*tests?", "run tests"),
    ("build\\s.*(?:project|app)", "build project"),
    ("check\\s.*(?:git|status|diff)", "git check"),
)

_DIRECT_CHAT_PATTERNS: tuple[tuple[str, str], ...] = (
    # Russian: questions / explanations / advice
    ("(?:что такое|кто такой|каков[аы]|какой смысл)", "definition question"),
    ("(?:чем|в ч[её]м)\\s.*(?:отличается|разниц[аы])", "comparison question"),
    ("объясни", "explanation request"),
    ("расскажи", "tell request"),
    ("почему", "why question"),
    ("как работает", "how-it-works question"),
    ("как устроен", "how-it-works question"),
    ("для чего", "purpose question"),
    ("зачем", "purpose question"),
    ("философи[яи]", "philosophy question"),
    ("посоветуй", "advice request"),
    ("как лучше", "advice request"),
    ("покажи пример", "example request"),
    ("дай код.*без создания", "code example only"),
    ("что думаешь", "opinion request"),
    ("привет", "greeting"),
    ("помоги разобраться", "help understanding"),
    # English: questions / explanations / advice
    ("what is", "definition question"),
    ("who is", "definition question"),
    ("explain", "explanation request"),
    ("tell me about", "tell request"),
    ("difference between", "comparison question"),
    ("philosophy", "philosophy question"),
    ("show.*example", "example request"),
    ("give.*example", "example request"),
    ("how does", "how-it-works question"),
    ("why is", "why question"),
    ("what are", "definition question"),
    ("compare", "comparison question"),
    ("pros and cons", "comparison question"),
    ("trade-?off", "comparison question"),
    ("best practic", "advice request"),
    ("architecture.*(?:advice|discuss)", "architecture discussion"),
    ("what do you think", "opinion request"),
    ("brainstorm", "brainstorm request"),
    ("suggest", "suggestion request"),
    ("how should", "advice request"),
    ("help me understand", "help understanding"),
)


def _match_direct_rule(user_goal: str) -> tuple[str, str, str] | None:
    """Check if a direct deterministic rule matches the user goal.

    Returns (role, matched_rule_label, pattern) or None.
    Checks in priority order: reader → devops → coder → chat.
    """
    lowered = user_goal.casefold()

    # An explicit no-modification request is read-only regardless of topic.
    # Reader reads any file equally well, so a devops-flavoured inspection
    # ("прочитай systemd unit, ничего не меняй") does not need the devops
    # prompt/toolset — that would only add input tokens on every step.
    if has_no_write_marker(user_goal):
        return ("reader", "explicit read-only", "no_write_marker")

    for pattern, label in _DIRECT_DEVOPS_PATTERNS:
        if re.search(pattern, lowered):
            return ("devops", label, pattern)

    for pattern, label in _DIRECT_CODER_PATTERNS:
        if re.search(pattern, lowered):
            return ("coder", label, pattern)

    for pattern, label in _DIRECT_CHAT_PATTERNS:
        if re.search(pattern, lowered):
            return ("chat", label, pattern)

    return None


# ── Keyword fallback ─────────────────────────────────────────────

_DEVOPS_KEYWORDS: tuple[str, ...] = (
    "docker", "deploy", "nginx", "systemctl", "kubernetes",
    "k8s", "ci/cd", "pipeline", "ansible", "terraform", "helm",
    "compose", "server", "production", "staging", "infrastructure",
    "container", "certbot", "ssl", "reverse proxy", "load balancer",
    "journalctl", "supervisor", "pm2", "crontab",
    "разверни", "задеплой", "сервер",
)

_CODER_KEYWORDS: tuple[str, ...] = (
    "создай", "напиши", "сделай", "исправь", "рефактор",
    "create", "write", "make", "fix", "refactor",
    "скрипт", "script", "файл", "file",
    "readme", "README", "логирование", "logging",
    "код", "code", "тест", "test",
    "сгенерируй", "закоди", "разработай",
    "generate", "code", "develop",
)

_CHAT_KEYWORDS: tuple[str, ...] = (
    # English
    "explain", "tell me about", "what do you think", "what's the best way",
    "advise", "approach", "opinion", "discuss",
    "brainstorm", "suggest", "what do you think",
    "how should", "best practice", "consult",
    "help me understand", "how does", "difference between",
    "compare", "pros and cons", "trade-off",
    "architecture",
    # Russian
    "объясни", "расскажи", "что думаешь", "как лучше", "посоветуй",
    "архитектур", "подход", "мнение",
    "обсуди",
    "помоги разобраться", "как устроен", "в чём разница",
    "плюсы и минусы", "привет", "посоветуй"
)


def _keyword_route(user_goal: str) -> str:
    """Keyword-based fallback routing. Priority: reader → devops → coder → chat → default."""
    lowered = user_goal.casefold()

    # Reader: an explicit "do not modify" wins over any topic keyword.
    if has_no_write_marker(user_goal):
        return "reader"

    # DevOps: infrastructure/deployment keywords
    for keyword in _DEVOPS_KEYWORDS:
        if keyword in lowered:
            return "devops"

    # Coder: creation/modification keywords
    for keyword in _CODER_KEYWORDS:
        if keyword in lowered:
            return "coder"

    # Chat: explanation/discussion keywords
    for keyword in _CHAT_KEYWORDS:
        if keyword in lowered:
            return "chat"

    return _DEFAULT_ROLE


# ── LLM-based routing ────────────────────────────────────────────

def _parse_llm_role(response: str) -> str:
    """Extracts role from LLM response. Robust to noise."""
    cleaned = response.strip().lower()
    # Direct match
    if cleaned in _VALID_ROLES:
        return cleaned
    # Search for role in response (LLM may add extra text)
    for role in _VALID_ROLES:
        if re.search(rf"\b{role}\b", cleaned):
            return role
    return _DEFAULT_ROLE


def route_role_decision_with_llm(
    user_goal: str,
    client: Any,
    model: str,
    temperature: float = 0.0,
) -> RoleRouteResult:
    """Determines role with full observability (RoleRouteResult).

    Priority:
      1. explicit read-only (no-write marker → reader)
      2. direct devops action
      3. direct coder action
      4. direct chat question
      5. LLM router
      6. keyword fallback
      7. default

    Returns a RoleRouteResult with role + source + metadata.
    """
    # ── Step 1–4: Direct deterministic rules ──────────────────
    direct = _match_direct_rule(user_goal)
    if direct is not None:
        direct_role, rule_label, rule_pattern = direct
        return RoleRouteResult(
            role=direct_role,
            source="direct",
            matched_rule=f"{rule_label}: {rule_pattern}",
            model=model,
        )

    # ── Step 4: LLM router ────────────────────────────────────
    decision = RoleRouteResult(source="default", model=model)
    system_prompt = _load_router_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_goal},
    ]
    safe_messages = redact_value(messages)
    assert isinstance(safe_messages, list)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=safe_messages,
            temperature=temperature,
            max_tokens=50,
        )
        raw = response.choices[0].message.content or ""
        decision.raw_response = raw
        decision.role = _parse_llm_role(raw)
        decision.source = "llm"
    except Exception as exc:
        # ── Step 5: LLM failed → keyword fallback ─────────────
        decision.error = redact_text(
            str(exc) if isinstance(exc, Exception) else repr(exc)
        )
        kw_role = _keyword_route(user_goal)
        if kw_role != _DEFAULT_ROLE or any(
            kw in user_goal.casefold()
            for kw_list in (_DEVOPS_KEYWORDS, _CODER_KEYWORDS, _CHAT_KEYWORDS)
            for kw in kw_list
        ):
            decision.role = kw_role
            decision.source = "keyword_fallback"
        else:
            # ── Step 6: Default ────────────────────────────────
            decision.role = _DEFAULT_ROLE
            decision.source = "default"

    return decision


def route_role_with_llm(
    user_goal: str,
    client: Any,
    model: str,
    temperature: float = 0.0,
) -> str:
    """Determines role via LLM call (fast_model). Backward-compatible wrapper.

    Returns only the role string. For full observability use
    route_role_decision_with_llm().
    """
    decision = route_role_decision_with_llm(
        user_goal=user_goal,
        client=client,
        model=model,
        temperature=temperature,
    )
    return decision.role


def route_role(user_goal: str) -> str:
    """Keyword-only routing (without LLM). Used as standalone fallback."""
    return _keyword_route(user_goal)
