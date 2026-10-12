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
