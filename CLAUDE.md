# CLAUDE.md

Quick reference only. For details, read the code, the module docstrings, `docs/` and each frontend's README;
the long architecture notes that used to live here are in git history (`git log -p -- CLAUDE.md`).

## What this is

Kvist is a local terminal coding agent working on one workspace directory. An LLM emits one JSON response per
step with a batch of actions (read/write files, shell, web, ...); a sandboxed executor runs them, feeds the result
back, and repeats until done. Several frontends drive the same Python core.

## Layout

- `kernel/` — the core, Python package (`python -m kernel`). Subpackages: `agent/` (loop, router, native tools),
  `protocol/` (action protocol, parser, frontend events), `sandbox/` (executor, terminal, policies, approvals),
  `session/`, `context/`, `llm/`, `plan/`, `memory/`, `plugins/` (skills), `ssh/` (remote core), `config/`,
  `prompts/` (data), `tests/`.
- `kernel/session-rs/` — Rust crate `kvist-session`: `--session-op` without starting Python (Linux/macOS/BSD/Windows, optional;
  `kernel/session/native.py` hands requests to it, Python serves them when it is missing).
- `frontends/` — `tui/{classic,ink,opencode,glass}`, `gui/{electron,tauri}`, `ide/{vscode,jetbrains}`, `zed`,
  `voice`, `kvide`, `tray`. Ink's TS modules are also imported by VS Code and OpenCode: check both sides.
- `scripts/` (installers, e2e smokes), `docs/` (`UI_API.md` = frontend protocol, `SKILLS.md`, `remote.md`),
  `branding/`, `site/`.
- Imports are `kernel.<subpackage>.<module>`; the classic terminal and Zed frontend are imported by bare name
  (`kernel/__init__.py` and `pytest.ini` add them to `sys.path`).
- `LocalAgent` (`kernel/agent/agent.py`) is assembled from functions bound as methods (`_run_loop = run_loop`).
  The logic lives in `agent_runtime.py`, `agent_model.py`, `plan_mode.py`, ...; edit those, not `agent.py`.

## Git (MANDATORY)

- Two long-lived branches: `experimental` and `main`. Every change is committed on `experimental` first, never
  on `main`. It reaches `main` only after the user checked it and explicitly confirmed, via
  `git merge --ff-only experimental` (first `git merge-base --is-ancestor main experimental`).
- Never create other branches or worktrees unless the user asks. Ask first if isolation seems necessary.
- Push only when the user explicitly asks, and only to the backup remote: `git push relay <branch>`.
  NEVER push to `origin` / GitHub; publishing is a manual, human-only action.

## Commands

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt

.venv/bin/python -m kernel --workspace "<path>"          # run the agent
.venv/bin/python scripts/run_task.py "<prompt>"          # headless single task

.venv/bin/python -m pytest                               # all Python suites
.venv/bin/python -m pytest kernel/tests/test_x.py::name  # one test
.venv/bin/python -m pytest -m offline_smoke              # fast, no credentials
.venv/bin/python -m pytest -m ""                         # also slow PTY/real-process tests
.venv/bin/python -m ruff check .
.venv/bin/python scripts/e2e_tools_smoke.py              # tools vs a fake provider (~3 s)
.venv/bin/python -m compileall -x '\.agent-workspace/|/workspace/' -q .

cd kernel/session-rs && cargo test && cargo build --release   # then test_session_native.py compares it with Python
cd frontends/tui/ink && npm install && npm run build && npm test && npm run typecheck
cd frontends/ide/vscode && npm test && npm run typecheck
cd frontends/gui/electron && npm test
cd frontends/gui/electron-legacy && npm test
cd frontends/gui/tauri && node --test "tests/*.test.mjs" && (cd src-tauri && cargo test)
```

## Things that bite

- Config: `~/.kvist/config.json`. Use `KVIST_CONFIG_DIR=<dir>` to try a first run; never fake `HOME` for it.
  Never print config secrets: read exact non-secret keys only.
- Per-project state lives in `<workspace>/.kvist/` (sessions, change journal, logs). Frontends use
  `main.py --session-op ...`, never the files.
- `--session-op` has two implementations: `session_store.py` (+ the storage half of `agent_logging.py`,
  `durable_json.py`, `sandbox/redaction.py`) and `kernel/session-rs`. A change to one is a change to both;
  `test_session_native.py` fails until they agree again, and is skipped when the crate is not built.
- Adding an action type: update `protocol/protocol.py`, the parser schema, the executor dispatch and the role
  prompts in `kernel/prompts/system/` (`test_prompt_protocol_sync.py` enforces it).
- Editing a bundled skill means raising its version and updating `kernel/tests/bundled_skill_digests.json`.
- Keys in `agent_context.SECTION_PRIORITIES` must match rendered section headers exactly.
- New message in `frontends/ide/vscode/src/messages.ts` needs handlers in both VS Code and JetBrains hosts.
- Raise `kernel/ssh/wire.REVISION` whenever the remote proxy/daemon wire contract changes.
- Tests must never start the real tray daemon or touch the network; use the fakes in `kernel/tests/conftest.py`.
- Doc changes need their Russian twin (`*.ru.md`).

## Conventions

- Every module starts with `# -*- coding: utf-8 -*-` and `from __future__ import annotations`.
- Dataclasses use `slots=True`; run state mutates through `RunState`.
- User-facing strings are bilingual EN/RU.

## Code generation rules

1. Code, comments and internal reasoning in English; the final reply to the user in Russian (informal «ты»).
2. No premature abstraction or speculative generality; no parameters/flags for hypothetical futures.
3. A function does one thing. Early return over nesting; max depth 3.
4. No silent exception swallowing; catch specific exceptions, never bare `except`.
5. Comment only the "why". Prefer the simple solution when it meets the requirement.
