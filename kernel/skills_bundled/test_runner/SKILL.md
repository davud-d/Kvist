# Test runner

Runs the project's tests through Kvist's workspace sandbox and reads the result
for you, so a run is evidence with numbers rather than a wall of output.

- `test_runner__run` with no arguments detects the tool: pytest (pytest.ini,
  pyproject.toml, setup.cfg, conftest.py or a tests folder with test_*.py),
  `node --test` (test files with no npm test script) or `npm test`.
- `target` narrows the run: a file, a folder, or a pytest node id
  (`tests/test_api.py::test_login`).
- The answer has `status` (passed, failed, error, timeout, no_tests), `counts`,
  `failures` (test and message, with file and line when the tool gives them),
  the exact `command`, and `log_artifact`, the full log kept for the person.

`error` means the tests did not run as tests (missing tool, import error,
syntax error): read `log_tail` before changing code. Commands go through the
same approval and sandbox rules as any shell command, so the person may be
asked to allow the first run. Run only the tests that answer the question;
a full suite is not needed after every small edit.
