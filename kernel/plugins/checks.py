# -*- coding: utf-8 -*-
"""Independent checks of a skill package: the core's own tests, separate from the author's.

The author's tests say whether the skill does its job. These say whether it
keeps the plugin contract, whatever its tests claim:

* static: the manifest, the files, Python syntax (compiled, never executed),
  the permission list kept to what the tools need;
* in the sandbox, with exactly the declared permissions: ``describe()``
  matches the manifest, every tool answers sample arguments within its time
  and size limits and its declared result schema, a cancel ends a call, a
  request for anything undeclared is caught, and nothing keeps running after
  the runner is stopped;
* the author's ``tests/`` under ``unittest``, in the same kind of sandbox,
  without network.

Passing proves none of these is broken in the cases tried; it does not prove
the skill harmless. The sandbox and the per-request permission checks stay on
for every real call.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kernel.plugins.capabilities import CAPABILITIES
from kernel.plugins.grants import Grant
from kernel.plugins.host import RunnerHost, RunnerStartError, ServiceError
from kernel.plugins.manifest import (
    KIND_TOOLS,
    Manifest,
    ManifestError,
    load_manifest,
    package_digest,
    package_files,
    read_package_file,
    MAX_INSTRUCTIONS_BYTES,
    MAX_PACKAGE_BYTES,
)
from kernel.plugins.net_broker import TaskServices
from kernel.plugins.registry import CatalogEntry, SkillRegistry
from kernel.plugins.sandbox import runner_unavailable_reason
from kernel.plugins.resources import Owner, ResourceManager
from kernel.agent.task_control import TaskControl

CONTRACT_SAMPLES = "tests/contract.json"
AUTHOR_TEST_TIMEOUT = 120.0


@dataclass(slots=True)
class Finding:
    check: str
    ok: bool
    detail: str = ""
    severity: str = "error"  # error | warning


@dataclass(slots=True)
class CheckReport:
    skill: str = ""
    version: str = ""
    digest: str = ""
    findings: list[Finding] = field(default_factory=list)
    author_tests: dict[str, Any] = field(default_factory=dict)
    similar: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(item.ok or item.severity == "warning" for item in self.findings) and \
            self.author_tests.get("status") in (None, "passed", "none")

    def add(self, check: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        self.findings.append(Finding(check, ok, detail, severity))

    def as_dict(self) -> dict[str, Any]:
        return {"skill": self.skill, "version": self.version, "digest": self.digest, "ok": self.ok,
                "findings": [asdict(item) for item in self.findings], "author_tests": self.author_tests,
                "similar_installed": self.similar}

    def summary(self) -> str:
        failed = [f"{item.check}: {item.detail}" for item in self.findings if not item.ok and item.severity == "error"]
        warnings = [f"{item.check}: {item.detail}" for item in self.findings if not item.ok and item.severity == "warning"]
        lines = [f"{self.skill} {self.version}: {'passed' if self.ok else 'FAILED'}"]
        lines += [f"  failed — {line}" for line in failed]
        lines += [f"  warning — {line}" for line in warnings]
        if self.author_tests:
            lines.append(f"  author tests: {self.author_tests.get('status')} {self.author_tests.get('summary', '')}".rstrip())
        if self.similar:
            lines.append(f"  similar installed skills: {', '.join(self.similar)}")
        return "\n".join(lines)


def check_package(path: Path | None, *, registry: SkillRegistry | None = None, run_sandbox: bool = True) -> CheckReport:
    report = CheckReport()
    if path is None:
        report.add("package", False, "no package")
        return report
    try:
        manifest = load_manifest(path)
        report.digest = package_digest(path)
    except ManifestError as error:
        report.add("manifest", False, str(error))
        return report
    report.skill, report.version = manifest.id, manifest.version
    report.add("manifest", True)
    _static_checks(path, manifest, report)
    if registry is not None:
        report.similar = similar_skills(manifest, registry)
    if manifest.kind != KIND_TOOLS or manifest.core_group or not run_sandbox:
        return report
    reason = runner_unavailable_reason()
    if reason:
        report.add("sandbox", False, reason)
        return report
    if manifest.python_deps and (registry is None or not registry.env_ready(manifest)):
        report.add("dependencies", False,
                   "Python dependencies are not installed, so the sandbox checks cannot run; install them first",
                   severity="warning")
        return report
    _sandbox_checks(path, manifest, report, registry)
    _author_tests(path, manifest, report, registry)
    return report


# --- static ----------------------------------------------------------------


def _static_checks(path: Path, manifest: Manifest, report: CheckReport) -> None:
    files = [relative for relative, _ in package_files(path)]
    report.add("instructions", manifest.instructions in files, f"{manifest.instructions} is missing"
               if manifest.instructions not in files else "")
    for relative in files:
        if not relative.endswith(".py"):
            continue
        source = read_package_file(path, relative, MAX_PACKAGE_BYTES).decode("utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            report.add("syntax", False, f"{relative}:{error.lineno}: {error.msg}")
            continue
        if not relative.startswith("tests/"):
            _check_declared_ctx_calls(tree, relative, manifest, report)
        if relative == manifest.entrypoint:
            defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
            missing = {"describe", "execute"} - defined
            report.add("entrypoint", not missing, f"{relative} does not define {', '.join(sorted(missing))}()"
                       if missing else "")
    egress = [request.name for request in manifest.capabilities if CAPABILITIES[request.name].egress]
    for request in manifest.capabilities:
        if CAPABILITIES[request.name].egress and not request.reason:
            report.add("permissions", False, f"{request.name} sends data off the machine but gives no reason",
                       severity="warning")
        if request.scope.get("domains") == ["*"] and not request.optional:
            report.add("permissions", False, f"{request.name} asks for every internet address up front; ask per "
                       "address at run time (optional + permissions.request)", severity="warning")
    report.add("permissions_listed", True, ", ".join(request.name for request in manifest.capabilities) or "none")
    if egress:
        report.add("egress", True, f"sends data off the machine through: {', '.join(egress)}", severity="warning")
    _check_references(path, manifest, files, report)


def _check_references(path: Path, manifest: Manifest, files: list[str], report: CheckReport) -> None:
    """Reference pages are read one at a time by `skills read`; a page that cannot be read is found here, not mid-task."""
    shipped = [relative for relative in files if relative.startswith("references/")]
    instructions = ""
    if manifest.instructions in files:
        instructions = read_package_file(path, manifest.instructions, MAX_PACKAGE_BYTES).decode("utf-8", errors="replace")
    named = set(re.findall(r"references/[\w.-]+\.md", instructions))
    if not shipped and not named:
        return
    readable = set()
    for relative in shipped:
        if relative.count("/") != 1 or not relative.endswith(".md"):
            report.add("references", False, f"{relative} cannot be read by the model: only references/*.md pages are",
                       severity="warning")
            continue
        data = read_package_file(path, relative, MAX_PACKAGE_BYTES)
        if len(data) > MAX_INSTRUCTIONS_BYTES:
            report.add("references", False, f"{relative} is larger than {MAX_INSTRUCTIONS_BYTES} bytes; split it")
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            report.add("references", False, f"{relative} is not UTF-8")
            continue
        readable.add(relative)
    for missing in sorted(named - set(shipped)):
        report.add("references", False, f"{manifest.instructions} points to {missing}, which the package lacks")
    for unnamed in sorted(readable - named):
        report.add("references", False, f"{unnamed} is never named in {manifest.instructions}, so the model will not "
                   "know when to read it", severity="warning")
    report.add("references", True, f"{len(readable)} reference pages")


def _check_declared_ctx_calls(tree: ast.AST, relative: str, manifest: Manifest, report: CheckReport) -> None:
    """Fail a ``ctx.<group>.<method>()`` call whose capability the manifest never declares.

    The sandbox check only reaches the calls a tool makes on sample arguments, so a
    skill whose dependencies are not installed would otherwise ship a call the core refuses.
    """
    from kernel.plugins.services import METHODS

    declared = {request.name for request in manifest.capabilities}
    reported: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Attribute)):
            continue
        method = f"{node.func.value.attr}.{node.func.attr}"
        capability = METHODS.get(method, (None, False))[0]
        if capability is None or capability in declared or method in reported:
            continue
        reported.add(method)
        report.add("undeclared_ctx_call", False,
                   f"{relative}:{node.lineno}: ctx.{method}() needs {capability}, which the manifest does not declare")


def similar_skills(manifest: Manifest, registry: SkillRegistry) -> list[str]:
    """Installed skills whose purpose or tools overlap this one's (other than its own id)."""
    words = _words(f"{manifest.short('en')} {' '.join(tool.name for tool in manifest.tools)}")
    similar = []
    for entry in registry.catalog():
        if entry.id == manifest.id or entry.manifest is None:
            continue
        other = _words(f"{entry.short('en')} {' '.join(tool.name for tool in entry.manifest.tools)}")
        if words and other and len(words & other) / min(len(words), len(other)) >= 0.5:
            similar.append(entry.id)
    return similar


