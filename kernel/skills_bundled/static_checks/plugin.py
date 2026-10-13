# -*- coding: utf-8 -*-
"""Kvist static checks skill: ruff, mypy, tsc, eslint, go vet and cargo check with structured findings."""
from __future__ import annotations

import re
import time
import tomllib
from pathlib import Path
from typing import Any, NamedTuple

EXCHANGE_OUT = Path("/kvist/exchange/out")
MAX_FINDINGS_PER_TOOL = 60
DEFAULT_TIMEOUT = 90

_FILE = r"(?P<file>[^\s:#(][^:(]*)"
PARSERS: dict[str, re.Pattern[str]] = {
    "ruff": re.compile(_FILE + r":(?P<line>\d+):(?P<column>\d+): (?P<code>[A-Z]+\d+) (?P<message>.*)$"),
    "mypy": re.compile(_FILE + r":(?P<line>\d+):(?:(?P<column>\d+):)? (?P<severity>error|warning): "
                       r"(?P<message>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$"),
    "tsc": re.compile(r"(?P<file>.+?)\((?P<line>\d+),(?P<column>\d+)\): (?P<severity>error|warning) "
                      r"(?P<code>TS\d+): (?P<message>.*)$"),
    "eslint": re.compile(_FILE + r":(?P<line>\d+):(?P<column>\d+): (?P<message>.*) "
                         r"\[(?P<severity>Error|Warning)(?:/(?P<code>[^\]]+))?\]$"),
    "go_vet": re.compile(r"(?P<file>[^\s#][^:]*\.go):(?P<line>\d+):(?:(?P<column>\d+):)? (?P<message>.*)$"),
    "cargo_check": re.compile(r"(?P<file>[^\s:]+\.rs):(?P<line>\d+):(?P<column>\d+): (?P<severity>error|warning)"
                              r"(?:\[(?P<code>[A-Z]\d+)\])?: (?P<message>.*)$"),
}
ESLINT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
                  ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")


class Plan(NamedTuple):
    tool: str
    command: list[str]
    unavailable: str = ""


def describe() -> dict[str, Any]:
    return {"tools": ["run"]}


