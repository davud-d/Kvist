# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_state import PlanInfo
from parser import CommandValidationError, load_single_json_object

_PROMPT_DIR = Path(__file__).resolve().parent

try:
    PLANNER_PROMPT = (_PROMPT_DIR / "prompts" / "planner_prompt.txt").read_text(encoding="utf-8").strip()
except FileNotFoundError:
    raise FileNotFoundError("Could not find the planner prompt.")


# Fallback classification only. The planner's own task_kind is authoritative;
# this list catches the case where the planner call fails or omits the field.
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


def infer_task_kind(user_goal: str) -> str:
    """Heuristic 'report' vs 'action' classification used only as a fallback."""
    lowered = user_goal.casefold()
    return "report" if any(marker in lowered for marker in _REPORT_MARKERS) else "action"

def _reset_client_disabled_backends(agent_self: Any) -> None:
    """Clear any disabled-backend state on the LLM client.

    The planner runs as a best-effort step before the main action loop.  If a
    backend fails during planning (e.g. an invalid API key triggers an auth
    error), the shared ``FreeFlowCompatibleClient`` instance records that backend
    as disabled.  This side-effect must not propagate into the action loop,
    which should still get a fair chance to try every configured backend.
    """
    try:
        client = getattr(agent_self, "client", None)
        disabled = getattr(client, "_disabled_backends", None)
        if isinstance(disabled, set):
            disabled.clear()
    except Exception:
        pass


def create_plan(self, user_goal: str) -> PlanInfo:
    prompt = (
        "User task:\n"
        f"{user_goal}\n\n"
        "Build a short plan, success criteria, and estimate complexity"
    )

    try:
        raw = self._call_model(
            model=self.config.fast_model,
            system_prompt=PLANNER_PROMPT,
            user_prompt=prompt,
            usage_kind="plan",
        )
        data = self._parse_json_dict(raw)
        plan = data.get("plan")
        success_criteria = data.get("success_criteria")
        risks = data.get("risks")
        complexity = str(data.get("complexity", "simple")).strip().lower()
        task_kind = str(data.get("task_kind", "")).strip().lower()
        if task_kind not in {"report", "action"}:
            task_kind = infer_task_kind(user_goal)

        if not isinstance(plan, list) or not plan:
            raise ValueError("planner returned empty plan")
        if not isinstance(success_criteria, list) or not success_criteria:
            raise ValueError("planner returned empty success_criteria")
        if not isinstance(risks, list):
            risks = []
        if complexity not in {"simple", "complex"}:
            complexity = "complex" if self._is_complex_goal(user_goal) else "simple"
        elif complexity == "simple" and self._is_complex_goal(user_goal):
            # The planner runs on fast_model and its "simple" verdict pins the
            # entire run to fast_model. Never let it downgrade a goal the
            # deterministic heuristic already flagged as complex.
            complexity = "complex"

        return PlanInfo(
            goal=str(data.get("goal", user_goal)).strip() or user_goal,
            complexity=complexity,
            plan=[str(item).strip() for item in plan if str(item).strip()],
            success_criteria=[
                str(item).strip()
                for item in success_criteria
                if str(item).strip()
            ],
            risks=[str(item).strip() for item in risks if str(item).strip()],
            task_kind=task_kind,
        )
    except Exception:
        # Reset any backend-disabled state that the planner call may have set so
        # that the action model loop gets a clean slate.  The planner is a
        # best-effort step: its failures must not permanently poison the shared
        # LLM client for the rest of the run.
        _reset_client_disabled_backends(self)
        return self._fallback_plan(user_goal)


def no_plan_info(self, user_goal: str) -> PlanInfo:
    """Minimal plan used when planning is skipped (`/plan false`).

    No LLM planner call is made. The single plan step explicitly tells the
    action model that no plan was generated, so it self-orients in the first
    step (inspect what it needs) instead of assuming a plan exists.
    """
    return PlanInfo(
        goal=user_goal,
        complexity="complex" if self._is_complex_goal(user_goal) else "simple",
        plan=[
            "No plan was generated for this task (planning skipped). "
            "Orient yourself first: inspect the files/state you need, "
            "then act directly toward the task.",
        ],
        success_criteria=[
            "the user's task is fully accomplished",
            "result verified by available means",
        ],
        risks=[
            "no upfront plan: verify assumptions before mutating",
        ],
        task_kind=infer_task_kind(user_goal),
    )


def fallback_plan(self, user_goal: str) -> PlanInfo:
    complexity = "complex" if self._is_complex_goal(user_goal) else "simple"
    return PlanInfo(
        goal=user_goal,
        complexity=complexity,
        plan=[
            "review relevant files and structure",
            "make minimal precise changes",
            "verify result and errors",
            "summarize the completed task",
        ],
        success_criteria=[
            "key changes applied",
            "errors not ignored",
            "result verified by available means",
        ],
        risks=[
            "model may loop on a single action",
            "part of the environment may be unavailable",
        ],
        task_kind=infer_task_kind(user_goal),
    )


def is_complex_goal(self, user_goal: str) -> bool:
    lowered = user_goal.casefold()
    keywords = (
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
    return len(user_goal) > 180 or any(keyword in lowered for keyword in keywords)


def parse_json_dict(self, text: str) -> dict[str, Any]:
    try:
        parsed = load_single_json_object(text)
    except CommandValidationError as error:
        raise ValueError(str(error)) from error

    if not isinstance(parsed, dict):
        raise ValueError("Planner response must be a JSON object")
    return parsed
