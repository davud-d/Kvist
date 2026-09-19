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


# Verbs that make a request a change request. Shared with agent_runtime
# (_is_modify_goal) so the router and the run loop can never disagree about
# whether the user asked for a change. The boundary assertions keep a short
# stem from firing inside an identifier like `remove_user` or `movement`.
_CHANGE_VERB_RE = re.compile(
    r"(?<![a-z_])(?:"
    r"add(?:s|ed|ing)?"
    r"|fix(?:es|ed|ing)?"
    r"|modif(?:y|ies|ied|ying)"
    r"|chang(?:e|es|ed|ing)"
    r"|updat(?:e|es|ed|ing)"
    r"|edit(?:s|ed|ing)?"
    r"|refactor(?:s|ed|ing)?"
    r"|improv(?:e|es|ed|ing)"
    r"|enhanc(?:e|es|ed|ing)"
    r"|replac(?:e|es|ed|ing)"
    r"|renam(?:e|es|ed|ing)"
    r"|remov(?:e|es|ed|ing)"
    r"|delet(?:e|es|ed|ing)"
    r"|implement(?:s|ed|ing)?"
    r"|rewrit(?:e|es|ing)|rewrote"
    r"|migrat(?:e|es|ed|ing)"
    r"|upgrad(?:e|es|ed|ing)"
    r"|bump(?:s|ed|ing)?"
    r"|convert(?:s|ed|ing)?"
    r"|mov(?:e|es|ed|ing)"
    r"|исправ|измени|изменить|обнов|добав|отредактир|переделай|доработай"
    r"|переимен|замен|удали|удалить|почин|поправ|реализ|перепиш|переписать"
    r"|мигрир|верни"
    r")(?![a-z_])",
    re.IGNORECASE,
)

# Change requests whose verb alone says nothing ("make" is also "make me a
# list"); the object of the change is what identifies them.
_CHANGE_PHRASE_RE = re.compile(
    r"make\s+(?:\S+\s+){0,6}?(?:pass|work|working|compile|build|run|runs|return|succeed)(?![a-z_])"
    r"|сделай\s+так"
    r"|сделай\s+(?:\S+\s+){0,6}?чтобы",
    re.IGNORECASE,
)


# A real extension, not any "x.y": "e.g." and "example.com" must not read as files.
_NAMED_FILE_RE = re.compile(
    r"(?<![\w])[\w./-]*\.(?:py|js|ts|tsx|jsx|json|ya?ml|toml|ini|cfg|conf|env|md|txt|csv|log|sql|sh"
    r"|go|rs|java|kt|c|h|cpp|rb|php|html|css|xml|lock|gradle)(?![\w])"
)


# Verbs that ask to look at something; "why do people name their file .env" has none.
_INSPECT_VERB_RE = re.compile(
    r"\b(?:find|look|check|inspect|review|read|open|show|summari[sz]e|count|total|sum|list|analy[sz]e|debug"
    r"|how many)\b"
    r"|найди|проверь|посмотри|покажи|прочитай|просмотри|сколько|сумм|итог|проанализ",
    re.IGNORECASE,
)


def _is_edit_of_named_file(user_goal: str) -> bool:
    """A change verb aimed at a named file, with no question or infra wording."""
    lowered = user_goal.casefold()
    if not (has_change_verb(user_goal) and _NAMED_FILE_RE.search(lowered)):
        return False
    # "Do not modify anything ... hand the fix to the coder" is a scope the
    # router model has to weigh, not a plain edit request.
    if any(marker in lowered for marker in _NO_WRITE_MARKERS):
        return False
    return not any(kw in lowered for kw in _CHAT_KEYWORDS + _DEVOPS_KEYWORDS)


def has_change_verb(text: str) -> bool:
    """True when the request asks for something to be changed."""
    lowered = text.casefold()
    return bool(_CHANGE_VERB_RE.search(lowered) or _CHANGE_PHRASE_RE.search(lowered))