def execute(tool_name: str, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
    if tool_name != "run":
        raise ValueError(f"unknown tool {tool_name}")
    target = str(arguments.get("target") or "").strip()
    if target.startswith("-"):
        raise ValueError("target must be a path, not an option")
    wanted = str(arguments.get("tool") or "auto")
    plans = plan_checks(context, wanted, target)
    if not plans:
        return {"status": "no_tools", "tools": [],
                "note": "No linter or type checker configuration was found; name one with the tool argument."}
    timeout = int(arguments.get("timeout_seconds") or DEFAULT_TIMEOUT)
    results, log = [], []
    for plan in plans:
        result, output = _run_plan(context, plan, timeout)
        results.append(result)
        log.append("$ " + " ".join(plan.command) + "\n" + output)
    answer: dict[str, Any] = {"status": overall_status(results), "tools": results}
    if any(result["status"] != "unavailable" for result in results):
        answer["log_artifact"] = _save_log(context, "\n\n".join(log))
    return answer


def _run_plan(context: Any, plan: Plan, timeout: int) -> tuple[dict[str, Any], str]:
    row: dict[str, Any] = {"tool": plan.tool, "command": plan.command}
    if plan.unavailable:
        return {**row, "status": "unavailable", "note": plan.unavailable}, plan.unavailable
    context.ui.notify("running " + " ".join(plan.command))
    started = time.monotonic()
    result = context.processes.run(plan.command, timeout=timeout)
    output = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip()
    findings = parse_findings(plan.tool, output)
    status = classify(result, findings, output)
    row.update({
        "status": status, "duration_seconds": round(time.monotonic() - started, 2),
        "counts": {"error": sum(f["severity"] == "error" for f in findings),
                   "warning": sum(f["severity"] == "warning" for f in findings)},
        "findings": findings[:MAX_FINDINGS_PER_TOOL], "truncated": len(findings) > MAX_FINDINGS_PER_TOOL,
    })
    if status in ("error", "unavailable", "timeout"):
        row["log_tail"] = output[-2000:]
    if result.get("denied"):
        row["note"] = "The command was refused before it ran; see log_tail."
    return row, output


def plan_checks(context: Any, wanted: str, target: str) -> list[Plan]:
    names = {str(item["name"]) for item in (context.files.list(".").get("items") or [])
             if isinstance(item, dict) and item.get("name")}
    pyproject = _pyproject_tools(context, names)
    detected = {
        "ruff": bool(names & {"ruff.toml", ".ruff.toml"}) or "ruff" in pyproject,
        "mypy": bool(names & {"mypy.ini", ".mypy.ini"}) or "mypy" in pyproject,
        "tsc": "tsconfig.json" in names,
        "eslint": bool(names & set(ESLINT_CONFIGS)),
        "go_vet": "go.mod" in names,
        "cargo_check": "Cargo.toml" in names,
    }
    chosen = [tool for tool in detected if detected[tool]] if wanted == "auto" else [wanted]
    builders = {
        "ruff": lambda: Plan("ruff", [_python_tool(context, "ruff"), "check", "--output-format=concise",
                                      "--no-cache", target or "."]),
        "mypy": lambda: Plan("mypy", [_python_tool(context, "mypy"), "--no-color-output", "--no-error-summary",
                                      "--show-column-numbers", target or "."]),
        "tsc": lambda: _node_plan(context, "tsc", ["--noEmit", "--pretty", "false"]),
        "eslint": lambda: _node_plan(context, "eslint", ["--format", "unix", target or "."]),
        "go_vet": lambda: Plan("go_vet", ["go", "vet", target or "./..."]),
        "cargo_check": lambda: Plan("cargo_check", ["cargo", "check", "--message-format=short", "--quiet"]),
    }
    return [builders[tool]() for tool in chosen]


def _pyproject_tools(context: Any, names: set[str]) -> dict[str, Any]:
    if "pyproject.toml" not in names:
        return {}
    try:
        tools = tomllib.loads(str(context.files.read("pyproject.toml").get("content") or "")).get("tool")
    except tomllib.TOMLDecodeError:
        return {}
    return tools if isinstance(tools, dict) else {}


def _exists(context: Any, path: str) -> bool:
    folder, _, name = path.rpartition("/")
    try:
        items = context.files.list(folder or ".").get("items") or []
    except Exception:  # noqa: BLE001 - a missing folder simply means "not installed"
        return False
    return any(isinstance(item, dict) and item.get("name") == name for item in items)


def _python_tool(context: Any, name: str) -> str:
    local = f".venv/bin/{name}"
    return local if _exists(context, local) else name


def _node_plan(context: Any, name: str, arguments: list[str]) -> Plan:
    binary = f"node_modules/.bin/{name}"
    if not _exists(context, binary):
        return Plan(name, [binary, *arguments], unavailable=f"{binary} is missing; the project's "
                    "dependencies are not installed (npm install), and this skill installs nothing.")
    return Plan(name, [binary, *arguments])


def parse_findings(tool: str, output: str) -> list[dict[str, Any]]:
    pattern = PARSERS.get(tool)
    findings: list[dict[str, Any]] = []
    for line in output.splitlines() if pattern else []:
        match = pattern.match(line.strip())
        if not match:
            continue
        found = match.groupdict()
        severity = (found.get("severity") or "error").lower()
        row: dict[str, Any] = {"file": found["file"], "line": int(found["line"]), "severity": severity,
                               "code": found.get("code") or "", "message": found["message"].strip()[:300]}
        if found.get("column"):
            row["column"] = int(found["column"])
        findings.append(row)
    return findings


def classify(result: dict[str, Any], findings: list[dict[str, Any]], output: str) -> str:
    if result.get("timed_out"):
        return "timeout"
    if result.get("denied"):
        return "error"
    if findings:
        return "findings"
    if result.get("returncode") == 0:
        return "clean"
    missing = re.search(r"No module named|command not found|not recognized|No such file or directory", output)
    return "unavailable" if missing or result.get("returncode") in (126, 127) else "error"


def overall_status(results: list[dict[str, Any]]) -> str:
    statuses = {result["status"] for result in results}
    for status in ("error", "timeout", "findings"):
        if status in statuses:
            return status
    return "incomplete" if "unavailable" in statuses else "clean"


def _save_log(context: Any, text: str) -> str:
    EXCHANGE_OUT.mkdir(parents=True, exist_ok=True)
    path = EXCHANGE_OUT / f"static-checks-{int(time.time())}.txt"
    path.write_text(text, encoding="utf-8")
    return context.artifacts.save(str(path), name=path.name, kind="check-log",
                                  caption="Full static check output")["path"]
