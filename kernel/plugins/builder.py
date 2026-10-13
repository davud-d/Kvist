# -*- coding: utf-8 -*-
"""Building a skill inside a task: a draft in the workspace, independent checks, the person's approval.

The model writes a draft package with its ordinary file tools (``scaffold``
starts one) in a workspace folder — never in the installed-skills directory,
the grants or the core, which no workspace action can reach. ``check`` copies
the draft into a private staging directory and runs ``checks.check_package``
on that copy. ``install`` accepts only the exact content that passed a check
in this task, asks the person in a dialog that shows what the package does,
what changes, its permissions, the check results and its dependencies, and
only then installs, activates and grants it for this workspace. Nothing here
can approve itself, and a draft that changed after its check must be checked
again.

Limits per task (documented in docs/SKILLS.md): ``MAX_CHECKS_PER_TASK``
checks, ``MAX_DRAFTS_PER_TASK`` different drafts, ``MAX_INSTALLS_PER_TASK``
installations. A helper cannot build or install: it has no person to ask.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel.plugins import capabilities as caps
from kernel.plugins.checks import CheckReport, check_package
from kernel.plugins.manifest import MAX_PACKAGE_BYTES, ManifestError, load_manifest, package_digest, package_files, read_package_file
from kernel.plugins.registry import RegistryError, SkillRegistry

MAX_CHECKS_PER_TASK = 10
MAX_DRAFTS_PER_TASK = 3
MAX_INSTALLS_PER_TASK = 2
DRAFTS_FOLDER = "kvist-skills"


class BuildError(Exception):
    """A build step the core refuses; the message goes to the model."""


@dataclass(slots=True)
class _Checked:
    digest: str
    ok: bool
    report: CheckReport
    at: float


@dataclass(slots=True)
class BuildLedger:
    """What this task built: counts for the limits and the checks an install may rely on."""

    checks: int = 0
    drafts: set[str] = field(default_factory=set)
    installs: int = 0
    checked: dict[str, _Checked] = field(default_factory=dict)


def draft_folder(workspace: Path, relative: str) -> Path:
    """A draft folder inside the workspace (never .kvist, never through a link)."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or ".kvist" in [part.lower() for part in candidate.parts]:
        raise BuildError("the draft must be a folder inside the workspace (not .kvist)")
    target = (workspace / candidate)
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        raise BuildError(f"{relative} does not exist") from None
    if workspace.resolve() not in resolved.parents or not resolved.is_dir() or target.is_symlink():
        raise BuildError(f"{relative} is not a folder inside the workspace")
    return resolved


def snapshot_draft(folder: Path, into: Path) -> Path:
    """Copy the draft's files (no links, bounded) so checks and install see one fixed content."""
    target = into / "package"
    target.mkdir(mode=0o700)
    for relative, _path in package_files(folder):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(read_package_file(folder, relative, MAX_PACKAGE_BYTES))
    return target


def check_draft(agent: Any, ledger: BuildLedger, relative: str) -> dict[str, Any]:
    if agent.is_helper:
        raise BuildError("a helper cannot build skills")
    if ledger.checks >= MAX_CHECKS_PER_TASK:
        raise BuildError(f"this task already ran {MAX_CHECKS_PER_TASK} skill checks; that limit stops endless fix loops")
    folder = draft_folder(Path(agent.executor.workspace), relative)
    if relative not in ledger.drafts and len(ledger.drafts) >= MAX_DRAFTS_PER_TASK:
        raise BuildError(f"this task already works on {MAX_DRAFTS_PER_TASK} skill drafts")
    ledger.drafts.add(relative)
    ledger.checks += 1
    agent._ui.update_live(user_message=f"checking skill draft {relative}", status="skill check",
                          step=getattr(agent, "_current_step", 0), progress=agent._state.progress, plan_step="verify")
    with tempfile.TemporaryDirectory(prefix="kvist-draft-") as raw:
        try:
            package = snapshot_draft(folder, Path(raw))
        except ManifestError as error:
            raise BuildError(str(error)) from None
        report = check_package(package, registry=agent.skills.registry)
    ledger.checked[relative] = _Checked(report.digest, report.ok, report, time.time())
    return {"path": relative, "ok": report.ok, "summary": report.summary(), "report": report.as_dict(),
            "checks_left": MAX_CHECKS_PER_TASK - ledger.checks,
            "note": "Passing shows the contract holds in the cases tried; the sandbox stays on for every real call."}