def _words(text: str) -> set[str]:
    import re

    return {word for word in re.findall(r"[a-z]{4,}", text.lower())}


# --- sandbox contract ------------------------------------------------------


class _RecordingServices:
    """A minimal core for checks: answers the safe methods, records every refusal."""

    def __init__(self, workspace: Path, granted: set[str]) -> None:
        self.workspace = workspace
        self.granted = granted
        self.refused: list[str] = []
        self.requests: list[str] = []

    def handle(self, method: str, params: dict[str, Any], call_id: str) -> Any:
        from kernel.plugins.services import METHODS

        self.requests.append(method)
        spec = METHODS.get(method)
        if spec is None:
            self.refused.append(f"{method} (not part of the API)")
            raise ServiceError("unknown_method", f"{method} is not part of plugin API v1")
        capability, _changes = spec
        if capability is not None and capability not in self.granted:
            self.refused.append(f"{method} (needs {capability}, not declared)")
            raise ServiceError("denied", f"{capability} is not declared")
        if method == "input.task":
            return {"text": "contract check", "updates": [], "language": "en", "session_id": "check",
                    "run_id": "check", "call_id": call_id, "message_id": 0, "skill": {}}
        if method in ("runtime.check", "runtime.progress", "ui.notify", "ui.message", "log.write"):
            return None
        if method == "runtime.status":
            return {"state": "running", "paused": False, "cancelled": False, "deadline_seconds": 30}
        if method == "runtime.wait":
            time.sleep(min(float(params.get("seconds", 0) or 0), 0.2))
            return None
        if method == "runtime.register_resource":
            return {"resource_id": "check"}
        if method in ("runtime.release_resource",):
            return None
        raise ServiceError("unavailable", f"{method} is not simulated by the contract check")