def has_no_write_marker(text: str) -> bool:
    """True when the user forbids modifying anything at all.

    A no-write marker inside a change request is a *scope*, not a read-only
    run ("make all tests pass without modifying the tests"), so the marker
    only counts when nothing else in the request asks for a change. The
    markers themselves contain change verbs ("don't change"), so they are
    removed before the request is checked for one.
    """
    lowered = text.casefold()
    if not any(marker in lowered for marker in _NO_WRITE_MARKERS):
        return False
    return not has_change_verb(_strip_no_write_markers(lowered))


def _strip_no_write_markers(lowered: str) -> str:
    for marker in _NO_WRITE_MARKERS:
        lowered = lowered.replace(marker, " ")
    return lowered

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
# Priority: reader (no-write marker) → devops → coder → chat (most specific → most general)

# A single chat pattern is a bare filler word ("почему", "привет", "explain",
# "what is") that says nothing about domain — it fires just as readily inside
# a genuine "why won't X start, investigate and report" diagnostic request as
# inside small talk. Enumerating every diagnostic rephrasing as its own regex
# doesn't scale (any new wording that avoids the exact enumerated verbs slips
# back into chat). Instead: a lone chat-pattern hit is not enough evidence by
# itself — require at least two independent chat signals to agree before
# short-circuiting past the LLM router. One weak signal defers to the LLM
# router (see prompts/system/router_prompt.txt), which reads the whole
# sentence and already knows to route inspect-and-report requests to reader.
_DIRECT_CHAT_MIN_MATCHES = 2

# The router only needs one word back, but reasoning models (deepseek-flash)
# spend their thinking out of the same budget: at 50 tokens the thinking
# regularly hit the cap and the reply arrived empty, which silently routed
# every such request to chat — the one role that cannot look at the workspace.
_ROUTER_MAX_TOKENS = 1024

_DIRECT_READER_PATTERNS: tuple[tuple[str, str], ...] = (
    # "explain"/"tell me about"/"summarize" alone is a chat question, but
    # paired with a concrete workspace artifact it means "look at this and
    # report", which chat cannot do at all (no file access) — without its
    # own rule this fell through to the chat patterns below, since those
    # verbs are exactly what _DIRECT_CHAT_PATTERNS matches on.
    # Russian
    ("(?:объясни|расскажи|перескажи|что\\s+в|покажи|прочитай|резюмируй|подытожь)\\s.*\\b(?:лог\\w*|файл\\w*|дифф\\w*|diff|статус|status|вывод\\w*|дамп\\w*|трейс\\w*|инцидент\\w*)\\b", "explain-artifact"),
    # English
    ("(?:explain|tell me about|summarize|summarise|show me|read)\\s.*\\b(?:log\\w*|file\\w*|diff|status|output\\w*|dump\\w*|trace\\w*|incident\\w*)\\b", "explain-artifact"),
)

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
    # "почини баг" carries no artifact noun, so the create-artifact rule above
    # never sees it; without its own rule it fell through to chat.
    ("(?:почини|починить|поправь|поправить|чини)\\b", "fix action"),
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
    # No bare "почему"/"why is" or "привет" here on purpose: a greeting is
    # zero-content filler that opens messages of every kind, and a bare "why"
    # is exactly as common in "why won't X start" diagnostics as in trivia
    # questions — keeping them out of even the multi-match corroboration
    # pool (see _DIRECT_CHAT_MIN_MATCHES) stops them from combining with
    # each other to fake a confident match. The LLM router and the keyword
    # fallback below still classify bare greetings/"why" as chat by default.
    # Russian: questions / explanations / advice
    ("(?:что такое|кто такой|каков[аы]|какой смысл)", "definition question"),
    ("(?:чем|в ч[её]м)\\s.*(?:отличается|разниц[аы])", "comparison question"),
    ("объясни", "explanation request"),
    ("расскажи", "tell request"),
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
    Checks in priority order: reader (no-write marker) → devops → coder →
    reader (explain-artifact) → chat.
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

    # Checked after devops/coder: "restart the service and show me the
    # logs" is a devops action that happens to mention "logs", not a
    # read-only request, so an action-topic match must win first.
    for pattern, label in _DIRECT_READER_PATTERNS:
        if re.search(pattern, lowered):
            return ("reader", label, pattern)

    # Chat patterns are individually weak (bare filler words), so a single
    # hit does not short-circuit — see _DIRECT_CHAT_MIN_MATCHES above.
    chat_hits = [
        (label, pattern)
        for pattern, label in _DIRECT_CHAT_PATTERNS
        if re.search(pattern, lowered)
    ]
    if len(chat_hits) >= _DIRECT_CHAT_MIN_MATCHES:
        combined_label = "+".join(label for label, _pattern in chat_hits)
        combined_pattern = "|".join(pattern for _label, pattern in chat_hits)
        return ("chat", combined_label, combined_pattern)

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
    # Verbs, not topic nouns — "file"/"code"/"test" alone matched an ordinary
    # conceptual sentence ("why do people name their config file .env") as
    # confidently as an actual action request, which is exactly what
    # router_prompt.txt's own "classify by action, not topic" rule forbids.
    # An action request still has a verb (create/write/fix/...) to match on.
    "создай", "напиши", "сделай", "исправь", "рефактор",
    "create", "write", "make", "fix", "refactor",
    "скрипт", "script",
    "readme", "README", "логирование", "logging",
    "сгенерируй", "закоди", "разработай",
    "generate", "develop",
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
    """Keyword-based fallback routing. Priority: reader (no-write) → devops → coder → reader (explain-artifact) → chat → default."""
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

    # Reader: "explain/summarize <artifact>" reads a workspace file, which
    # chat cannot do — checked after devops/coder (an action request that
    # happens to mention "logs" is still an action) but before chat.
    for pattern, _label in _DIRECT_READER_PATTERNS:
        if re.search(pattern, lowered):
            return "reader"

    # Chat: explanation/discussion keywords
    for keyword in _CHAT_KEYWORDS:
        if keyword in lowered:
            return "chat"

    # A change verb aimed at a named file ("add X to pricing.py") is an edit
    # even when no keyword above knows the verb. Checked after the chat
    # keywords so "explain how to update package.json" stays a question.
    if _is_edit_of_named_file(user_goal):
        return "coder"

    # Chat has no file access, so a question about a named file answered by chat
    # is a flat refusal; reader can look and cannot write, which makes it the
    # safe guess when nothing else decided.
    if _NAMED_FILE_RE.search(lowered) and _INSPECT_VERB_RE.search(lowered):
        return "reader"

    return _DEFAULT_ROLE


