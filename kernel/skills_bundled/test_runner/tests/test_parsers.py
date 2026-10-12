import unittest

import plugin

PYTEST = """F.s
FAILED tests/test_api.py::test_login - AssertionError: assert 401 == 200
ERROR tests/test_db.py::test_connect - ConnectionRefusedError
tests/test_api.py:12: AssertionError
1 failed, 1 passed, 1 skipped, 1 error in 0.42s
"""

TAP = """TAP version 13
not ok 2 - adds numbers
# tests 3
# pass 2
# fail 1
"""


class ParserTest(unittest.TestCase):
    def test_pytest_summary_and_failures(self):
        parsed = plugin.parse_pytest(PYTEST)
        self.assertEqual(parsed["counts"], {"failed": 1, "passed": 1, "skipped": 1, "errors": 1})
        self.assertEqual(parsed["failures"][0]["test"], "tests/test_api.py::test_login")
        self.assertEqual(parsed["failures"][0]["line"], 12)
        self.assertEqual(parsed["failures"][1]["kind"], "error")

    def test_tap_counts_and_failures(self):
        parsed = plugin.parse_tap(TAP)
        self.assertEqual(parsed["counts"], {"total": 3, "passed": 2, "failed": 1})
        self.assertEqual(parsed["failures"], [{"test": "adds numbers", "kind": "failed", "message": ""}])

    def test_status_classification(self):
        self.assertEqual(plugin.classify({"returncode": 0}, {"counts": {}, "failures": []}), "passed")
        self.assertEqual(plugin.classify({"returncode": 1}, {"counts": {"failed": 1}, "failures": []}), "failed")
        self.assertEqual(plugin.classify({"returncode": 2}, {"counts": {}, "failures": []}), "error")
        self.assertEqual(plugin.classify({"returncode": -1, "timed_out": True}, {"counts": {}, "failures": []}), "timeout")


if __name__ == "__main__":
    unittest.main()