def _sandbox_checks(path: Path, manifest: Manifest, report: CheckReport, registry: SkillRegistry | None) -> None:
    with tempfile.TemporaryDirectory(prefix="kvist-check-") as raw:
        scratch = Path(raw)
        workspace = scratch / "workspace"
        workspace.mkdir()
        resources = ResourceManager(workspace, runtime_root=_short_runtime(scratch))
        digest = package_digest(path)
        entry = CatalogEntry(manifest.id, manifest.version, digest, True, "", manifest, path)
        granted = {request.name for request in manifest.capabilities}
        grant = Grant(manifest.id, digest, str(workspace), {name: {} for name in granted}, time.time())
        owner = Owner(session="check", run_id="check", kind="skill", skill=f"{manifest.id}@{manifest.version}")
        host = RunnerHost(entry, grant, resources=resources, owner=owner, registry=registry or SkillRegistry(scratch),
                          services=TaskServices())
        services = _RecordingServices(workspace, granted)
        try:
            host.start()
        except RunnerStartError as error:
            report.add("runner_start", False, str(error))
            resources.shutdown("check_failed")
            return
        report.add("runner_start", True)
        report.add("describe", True, f"offers {', '.join(sorted(host.described)) or 'nothing'}")
        samples = _samples(path)
        for tool in manifest.tools:
            if tool.name not in host.described:
                continue
            arguments = samples.get(tool.name) or _minimal_arguments(tool.parameters)
            outcome = host.call(tool, arguments, services.handle, _running_control())
            if outcome.status in ("success", "partial"):
                report.add(f"tool:{tool.name}", True, f"{outcome.status} in {outcome.duration:.1f} s")
            elif outcome.error_code in ("result_too_large", "bad_result", "runner_crashed", "protocol_error",
                                        "runner_stopped"):
                report.add(f"tool:{tool.name}", False, f"{outcome.error_code}: {outcome.error_message}")
            else:
                report.add(f"tool:{tool.name}", True, f"answered {outcome.status} ({outcome.error_message[:120]})",
                           severity="warning")
            if not host.alive:
                break
        if services.refused:
            report.add("undeclared_access", False, "the skill asked for: " + "; ".join(sorted(set(services.refused))))
        else:
            report.add("undeclared_access", True)
        _cancel_check(host, manifest, samples, report)
        pid = host.process.pid if host.process else None
        outcomes = host.stop()
        leftovers = [outcome for outcome in outcomes if not outcome.stopped]
        gone = pid is None or not Path(f"/proc/{pid}").exists()
        report.add("cleanup", not leftovers and gone,
                   "; ".join(f"{o.label}: {o.error}" for o in leftovers) or ("" if gone else "the runner still exists"))
        resources.shutdown("check_done")


