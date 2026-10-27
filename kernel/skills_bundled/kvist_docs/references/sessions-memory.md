# Sessions, checkpoints, memory, timers

## Sessions

- A launch opens its own fresh session; `/new` does the same and keeps the old one.
  `/session` creates, switches or deletes. Names are 1-64 lowercase letters, digits, `-`
  or `_`. Automatic names look like `chat-<timestamp>`; an empty auto-named session is dropped.
- State is under `<workspace>/.kvist/`: `sessions/<name>.json` (history and checkpoint),
  `sessions/<name>.events.jsonl` (display-only log the interfaces redraw from),
  `change_journal.json`, `logs/`. Interfaces use `--session-op`, not the files.
- A session can be closed for good (after an insult to the agent or people following a
  warning, or when the person asks for it and confirms). It stays readable; `/new` continues.

## Interrupted work

A checkpoint is written after every step that changes something. After a stop, a crash or
a closed terminal, `/resume` (or `kvist --resume`) continues. A new message while a
checkpoint exists continues the same task with that message as a follow-up. A write that
may have been cut mid-way is reconciled by content digest on resume, and Kvist stops
rather than guesses when it cannot tell. Running commands and helpers are not restored.

## Import

`/export` (`/import`) reads sessions of other agents (Claude Code, Codex, OpenCode, Cursor,
Qwen Code, Continue, Aider) read-only into new Kvist sessions: requests, last answers, files
changed; never tool output. An already imported session is skipped.

## Plan mode

For a task whose direction is the risky part, the agent offers to start with a plan: it
reads without changing anything, proposes a plan, and edits only after the person accepts
or revises it. A decline is not remembered; small edits skip all this.

## Memory

Records that outlive a session: `project_fact`, `decision`, `preference`. One SQLite
file per project (`.kvist/memory/memory.db`) and one for the person's preferences everywhere.
Each record says who stated it: `person`, `observed` or `inferred`.

- The agent can say "remember …" / "forget …"; forgetting asks the person first.
- `/memory status|pause|resume|inspect|why|forget|remember|remember-global|process`.
  `pause` stops agent and skill writes. `process` has the fast model pick durable findings
  from the session's finished turns (asks consent once); interactive interfaces then do it
  in the background after a session has been quiet for 10 minutes, on leaving it and at launch.
- Credentials and personal data are refused, not stored redacted. Relevant records are
  quoted into prompts as reference, never as orders.

## Timers and background work

- `run_shell` with `background: true` starts a long command (a dev server); the agent reads
  its output at step boundaries and stops it when done. At most 4 run at once.
- In the Ink terminal app and the VS Code panel the agent can be woken: after a job or
  helper ends, or for a `wait` (1 s to 24 h) and a `schedule` (one-time or recurring,
  stored in `.kvist/schedules.json`). Elsewhere (classic terminal, headless, JetBrains,
  Zed) those end at the answer, `wait` is refused and a schedule only fires later in a
  place that can wake the agent. A closed Kvist runs nothing.
