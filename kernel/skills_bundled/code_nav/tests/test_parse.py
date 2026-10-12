import unittest

import plugin


class ParseTest(unittest.TestCase):
    def test_pyflakes_lines_become_findings(self):
        text = "app.py:3:1: 'os' imported but unused\nlib/x.py:10: undefined name 'foo'\n"
        self.assertEqual(plugin.parse_findings(text), [
            {"path": "app.py", "line": 3, "column": 1, "message": "'os' imported but unused"},
            {"path": "lib/x.py", "line": 10, "column": None, "message": "undefined name 'foo'"},
        ])


if __name__ == "__main__":
    unittest.main()
