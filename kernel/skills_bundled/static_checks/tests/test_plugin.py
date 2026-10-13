import tempfile
import unittest
from pathlib import Path

import plugin

RUFF = """kernel/a.py:3:1: F401 [*] `os` imported but unused
kernel/b.py:10:5: E501 Line too long (120 > 100)
Found 2 errors.
"""
MYPY = """kernel/a.py:7:12: error: Incompatible types in assignment  [assignment]
kernel/a.py:7:12: note: see the docs
kernel/b.py:2: warning: unused ignore  [unused-ignore]
"""
TSC = "src/app.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
ESLINT = "/work/src/a.js:4:7: 'x' is assigned a value but never used. [Error/no-unused-vars]\n"
GO = "# example.com/m\n./main.go:9:2: unreachable code\n"
CARGO = "src/lib.rs:5:9: error[E0425]: cannot find value `y` in this scope\n"


class Files:
    def __init__(self, files):
        self.files = files

    def list(self, path="."):
        prefix = "" if path == "." else path.rstrip("/") + "/"
        names = {name[len(prefix):].split("/", 1)[0] for name in self.files if name.startswith(prefix)}
        return {"items": [{"name": name} for name in sorted(names)]}

    def read(self, path):
        return {"content": self.files[path]}


class Context:
    def __init__(self, files, outcomes=None):
        self.files = Files(files)
        self.outcomes = outcomes or {}
        self.commands = []
        self.ui = self
        self.processes = self
        self.artifacts = self

    def notify(self, text):
        pass

    def run(self, command, timeout=None):
        self.commands.append(command)
        for key, value in self.outcomes.items():
            if key in " ".join(command):
                return value
        return {"returncode": 0, "stdout": "", "stderr": ""}

    def save(self, path, name=None, kind=None, caption=None):
        return {"path": f"artifacts/{name}"}


class ParserTest(unittest.TestCase):
    def test_ruff(self):
        rows = plugin.parse_findings("ruff", RUFF)
        self.assertEqual(rows[0], {"file": "kernel/a.py", "line": 3, "column": 1, "severity": "error",
                                   "code": "F401", "message": "[*] `os` imported but unused"})
        self.assertEqual(len(rows), 2)

    def test_mypy_skips_notes_and_reads_optional_column(self):
        rows = plugin.parse_findings("mypy", MYPY)
        self.assertEqual([(r["line"], r["code"], r["severity"]) for r in rows],
                         [(7, "assignment", "error"), (2, "unused-ignore", "warning")])
        self.assertNotIn("column", rows[1])

    def test_tsc_eslint_go_cargo(self):
        self.assertEqual(plugin.parse_findings("tsc", TSC)[0]["code"], "TS2322")
        eslint = plugin.parse_findings("eslint", ESLINT)[0]
        self.assertEqual((eslint["code"], eslint["severity"]), ("no-unused-vars", "error"))
        self.assertEqual(plugin.parse_findings("go_vet", GO)[0]["file"], "./main.go")
        self.assertEqual(plugin.parse_findings("cargo_check", CARGO)[0]["code"], "E0425")

    def test_classification(self):
        finding = [{"severity": "error"}]
        self.assertEqual(plugin.classify({"returncode": 0}, [], ""), "clean")
        self.assertEqual(plugin.classify({"returncode": 1}, finding, ""), "findings")
        self.assertEqual(plugin.classify({"returncode": 0}, finding, ""), "findings")
        self.assertEqual(plugin.classify({"returncode": 1}, [], "boom"), "error")
        self.assertEqual(plugin.classify({"returncode": 1}, [], "No module named ruff"), "unavailable")
        self.assertEqual(plugin.classify({"timed_out": True}, [], ""), "timeout")

    def test_overall_never_calls_a_skipped_checker_clean(self):
        self.assertEqual(plugin.overall_status([{"status": "clean"}, {"status": "unavailable"}]), "incomplete")
        self.assertEqual(plugin.overall_status([{"status": "clean"}]), "clean")
        self.assertEqual(plugin.overall_status([{"status": "findings"}, {"status": "error"}]), "error")


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        plugin.EXCHANGE_OUT = Path(self.scratch.name)

    def tearDown(self):
        self.scratch.cleanup()

    def test_detects_from_configuration(self):
        context = Context({"pyproject.toml": "[tool.ruff]\nline-length = 100\n", "tsconfig.json": "{}",
                           ".venv/bin/ruff": "", "node_modules/.bin/tsc": "", "a.py": ""},
                          {"ruff": {"returncode": 1, "stdout": RUFF}})
        result = plugin.execute("run", {}, context)
        self.assertEqual(result["status"], "findings")
        self.assertEqual([row["tool"] for row in result["tools"]], ["ruff", "tsc"])
        self.assertEqual(context.commands[0][:2], [".venv/bin/ruff", "check"])
        self.assertEqual(result["tools"][0]["counts"], {"error": 2, "warning": 0})
        self.assertTrue(result["log_artifact"].startswith("artifacts/static-checks-"))

    def test_missing_node_binary_is_unavailable_and_not_run(self):
        context = Context({"tsconfig.json": "{}"})
        result = plugin.execute("run", {}, context)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(context.commands, [])
        self.assertNotIn("log_artifact", result)

    def test_no_configuration_says_so(self):
        result = plugin.execute("run", {}, Context({"notes.txt": ""}))
        self.assertEqual(result["status"], "no_tools")

    def test_named_tool_runs_without_configuration_and_target_is_passed(self):
        context = Context({"a.py": ""})
        result = plugin.execute("run", {"tool": "mypy", "target": "a.py"}, context)
        self.assertEqual(result["status"], "clean")
        self.assertEqual(context.commands[0][-1], "a.py")

    def test_target_cannot_be_an_option(self):
        with self.assertRaises(ValueError):
            plugin.execute("run", {"tool": "ruff", "target": "--fix"}, Context({}))


if __name__ == "__main__":
    unittest.main()
