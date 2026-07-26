# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any

from protocol import AgentCommand


def prepare_web_search_command(self, user_goal: str, command: AgentCommand) -> str | None:
    query = str(command.action.args.get("query", "")).strip()
    if not query:
        return "Empty query for web_search"

    if not self._is_web_topic(user_goal=user_goal, query=query):
        return "web_search is only allowed for tasks involving external libraries and current API/releases"

    if "top_k" not in command.action.args and "max_results" not in command.action.args:
        command.action.args["top_k"] = 5
    if "freshness" not in command.action.args:
        command.action.args["freshness"] = "any"

    enhanced_query = self._append_known_version_to_query(query)
    command.action.args["query"] = enhanced_query

    normalized_query = self._normalize_message(enhanced_query)
    normalized_thought = self._normalize_message(command.thought)
    if (
        normalized_query == self._state.last_web_query
        and normalized_thought == self._state.last_web_thought
    ):
        return "Repeating identical web_search without a new hypothesis is forbidden"

    self._state.last_web_query = normalized_query
    self._state.last_web_thought = normalized_thought
    return None


def prepare_fetch_url_command(self, command: AgentCommand) -> str | None:
    url = str(command.action.args.get("url", "")).strip()
    if not url:
        return "Empty url for fetch_url"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "fetch_url only supports http/https URLs"
    if "max_chars" not in command.action.args:
        command.action.args["max_chars"] = 12000
    return None


def is_web_topic(self, user_goal: str, query: str) -> bool:
    text = f"{user_goal} {query}".casefold()
    keywords = (
        "external library",
        "sdk",
        "framework",
        "deprecated",
        "migration",
        "release notes",
        "changelog",
        "breaking change",
        "signature",
        "attribute",
        "import error",
        "official docs",
        "new version",
        "price",
        "pricing",
        "limit",
        "status",
    )

    if any(keyword in text for keyword in keywords):
        return True

    for package in self._state.dependency_versions:
        if self._contains_package_token(text, package):
            return True

    return False


def append_known_version_to_query(self, query: str) -> str:
    lowered_query = query.casefold()
    for package, raw_version in self._state.dependency_versions.items():
        if not self._contains_package_token(lowered_query, package):
            continue

        version_token = self._extract_version_token(raw_version)
        if not version_token:
            continue
        if version_token.casefold() in lowered_query:
            continue
        return f"{query} {version_token}"

    return query


def contains_package_token(self, text: str, package: str) -> bool:
    candidates = {
        package.casefold(),
        package.replace("-", "_").casefold(),
        package.replace("_", "-").casefold(),
    }
    for candidate in candidates:
        pattern = rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])"
        if re.search(pattern, text):
            return True
    return False


def extract_version_token(self, raw_spec: str) -> str:
    match = re.search(r"\d+(?:\.\d+){0,4}", raw_spec)
    return match.group(0) if match else ""


def extract_fact_from_content(self, url: str, title: str, content: str) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in content.splitlines()
        if line.strip()
    ]
    if not lines:
        return ""

    snippet = " ".join(lines[:2]).strip()
    snippet = snippet[:260]
    label = title or url
    if not label:
        label = "Source"
    return f"{label}: {snippet}" if snippet else label


def summarize_search_results(self, results: Any) -> str:
    if not isinstance(results, list):
        return ""

    titles: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        titles.append(title)
        if len(titles) >= 3:
            break

    return "; ".join(titles)


def normalize_message(self, text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()