def _cancel_check(host: RunnerHost, manifest: Manifest, samples: dict[str, Any], report: CheckReport) -> None:
    if not host.alive or not manifest.tools:
        return
    tool = next((item for item in manifest.tools if item.name in host.described), None)
    if tool is None:
        return
    control = _running_control()
    threading.Timer(0.3, lambda: control.request_cancel("check")).start()
    started = time.monotonic()
    outcome = host.call(tool, samples.get(tool.name) or _minimal_arguments(tool.parameters),
                        lambda method, params, call_id: _slow_answer(control, method), control)
    elapsed = time.monotonic() - started
    report.add("cancel", outcome.status in ("cancelled", "success", "partial", "error") and elapsed < 15,
               f"ended as {outcome.status} {elapsed:.1f} s after start")


def _slow_answer(control: TaskControl, method: str) -> Any:
    control.wait(1.0)
    raise ServiceError("cancelled", "the check cancelled this call")


def _running_control() -> TaskControl:
    control = TaskControl()
    control.begin()
    return control


def _samples(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(read_package_file(path, CONTRACT_SAMPLES, 256 * 1024).decode("utf-8"))
    except (ManifestError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _minimal_arguments(schema: dict[str, Any]) -> dict[str, Any]:
    defaults = {"string": "test", "integer": 1, "number": 1, "boolean": False, "array": [], "object": {}}
    arguments: dict[str, Any] = {}
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        spec = properties.get(name, {})
        if "enum" in spec:
            arguments[name] = spec["enum"][0]
            continue
        kind = spec.get("type", "string")
        kind = kind[0] if isinstance(kind, list) else kind
        value = defaults.get(kind, "test")
        if kind in ("integer", "number") and "minimum" in spec:
            value = spec["minimum"]
        arguments[name] = value
    return arguments


def _short_runtime(scratch: Path) -> Path:
    import os

    base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    runtime = Path(tempfile.mkdtemp(prefix="kvchk-", dir=base))
    return runtime


# --- author tests ------------------------------------------------------------


def _author_tests(path: Path, manifest: Manifest, report: CheckReport, registry: SkillRegistry | None) -> None:
    tests = [relative for relative, _ in package_files(path) if relative.startswith("tests/") and
             Path(relative).name.startswith("test_") and relative.endswith(".py")]
    if not tests:
        report.author_tests = {"status": "none", "summary": "the package has no tests/test_*.py"}
        report.add("author_tests_present", False, "no tests/test_*.py in the package", severity="warning")
        return
    from kernel.plugins.sandbox import RUNNER_DIR, runner_python
    from kernel.plugins.resources import ManagedProcess
    from kernel.sandbox.sandbox import RUNTIME_MOUNT_ROOTS, resolve_bubblewrap_path

    with tempfile.TemporaryDirectory(prefix="kvist-tests-") as raw:
        snapshot = Path(raw) / "skill"
        shutil.copytree(path, snapshot, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        argv = [resolve_bubblewrap_path(), "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL",
                "--clearenv", "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp",
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
        for root in RUNTIME_MOUNT_ROOTS:
            if Path(root).exists():
                argv += ["--ro-bind", root, root]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--ro-bind", str(snapshot), "/kvist/skill", "--ro-bind", str(RUNNER_DIR), "/kvist/runner"]
        site = []
        if manifest.python_deps and registry is not None and registry.env_ready(manifest):
            argv += ["--ro-bind", str(registry.env_dir(manifest)), "/kvist/env"]
            site = ["/kvist/env/site"]
        python_path = ":".join(["/kvist/skill", "/kvist/runner", *site])
        argv += ["--setenv", "PYTHONPATH", python_path, "--chdir", "/kvist/skill", "--",
                 runner_python(), "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
        process = ManagedProcess(argv, label=f"tests of {manifest.id}", env={"PATH": "/usr/bin:/bin"},
                                 memory_mb=2048, pids_max=256)
        process.start("check-tests")
        deadline = time.monotonic() + AUTHOR_TEST_TIMEOUT
        while process.alive() and time.monotonic() < deadline:
            time.sleep(0.1)
        timed_out = process.alive()
        process.stop(grace=1.0)
        output = process.output_tail(20000)
        code = process.proc.returncode if process.proc is not None else None
    tail = output.strip().splitlines()[-1] if output.strip() else ""
    status = "timeout" if timed_out else ("passed" if code == 0 else "failed")
    report.author_tests = {"status": status, "summary": tail, "output_tail": output[-4000:]}
    report.add("author_tests", status == "passed", "" if status == "passed" else f"{status}: {tail}")
