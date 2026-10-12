# -*- coding: utf-8 -*-
"""Kvist test runner skill: pytest, node --test and npm test with structured results."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

EXCHANGE_OUT = Path("/kvist/exchange/out")
_PYTEST_MARKERS = ("pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py", "tox.ini")
_PYTEST_COUNT_RE = re.compile(r"(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed|deselected|warnings?)")
_PYTEST_FAILURE_RE = re.compile(r"^(FAILED|ERROR) (\S+?)(?: - (.*))?$")
_PYTEST_LOCATION_RE = re.compile(r"^(\S+\.py):(\d+): ")
_TAP_COUNT_RE = re.compile(r"^# (tests|pass|fail|skipped|todo|cancelled) (\d+)$")
_TAP_FAIL_RE = re.compile(r"^\s*not ok \d+ - (.*)$")
MAX_FAILURES = 50


def describe() -> dict[str, Any]:
    return {"tools": ["run"]}


def execute(tool_name: str, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
    if tool_name != "run":
        raise ValueError(f"unknown tool {tool_name}")
    runner = arguments.get("runner") or "auto"
    if runner == "auto":
        runner = detect_runner(context)
    if runner is None:
        return {"status": "no_tests", "runner": "none", "command": [],
                "note": "No pytest configuration, test files or npm test script was found."}
    command = build_command(runner, str(arguments.get("target") or ""), context)
    context.ui.notify("running " + " ".join(command))
    started = time.monotonic()
    result = context.processes.run(command, timeout=arguments.get("timeout_seconds") or 300)
    duration = round(time.monotonic() - started, 2)
    stdout, stderr = str(result.get("stdout") or ""), str(result.get("stderr") or "")
    output = f"{stdout}\n{stderr}".strip()
    parsed = parse_pytest(output) if runner == "pytest" else parse_tap(output)
    status = classify(result, parsed)
    log = save_log(context, runner, command, output)
    return {"status": status, "runner": runner, "command": command, "counts": parsed["counts"],
            "failures": parsed["failures"][:MAX_FAILURES], "duration_seconds": duration,
            "log_artifact": log, "log_tail": output[-3000:],
            **({"note": "The command was refused before it ran; see log_tail."} if result.get("denied") else {})}


def detect_runner(context: Any) -> str | None:
    root = {entry.get("name") for entry in (context.files.list(".").get("items") or []) if isinstance(entry, dict)}
    if root & set(_PYTEST_MARKERS):
        return "pytest"
    if "package.json" in root:
        package = json.loads(context.files.read("package.json")["content"] or "{}")
        if (package.get("scripts") or {}).get("test"):
            return "npm"
    found = context.files.find("test_*.py")
    if found.get("matches"):
        return "pytest"
    if context.files.find("*.test.js").get("matches") or context.files.find("*.test.mjs").get("matches"):
        return "node"
    return None


def build_command(runner: str, target: str, context: Any) -> list[str]:
    if runner == "pytest":
        python = ".venv/bin/python" if _exists(context, ".venv/bin/python") else "python3"
        command = [python, "-m", "pytest", "-q", "-rfEs", "-p", "no:cacheprovider", "--color=no"]
        return command + ([target] if target else [])
    if runner == "node":
        return ["node", "--test", "--test-reporter=tap", *([target] if target else [])]
    return ["npm", "test", "--silent", *(["--", target] if target else [])]


def _exists(context: Any, path: str) -> bool:
    folder, _, name = path.rpartition("/")
    try:
        items = context.files.list(folder or ".").get("items") or []
    except Exception:  # noqa: BLE001 - a missing folder simply means "no"
        return False
    return any(isinstance(item, dict) and item.get("name") == name for item in items)


def parse_pytest(output: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    failures: list[dict[str, Any]] = []
    lines = output.splitlines()
    for line in reversed(lines):
        if re.search(r"\b(passed|failed|error|errors|skipped|no tests ran)\b", line) and " in " in line:
            for number, word in _PYTEST_COUNT_RE.findall(line):
                key = {"error": "errors", "warning": "warnings"}.get(word, word)
                counts[key] = int(number)
            break
    for line in lines:
        match = _PYTEST_FAILURE_RE.match(line.strip())
        if match:
            kind, test, message = match.groups()
            failure: dict[str, Any] = {"test": test, "kind": kind.lower(), "message": (message or "").strip()[:500]}
            location = _location(lines, test)
            if location:
                failure.update(location)
            failures.append(failure)
    return {"counts": counts, "failures": failures}


def _location(lines: list[str], test: str) -> dict[str, Any] | None:
    module = test.split("::", 1)[0]
    for line in lines:
        match = _PYTEST_LOCATION_RE.match(line.strip())
        if match and match.group(1).endswith(module):
            return {"file": match.group(1), "line": int(match.group(2))}
    return None


def parse_tap(output: str) -> dict[str, Any]:
    counts: dict[str, int] = {}
    failures = []
    for line in output.splitlines():
        count = _TAP_COUNT_RE.match(line.strip())
        if count:
            key = {"pass": "passed", "fail": "failed", "tests": "total"}.get(count.group(1), count.group(1))
            counts[key] = int(count.group(2))
        failure = _TAP_FAIL_RE.match(line)
        if failure and not failure.group(1).lstrip().startswith("#"):
            failures.append({"test": failure.group(1).strip()[:300], "kind": "failed", "message": ""})
    return {"counts": counts, "failures": failures}


def classify(result: dict[str, Any], parsed: dict[str, Any]) -> str:
    if result.get("timed_out"):
        return "timeout"
    if result.get("denied"):
        return "error"
    code = result.get("returncode")
    counts = parsed["counts"]
    if code == 0:
        return "passed"
    if counts.get("failed") or parsed["failures"]:
        return "failed"
    if code == 5 and not counts:
        return "no_tests"
    return "error"


def save_log(context: Any, runner: str, command: list[str], output: str) -> str:
    EXCHANGE_OUT.mkdir(parents=True, exist_ok=True)
    path = EXCHANGE_OUT / f"{runner}-log-{int(time.time())}.txt"
    path.write_text("$ " + " ".join(command) + "\n\n" + output, encoding="utf-8")
    artifact = context.artifacts.save(str(path), name=path.name, kind="test-log", caption="Full test log")
    return artifact["path"]
