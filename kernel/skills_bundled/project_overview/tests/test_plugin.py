import json
import unittest

import plugin


class FakeFiles:
    def __init__(self, files, truncated=()):
        self.files = files
        self.truncated = set(truncated)

    def list(self, path="."):
        prefix = "" if path == "." else path.rstrip("/") + "/"
        names = {}
        for name in self.files:
            if name.startswith(prefix):
                head = name[len(prefix):].split("/", 1)
                names[head[0]] = "dir" if len(head) > 1 else "file"
        return {"items": [{"name": name, "type": kind} for name, kind in sorted(names.items())]}

    def find(self, pattern):
        suffix = pattern.lstrip("*")
        matches = [name for name in self.files if name.endswith(suffix)]
        return {"matches": matches, "truncated": pattern in self.truncated}

    def read(self, path):
        return {"content": self.files[path]}


class Context:
    def __init__(self, files, truncated=()):
        self.files = FakeFiles(files, truncated)


PYPROJECT = """
[project]
name = "demo"
[project.scripts]
demo = "demo.cli:main"
[tool.pytest.ini_options]
addopts = "-q"
[tool.ruff]
line-length = 100
"""


class ScanTest(unittest.TestCase):
    def scan(self, files, truncated=()):
        return plugin.execute("scan", {}, Context(files, truncated))

    def test_python_project(self):
        result = self.scan({"pyproject.toml": PYPROJECT, "README.md": "# Demo\nHello",
                            "demo/__init__.py": "", "demo/__main__.py": "", "tests/test_a.py": ""})
        self.assertEqual(result["languages"][0], {"language": "Python", "files": 3, "at_least": False})
        self.assertEqual(result["commands"]["test"], ["python -m pytest"])
        self.assertEqual(result["commands"]["lint"], ["ruff check ."])
        self.assertIn("demo = demo.cli:main", result["entry_points"])
        self.assertIn("demo/__main__.py", result["entry_points"])
        self.assertTrue(result["readme"].startswith("# Demo"))

    def test_node_scripts_are_read_not_guessed(self):
        package = json.dumps({"main": "dist/index.js", "scripts": {"test": "vitest", "lint": "eslint ."}})
        result = self.scan({"package.json": package, "src/a.ts": ""})
        self.assertEqual(result["commands"], {"test": ["npm test"], "lint": ["npm run lint"]})
        self.assertIn("main: dist/index.js", result["entry_points"])
        self.assertNotIn("build", result["commands"])

    def test_capped_search_is_reported(self):
        result = self.scan({"a.go": "", "go.mod": "module x"}, truncated={"*.go"})
        self.assertTrue(result["languages"][0]["at_least"])
        self.assertTrue(any("at_least" in note for note in result["notes"]))
        self.assertEqual(result["commands"]["test"], ["go test ./..."])

    def test_unknown_project_says_so(self):
        result = self.scan({"notes.txt": "x"})
        self.assertEqual(result["languages"], [])
        self.assertEqual(result["commands"], {})
        self.assertTrue(any("entry point" in note for note in result["notes"]))

    def test_vendored_entry_points_hidden_files_last_and_html_stripped_from_readme(self):
        result = self.scan({".hidden": "", "app.py": "", "README.md": "<p align=\"center\">\n<img src=x>\n</p>\n\n\n\n# Title\ntext",
                            ".venv/lib/site-packages/pkg/__main__.py": "", "tool/__main__.py": ""})
        flags = [item["name"].startswith(".") for item in result["top_level"]]
        self.assertEqual(flags, sorted(flags))
        self.assertTrue(flags[-1] and not flags[0])
        self.assertEqual([p for p in result["entry_points"] if p.endswith("__main__.py")], ["tool/__main__.py"])
        self.assertNotIn("<", result["readme"])
        self.assertIn("# Title", result["readme"])

    def test_broken_package_json_is_a_note(self):
        result = self.scan({"package.json": "{not json"})
        self.assertTrue(any("not valid JSON" in note for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