def install_draft(agent: Any, ledger: BuildLedger, relative: str) -> dict[str, Any]:
    if agent.is_helper:
        raise BuildError("a helper cannot install skills")
    if ledger.installs >= MAX_INSTALLS_PER_TASK:
        raise BuildError(f"this task already installed {MAX_INSTALLS_PER_TASK} skills")
    checked = ledger.checked.get(relative)
    if checked is None or not checked.ok:
        raise BuildError(f"{relative} has no passing check in this task; run skills op check first")
    if not getattr(agent.config, "interactive", False):
        raise BuildError("installing a skill needs the person's approval, and nobody can answer here")
    folder = draft_folder(Path(agent.executor.workspace), relative)
    registry: SkillRegistry = agent.skills.registry
    with tempfile.TemporaryDirectory(prefix="kvist-install-") as raw:
        package = snapshot_draft(folder, Path(raw))
        digest = package_digest(package)
        if digest != checked.digest:
            raise BuildError(f"{relative} changed after its check; check it again before installing")
        manifest = load_manifest(package)
        title, body = install_text(manifest, registry, checked.report, agent._state.response_language or "en")
        agent.control.awaiting_input(True)
        try:
            approved = bool(agent._ui.confirm(title, body))
        finally:
            agent.control.awaiting_input(False)
        if not approved:
            raise BuildError("the person did not approve installing this skill; it was not installed")
        try:
            result = registry.install(package, origin="builder")
        except RegistryError as error:
            raise BuildError(str(error)) from None
        if result.digest != digest:
            raise BuildError("the installed content differs from the checked content; nothing was activated")
        registry.activate(result.skill_id, result.version, result.digest)
    ledger.installs += 1
    agent.skills.grants.put(manifest.id, digest, Path(agent.executor.workspace), manifest.required_capabilities())
    agent.skills.defer_until_restart(manifest.id)
    agent.skills._catalog_cache = None
    deps_note = ""
    if manifest.python_deps and not registry.env_ready(manifest):
        from kernel.plugins.deps import DependencyError, install_dependencies

        agent._ui.update_live(user_message=f"installing dependencies of {manifest.id}", status="skill install",
                              step=getattr(agent, "_current_step", 0), progress=agent._state.progress,
                              plan_step="verify")
        try:
            install_dependencies(registry, manifest)
        except DependencyError as error:
            deps_note = f"Dependencies were not installed: {error}"
    entry = registry.entry(manifest.id)
    return {"installed": manifest.id, "version": manifest.version, "digest": digest,
            "available": False, "available_after_restart": bool(entry and entry.available),
            "reason": entry.reason if entry and not entry.available else "",
            "note": deps_note or "Approved for this workspace. It cannot be attached in this session: it becomes "
                    "available after Kvist is restarted. Tell the person that."}


def install_text(manifest, registry: SkillRegistry, report: CheckReport, language: str) -> tuple[str, str]:
    """The install dialog: the package, what changes against what is installed, permissions, checks, dependencies."""
    ru = language == "ru"
    current = registry.entry(manifest.id)
    lines = [f"{manifest.label(language)} {manifest.version} — {manifest.short(language)}",
             ("Создан агентом в этой задаче." if ru else "Built by the agent in this task.")]
    if current is not None and current.manifest is not None:
        before = set(current.manifest.required_capabilities())
        after = set(manifest.required_capabilities())
        lines.append((f"Заменит установленную версию {current.version}." if ru else
                      f"Replaces the installed version {current.version}."))
        if after - before:
            lines.append(("Новые права: " if ru else "New permissions: ") + ", ".join(sorted(after - before)))
        if before - after:
            lines.append(("Больше не нужны: " if ru else "No longer needed: ") + ", ".join(sorted(before - after)))
    if report.similar:
        lines.append(("Похожие установленные скиллы: " if ru else "Similar installed skills: ") + ", ".join(report.similar))
    lines.append("")
    lines.append("Скилл сможет:" if ru else "It will be able to:")
    for request in manifest.capabilities:
        if not request.optional:
            lines.append(f"• {caps.describe(request.name, request.scope, language)}")
    if manifest.python_deps:
        lines.append("")
        lines.append(("Будут скачаны зависимости (только готовые колёса, с PyPI): " if ru else
                      "Dependencies to download (binary wheels only, from PyPI): ") + ", ".join(manifest.python_deps))
    lines.append("")
    lines.append(("Проверки Kvist: " if ru else "Kvist's checks: ") + report.summary())
    lines.append("")
    lines.append(("Проверки показывают соблюдение контракта в опробованных случаях, а не отсутствие вреда; "
                  "код работает только в песочнице с перечисленными правами." if ru else
                  "Checks show the contract holds in the cases tried, not that the code is harmless; it runs "
                  "only in the sandbox with the permissions listed."))
    title = (f"Установить скилл «{manifest.label(language)}»?" if ru else f"Install the skill \"{manifest.label(language)}\"?")
    return title, "\n".join(lines)


_TEMPLATE_PLUGIN = '''# -*- coding: utf-8 -*-
"""{name}: a Kvist skill. See docs/SKILLS.md for the API of ``context``."""


def describe():
    return {{"tools": ["{tool}"]}}


def execute(tool_name, arguments, context):
    if tool_name == "{tool}":
        return {{"echo": arguments["text"]}}
    raise ValueError(f"unknown tool {{tool_name}}")
'''

_TEMPLATE_TEST = '''import unittest

import plugin


class FakeContext:
    pass


class ToolTest(unittest.TestCase):
    def test_{tool}_echoes(self):
        self.assertEqual(plugin.execute("{tool}", {{"text": "hi"}}, FakeContext()), {{"echo": "hi"}})


if __name__ == "__main__":
    unittest.main()
'''


def scaffold_files(skill_id: str, name: str, summary: str) -> dict[str, str]:
    """The files of a minimal tools skill; the model edits them like any workspace files."""
    tool = "run"
    manifest = {
        "manifest_version": 1, "id": skill_id, "version": "0.1.0", "plugin_api": 1, "kind": "tools",
        "name": {"en": name}, "summary": {"en": summary}, "instructions": "SKILL.md", "entrypoint": "plugin.py",
        "platforms": ["linux"], "source": {"origin": "builder"},
        "capabilities": {"required": [], "optional": []},
        "tools": [{"name": tool, "summary": "Describe what this tool does.", "readonly": True,
                   "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                                  "required": ["text"], "additionalProperties": False}}],
    }
    return {
        "manifest.json": json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        "SKILL.md": f"# {name}\n\n{summary}\n\n## Tools\n\n- `{skill_id}__{tool}`: describe when to use it.\n",
        "plugin.py": _TEMPLATE_PLUGIN.format(name=name, tool=tool),
        "tests/test_plugin.py": _TEMPLATE_TEST.format(tool=tool),
        "tests/contract.json": json.dumps({tool: {"text": "contract sample"}}, indent=2) + "\n",
    }
