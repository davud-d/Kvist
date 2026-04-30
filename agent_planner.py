# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_state import PlanInfo

_PROMPT_DIR = Path(__file__).resolve().parent
PLANNER_PROMPT = (_PROMPT_DIR / "planner_prompt.txt").read_text(encoding="utf-8").strip()


def detect_request_language(text: str) -> str:
    cyrillic = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    return "ru" if cyrillic > latin else "en"


def create_plan(self, user_goal: str) -> PlanInfo:
    language = detect_request_language(user_goal)
    if language == "ru":
        prompt = (
            "Задача пользователя:\n"
            f"{user_goal}\n\n"
            "Собери короткий план, критерии успеха и оцени сложность."
        )
    else:
        prompt = (
            "User task:\n"
            f"{user_goal}\n\n"
            "Build a short plan, success criteria, and estimate complexity"
        )

    try:
        raw = self._call_model(
            model=self.config.planner_model,
            system_prompt=PLANNER_PROMPT,
            user_prompt=prompt,
        )
        data = self._parse_json_dict(raw)
        plan = data.get("plan")
        success_criteria = data.get("success_criteria")
        risks = data.get("risks")
        complexity = str(data.get("complexity", "simple")).strip().lower()

        if not isinstance(plan, list) or not plan:
            raise ValueError("planner returned empty plan")
        if not isinstance(success_criteria, list) or not success_criteria:
            raise ValueError("planner returned empty success_criteria")
        if not isinstance(risks, list):
            risks = []
        if complexity not in {"simple", "complex"}:
            complexity = "complex" if self._is_complex_goal(user_goal) else "simple"

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
        )
    except Exception:
        return self._fallback_plan(user_goal)


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
    )


def is_complex_goal(self, user_goal: str) -> bool:
    lowered = user_goal.casefold()
    keywords = (
        "архитект",
        "рефактор",
        "production",
        "продакш",
        "система",
        "агент",
        "несколько файлов",
        "проект",
        "анализ",
        "память",
        "планирован",
        "улучш",
        "апгрейд",
    )
    return len(user_goal) > 180 or any(keyword in lowered for keyword in keywords)


def parse_json_dict(self, text: str) -> dict[str, Any]:
    stripped = text.strip()
    candidates = [stripped]

    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        inner = "\n".join(lines[1:-1]).strip()
        candidates.append(inner)

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and start < end:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        cleaned = candidate.replace("“", '"').replace("”", '"')
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            cleaned = cleaned.replace(",}", "}").replace(",]", "]")
            parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("Failed to parse JSON object")
