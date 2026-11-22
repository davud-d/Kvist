# Contributing

[Русская версия](CONTRIBUTING.ru.md)

## Development environment

You need Python 3.11 or newer.

```bash
git clone https://github.com/davud-d/Kvist
cd Kvist
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m kernel --workspace "<path>"   # run the agent
```

On Windows use `py -m venv .venv` and `.venv\Scripts\python.exe`. The frontends
need Node.js (and Bun for the OpenCode one); each has its own README with build
steps. The layout of the repository is in [`CLAUDE.md`](CLAUDE.md).

## Checks

Run these before you open a pull request:

```bash
.venv/bin/python -m pytest              # all Python suites
.venv/bin/python -m pytest -m offline_smoke   # fast, no credentials
.venv/bin/python -m pytest -m slow      # slow PTY and real-process tests
.venv/bin/python -m ruff check .
```

Tests must not touch the network or start the real tray daemon; use the fakes in
`kernel/tests/conftest.py`. If you change a frontend, run its own tests too, for
example `cd frontends/tui/ink && npm install && npm test`.

## Branches

Every change goes to the `experimental` branch first, never straight to `main`.
Base your pull request on `experimental`. `main` is moved only by a
fast-forward merge from `experimental`, after the maintainer has checked the
change.

## Conventions

- Every Python module starts with `# -*- coding: utf-8 -*-` and
  `from __future__ import annotations`.
- User-facing strings are bilingual, English and Russian.
- A change to a document needs the same change in its Russian twin (`*.ru.md`).
- Comment the why, not the what. No silent `except`; catch specific exceptions.
- Commit messages follow `type(scope): summary`, for example
  `fix(agent): ...`, `feat(ui): ...`. One change, one commit.

## Security

Do not report vulnerabilities in issues or pull requests; see
[`SECURITY.md`](SECURITY.md).