# ── LLM-based routing ────────────────────────────────────────────

# A router reply is supposed to be one word. Some models (verbose/chatty ones,
# not just reasoning models burning their thinking budget) sometimes ignore
# that and answer the user's actual question instead of classifying it. A role
# word can then appear in that prose by pure coincidence — "explain Docker"
# answered in full will happily use the word "container" but could just as
# easily use "code" or "chat" in an unrelated sentence. Trusting any role word
# found anywhere in an unbounded reply turns the router into a coin flip on
# whatever word the model happened to write, not its actual classification.
# Capping the length before searching means a genuine short/noisy answer
# ("Role: coder", "**reader**") is still trusted, but a full answer is treated
# as "the model didn't classify" and falls through to the keyword fallback
# instead of a spuriously "confident" wrong pick.
_MAX_ROUTER_REPLY_CHARS = 40


def _response_names_role(response: str) -> bool:
    cleaned = response.strip().lower()
    if len(cleaned) > _MAX_ROUTER_REPLY_CHARS:
        return False
    return any(re.search(rf"\b{role}\b", cleaned) for role in _VALID_ROLES)


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

    # An edit of a named file is not a judgement call; one model answering
    # "chat" to it would answer a change request without touching the file.
    if _is_edit_of_named_file(user_goal):
        return RoleRouteResult(
            role="coder",
            source="direct",
            matched_rule="change verb on a named file",
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
            max_tokens=_ROUTER_MAX_TOKENS,
        )
        raw = response.choices[0].message.content or ""
        decision.raw_response = raw
        if _response_names_role(raw):
            decision.role = _parse_llm_role(raw)
            decision.source = "llm"
            return decision
        # An empty or role-less reply is no decision; defaulting it to chat
        # would answer a workspace question without looking at the workspace.
        decision.error = "router reply named no role"
        decision.role = _keyword_route(user_goal)
        decision.source = "keyword_fallback"
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
