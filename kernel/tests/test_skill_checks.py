"""The core's independent package checks catch contract breaks the author's own tests miss."""
from __future__ import annotations

from kernel.plugins.checks import check_package
from kernel.tests.skill_fixtures import ECHO_TOOL, requires_runner, write_skill

GREEN_AUTHOR_TEST = '''
import unittest


class Passes(unittest.TestCase):
    def test_it_is_fine(self):
        self.assertTrue(True)
'''


def _with_tests(folder, source=GREEN_AUTHOR_TEST):
    (folder / "tests").mkdir()
    (folder / "tests" / "test_plugin.py").write_text(source, encoding="utf-8")
    return folder


def _finding(report, check):
    return next(item for item in report.findings if item.check == check)


@requires_runner
def test_a_well_behaved_package_passes(tmp_path):
    report = check_package(_with_tests(write_skill(tmp_path / "src")))

    assert report.ok, report.summary()
    assert _finding(report, "tool:say").ok
    assert _finding(report, "cleanup").ok
    assert report.author_tests["status"] == "passed"


@requires_runner
def test_undeclared_access_fails_even_with_green_author_tests(tmp_path):
    plugin = """
def describe():
    return {"tools": ["say"]}
def execute(tool_name, arguments, context):
    try:
        context.files.read("secret.txt")
    except Exception:
        pass
    return {"echo": arguments["text"]}
"""
    report = check_package(_with_tests(write_skill(tmp_path / "src", plugin=plugin)))

    assert report.author_tests["status"] == "passed"
    assert not report.ok
    assert "files.read" in _finding(report, "undeclared_access").detail


@requires_runner
def test_a_result_outside_the_declared_schema_fails(tmp_path):
    tool = {**ECHO_TOOL, "result": {"type": "object", "properties": {"echo": {"type": "string"}},
                                    "required": ["echo"], "additionalProperties": False}}
    plugin = """
def describe():
    return {"tools": ["say"]}
def execute(tool_name, arguments, context):
    return {"echo": 5, "extra": True}
"""
    report = check_package(_with_tests(write_skill(tmp_path / "src", plugin=plugin, tools=[tool])))

    assert not report.ok
    assert "bad_result" in _finding(report, "tool:say").detail


@requires_runner
def test_describe_offering_more_than_the_manifest_fails_to_start(tmp_path):
    plugin = """
def describe():
    return {"tools": ["say", "format_disk"]}
def execute(tool_name, arguments, context):
    return {}
"""
    report = check_package(write_skill(tmp_path / "src", plugin=plugin))

    assert not report.ok
    assert "does not declare" in _finding(report, "runner_start").detail


def test_syntax_errors_and_a_missing_execute_are_found_without_running_code(tmp_path):
    marker = tmp_path / "ran"
    plugin = f"open({str(marker)!r}, 'w')\ndef describe(:\n"
    report = check_package(write_skill(tmp_path / "src", plugin=plugin), run_sandbox=False)

    assert not report.ok
    assert "syntax" in [item.check for item in report.findings if not item.ok]
    assert not marker.exists()


@requires_runner
def test_failing_author_tests_fail_the_check(tmp_path):
    failing = GREEN_AUTHOR_TEST.replace("assertTrue(True)", "assertTrue(False)")
    report = check_package(_with_tests(write_skill(tmp_path / "src"), failing))

    assert report.author_tests["status"] == "failed"
    assert not report.ok


@requires_runner
def test_author_tests_run_without_network_or_the_persons_files(tmp_path):
    probing = '''
import os, socket, unittest


class Probe(unittest.TestCase):
    def test_isolated(self):
        self.assertFalse(os.path.exists(os.path.expanduser("~/.kvist")))
        self.assertFalse(os.path.exists("/home"))
        with self.assertRaises(OSError):
            socket.create_connection(("1.1.1.1", 53), timeout=2)
'''
    report = check_package(_with_tests(write_skill(tmp_path / "src"), probing))

    assert report.author_tests["status"] == "passed", report.author_tests.get("output_tail")


ASKING_PLUGIN = """
def describe():
    return {"tools": ["say"]}
def execute(tool_name, arguments, context):
    context.permissions.request("network.internet", "open a site", scope={"domains": ["example.com"]})
    return {"echo": arguments["text"]}
"""


def test_a_call_to_an_undeclared_capability_fails_without_running_the_skill(tmp_path):
    report = check_package(write_skill(tmp_path / "src", plugin=ASKING_PLUGIN), run_sandbox=False)

    assert not report.ok
    assert "permissions.request" in _finding(report, "undeclared_ctx_call").detail


def test_declaring_the_capability_clears_the_static_finding(tmp_path):
    folder = write_skill(tmp_path / "src", plugin=ASKING_PLUGIN,
                         capabilities=["input.read", "permissions.request"])

    report = check_package(folder, run_sandbox=False)

    assert report.ok, report.summary()


def test_every_bundled_skill_declares_what_its_code_calls():
    from pathlib import Path

    import kernel

    bundled = Path(kernel.__file__).parent / "skills_bundled"
    for package in sorted(path for path in bundled.iterdir() if (path / "manifest.json").is_file()):
        report = check_package(package, run_sandbox=False)
        assert report.ok, f"{package.name}: {report.summary()}"


def test_check_finds_reference_pages_the_model_could_not_read(tmp_path):
    from kernel.plugins.manifest import MAX_INSTRUCTIONS_BYTES

    folder = write_skill(tmp_path / "src", "guide", kind="instructions",
                         instructions="Read references/ok.md, references/big.md, references/cp.md and references/gone.md.\n")
    pages = folder / "references"
    pages.mkdir()
    (pages / "ok.md").write_text("# Fine\n", encoding="utf-8")
    (pages / "big.md").write_text("x" * (MAX_INSTRUCTIONS_BYTES + 1), encoding="utf-8")
    (pages / "cp.md").write_bytes("Привет".encode("cp1251"))
    (pages / "extra.md").write_text("# Never named\n", encoding="utf-8")

    report = check_package(folder, run_sandbox=False)

    problems = {(item.detail.split(" ")[0], item.severity) for item in report.findings
                if item.check == "references" and not item.ok}
    assert not report.ok
    assert ("references/big.md", "error") in problems and ("references/cp.md", "error") in problems
    assert any("references/gone.md, which the package lacks" in item.detail for item in report.findings)
    assert ("references/extra.md", "warning") in problems


def test_bundled_reference_pages_match_what_their_instructions_name():
    """A page the instructions point to but the package lacks is a dead end for the model; an unnamed one is never read."""
    import re
    from pathlib import Path

    import kernel
    from kernel.plugins.manifest import MAX_INSTRUCTIONS_BYTES

    bundled = Path(kernel.__file__).parent / "skills_bundled"
    for package in sorted(path for path in bundled.iterdir() if (path / "references").is_dir()):
        shipped = {f"references/{page.name}" for page in (package / "references").glob("*.md")}
        named = set(re.findall(r"references/[a-z0-9_-]+\.md", (package / "SKILL.md").read_text(encoding="utf-8")))
        assert named == shipped, f"{package.name}: named {sorted(named - shipped)}, unnamed {sorted(shipped - named)}"
        for page in shipped:
            assert (package / page).stat().st_size <= MAX_INSTRUCTIONS_BYTES, f"{package.name}/{page} is too large"
