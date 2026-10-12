"""Building skill packages for tests: a manifest, instructions and optional plugin code."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from kernel.plugins.grants import GrantStore
from kernel.plugins.registry import SkillRegistry
from kernel.plugins.sandbox import runner_unavailable_reason

requires_runner = pytest.mark.skipif(bool(runner_unavailable_reason()), reason=runner_unavailable_reason() or "")

ECHO_TOOL = {
    "name": "say",
    "summary": "Echo the text back.",
    "readonly": True,
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                   "additionalProperties": False},
}


def write_skill(
    root: Path,
    skill_id: str = "echo",
    *,
    version: str = "0.1.0",
    kind: str = "tools",
    tools: list[dict[str, Any]] | None = None,
    capabilities: list[Any] | None = None,
    plugin: str | None = None,
    instructions: str = "# Echo\nUse say to echo.\n",
    extra: dict[str, Any] | None = None,
) -> Path:
    folder = root / f"{skill_id}-{version}"
    folder.mkdir(parents=True)
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "id": skill_id,
        "version": version,
        "plugin_api": 1,
        "kind": kind,
        "name": {"en": skill_id.title(), "ru": skill_id.title()},
        "summary": {"en": f"The {skill_id} test skill", "ru": f"Тестовый скилл {skill_id}"},
        "platforms": ["linux"],
        "capabilities": {"required": capabilities if capabilities is not None else ["input.read"]},
        "tools": tools if tools is not None else ([ECHO_TOOL] if kind == "tools" else []),
        **(extra or {}),
    }
    if kind != "tools":
        manifest.pop("tools")
        manifest["capabilities"] = {"required": []}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "SKILL.md").write_text(instructions, encoding="utf-8")
    if kind == "tools":
        source = plugin if plugin is not None else ECHO_PLUGIN
        (folder / "plugin.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return folder


ECHO_PLUGIN = """
def describe():
    return {"tools": ["say"]}

def execute(tool_name, arguments, context):
    task = context.input.task()
    return {"echo": arguments["text"], "task_text": task["text"]}
"""


def install(registry: SkillRegistry, source: Path) -> Any:
    result = registry.install(source)
    registry.activate(result.skill_id, result.version, result.digest)
    return result


def grant_all(grants: GrantStore, registry: SkillRegistry, skill_id: str, workspace: Path) -> None:
    entry = registry.entry(skill_id)
    assert entry is not None and entry.manifest is not None
    grants.put(skill_id, entry.digest, workspace, entry.manifest.required_capabilities())
