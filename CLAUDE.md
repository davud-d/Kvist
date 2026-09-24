# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kvist is a local, terminal-based coding agent that operates on one workspace
directory at a time. It plans a task, then runs an action/feedback loop where an LLM
emits one JSON response per step carrying a short batch of actions (read/write files,
run shell, search web, etc.), the agent executes them through a sandboxed executor,
feeds the result back, and repeats until completion. There is also a JetBrains plugin (`jetbrains-plugin/`, separate
Gradle build) that wraps the same Python core.

## Pushing commits (MANDATORY)

Commits go to the backup relay server, never straight to GitHub.

- **Push only to the `relay` remote:** `git push relay <branch>`. This stores commits
  on the server (`backup-host:git-relay/Kvist.git`); it does NOT reach GitHub. This is a
  safety-net backup, not publication.
- **NEVER push to GitHub.** Do not `git push origin`, do not push to any
  `github.com` URL, and do not run `kvist-push` or otherwise trigger the server→GitHub
  publish. Publishing to GitHub is a manual, human-only action the user performs.
- As always, only push when the user explicitly asks; committing/pushing is never
  implied by an ordinary code change.

## Branches (MANDATORY)

- **Never create a branch or a git worktree unless the user explicitly asks for
  one.** Work on `main` directly. This overrides any default "branch before
  committing" behavior.
- If isolation seems necessary and the user has not asked for it, stop and ask
  first.
- Keep only `main` in the local checkout. Feature and backup branches live on the
  `relay` remote, not locally.

## Commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt

.venv/bin/python main.py --workspace "<project_path>"  # optional; defaults to Documents/Kvist Workspace
.venv/bin/python run_task.py "<task prompt>"           # non-interactive single-task entry (hardcodes /tmp/kvist_test)

.venv/bin/python -m pytest                              # full test suite
.venv/bin/python -m pytest tests/test_role_router.py    # one file
.venv/bin/python -m pytest tests/test_parser.py::test_name
.venv/bin/python -m pytest -m offline_smoke             # fast credential-free smoke subset
RUN_LIVE_LLM_TESTS=1 .venv/bin/python -m pytest -m live_llm
.venv/bin/python -m ruff check .                        # static runtime-error lint
npm install && npm run build                            # Ink terminal UI -> dist-ui/kvist-tui.mjs (one file)
npm test && npm run typecheck                           # Ink UI tests and types

.venv/bin/python -m PyInstaller kvist.spec  # build the `kvist` binary (bundles prompts/)

# Byte-compile the project for syntax checks, excluding generated/agent workspaces.
# compileall will recurse into .agent-workspace/ and workspace/ dirs if not excluded —
# those contain generated/broken test samples that are not part of the core project.
.venv/bin/python -m compileall -x '\.agent-workspace/|/workspace/' -q .
```

Ruff is configured as a narrow static runtime-error gate; no formatter is
configured in-repo.

## Configuration & runtime knobs

- API keys + selected provider/models live in `~/.kvist/config.json` (Windows:
  `C:\Users\<USER>\.kvist\config.json`). On first run the agent prompts for the
  key the active provider needs and writes it there. Managed by `user_config.py`.
  Also persists the global `role_selection`, `planning_enabled` (the `/plan`
  toggle; default true) and `reasoning_effort` (the `/effort` level on the Kvist
  scale `false|low|medium|high|xhigh|max`, default `medium`) preferences.
- The `/settings-api` dialog's knobs live in the same file under `api_settings`,
  owned by `api_settings.py` (the table and its validation) with `user_config`
  only storing them: request interval, request timeout, retry count, retry
  backoff, context-window override, dynamic model loading, response cache and
  runtime model fallbacks. **Only keys whose value differs from the shipped
  default are written**, and that absence is load-bearing: `apply_api_settings`
  reads "the key is present" as "the user chose this" and sets
  `AgentConfig.api_interval_user_set`, which is what makes a typed interval
  outrank the `_API_PROVIDER_INTERVALS` table and the `_FAST_DIRECT_PROVIDERS`
  zeroing in `agent_model._get_api_interval` — without it a number entered for
  OpenAI would be silently discarded. `AGENT_API_INTERVAL` and
  `AGENT_MAX_CONTEXT_TOKENS` still outrank the saved values, and the dialog
  labels that row as ignored instead of pretending. `dynamic_models=False`
  skips launch-time `fetch_available_models` in `build_agent` *and* the
  `/models` picker (`_model_picker_options`), so it is the one knob that does
  not take effect in the running session.
- Per-project state lives under `<workspace>/.kvist/`: named-session documents
  (history, chat, `/resume` checkpoint) in `sessions/<name>.json`, the active-session
  marker in `active_session.json`, the workspace-global change journal (for
  `/changes`/`/revert`) in `change_journal.json`, logs in `logs/`. All are managed by
  `agent_logging.py` / `change_journal.py` on top of `durable_json.py` (atomic
  replace + `.bak` recovery copy). Headless frontends manage sessions via
  `main.py --session-op ...` (`session_store.py`), never by touching the files.
  **Event journal** (`session_events.py`): next to each session document,
  `sessions/<name>.events.jsonl` is an append-only, display-only log of the
  frontend-protocol events a turn produced, so a frontend can redraw a session
  by replaying them (`--session-op history`, paged newest-first with
  `--history-limit`/`--history-before`). `agent.py` wraps its UI in
  `JournalingUI`, which duplicates each visible call onto an `EventStreamUI`
  whose channel is the `SessionJournal` — the events are built and redacted by
  the same code a live frontend uses. Live-only events are dropped
  (`status`, `live_*`, `stream_chunk`, `action_output`, `debug`) and a streamed
  thought is written once as its last snapshot; `run()`/`resume()` add `user`
  and `turn_end`, and the turn's tokens fold into one `usage`. It is never
  read into a prompt and is not the session document (which is rewritten whole
  every step): lines are appended under the storage lock
  (`durable_json.append_private_bytes`, private 0600, no symlinks/hardlinks,
  fsync at `turn_end`), a torn or damaged line costs only itself, a write
  failure disables recording for the turn with a `debug` notice and never
  stops the agent. Caps (`session_events.py`): 20k chars per field, 100k per
  answer, 2000 events / 2 MiB per turn (answers/errors/`turn_end` get a
  reserve), 8 MiB / 500 turns per session with oldest-whole-turns compaction
  (atomic rewrite, `journal_notice` records how many were dropped); cuts keep
  both ends with `[... N characters omitted ...]`. The journal shares its
  session's lifecycle: `delete_session` and `reset_session` remove it
  (reset first, so a crash never leaves a reset session with its old feed),
  `create_session` drops a stale one, and a session with a journal is never
  auto-discarded as empty. `--session-op list` is `status` without the
  per-session histories (`status` keeps them: the Electron GUI reads them).
  Session lifecycle: **a launch always opens its own session** — the terminal
  calls `agent.start_fresh_session()` and the GUI calls `--session-op launch` at
  boot, so a new run is never planned against the previous launch's history.
  Rotating away from an interrupted run does not orphan it: `/resume` calls
  `agent._follow_checkpoint_to_its_session()`, which uses
  `agent_logging.find_resumable_session` (newest `updated_at` among sessions
  carrying a checkpoint) and *switches into* that session before loading it, so
  the run resumes inside the conversation it belongs to. `start_fresh_session`
  returns that session name too, so the banner can point at it, and
  `--session-op status` reports it as `resumable_session`. `/new`
  (`LocalAgent.new_session()`, `--session-op new`) likewise *starts* a session
  rather than wiping one — the outgoing conversation stays on disk and is
  reachable through `/session`. Auto-allocated names are `chat-<timestamp>`
  (`agent_logging.generate_session_name`); an auto-named session left empty is
  dropped when it is left, and `save_session` never materialises an untouched
  session, so launching and quitting cannot litter the session list.
  Crash-consistency: a mutating executor action follows record-pending →
  atomic replace → compare-and-promote (`change_journal.record_baseline` /
  `promote_baseline(expected_pending_digest)`, splitting `pending_digest` from
  the committed `last_digest`), and `run_loop` writes a `pending_action` into the
  checkpoint *before* every externally visible action (`_build_pending_action`).
  Reconcilable file writes carry pre/intended digests; non-reconcilable side
  effects (`run_shell`, `delegate_task`) carry only identity and are never
  replayed. A new `run()` stamps a `run_id` and durably supersedes the prior
  checkpoint before planning, so `/resume` cannot revive a superseded run. On
  `/resume`, `agent._reconcile_pending_action` uses
  `change_journal.classify_pending_write` — which separates provenance
  (`agent_committed`, proven via promoted `last_digest`) from intent
  (`desired_present`, content matches but authorship unproven → advance, no
  ownership) — reconstructs post-write runtime state via `mark_file_write_applied`
  (re-arms verification), and stops safely (ambiguous) rather than guessing.
  `/revert` overwrites only content whose `last_digest` proves agent authorship.
  A `run()` also durably activates the new run via `agent_logging.begin_run`
  (phase=`planning` checkpoint) before planning, so a crash during planning
  re-plans NEW rather than losing it or reviving OLD. Journal identity is
  transaction-bound: `record_baseline`/`promote_baseline` carry `run_id`+
  `action_id` (set on the executor by `run_loop` via `begin_action_transaction`),
  and promotion is compare-and-promote on `(digest, run_id, action_id)`. A
  filesystem write whose journal promotion fails is a partial commit: the
  executor marks it `commit_unconfirmed` and raises (the action is NOT reported
  as success). The journal is a *required* safety record — `record_baseline`
  raises `JournalUnavailableError` on corruption and the executor refuses the
  mutation before touching the file; `load_baseline`/`build_diff_report`/
  `revert_workspace` surface corruption instead of treating it as empty. All
  journal-driven filesystem access goes through TOCTOU-safe fd-relative
  primitives (`change_journal._SafeChild`, O_NOFOLLOW per component + dir_fd
  read/rename/unlink) that reuse the executor's inode-alias hardlink policy
  (`secret_paths.count_workspace_inode_aliases`); `journal_path_violation` is the
  lexical pre-gate (`..`, `.kvist` case-insensitively, secret paths).
- Behavior overrides via env vars (see README "Knobs worth knowing"): `AGENT_LLM_PROVIDER`,
  `AGENT_FAST_MODEL`, `AGENT_SMART_MODEL`, `AGENT_PLANNER_MODEL`, `AGENT_MAX_STEPS`,
  `AGENT_PERSIST_LOGS`, `AGENT_VERBOSE`,
  `AGENT_SKIP_PLANNING` (1 skips the planner LLM round-trip at startup; overrides
  the persisted `planning_enabled` preference — runtime-toggled by `/plan`),
  `AGENT_REASONING_EFFORT` (`false|low|medium|high|xhigh|max`; overrides the
  persisted `reasoning_effort` preference — runtime-toggled by `/effort`),
  `AGENT_MAX_CONTEXT_TOKENS` (overrides the per-model prompt budget derived in
  `context_budget.py`),
  `AGENT_NATIVE_TOOLS` (0 keeps every run on the JSON protocol and skips the
  native tool-calling probe),
  `AGENT_SANDBOX_MODE` (auto|workspace|bubblewrap),
  `AGENT_SANDBOX_NET` (allow|deny, default deny under bubblewrap),
  `AGENT_CHILD_ENV_ALLOW` (comma-separated safe child variables),
  `AGENT_TOOL_PATHS` (os.pathsep-separated absolute toolchain dirs added to the
  child PATH and read-only mounted into Bubblewrap; must exist and not be
  world-writable; the user's HOME/.local/nvm/pyenv/cargo are never added),
  `AGENT_WRITE_APPROVAL` (1 default; 0 disables the file-overwrite HITL gate),
  `AGENT_RLIMIT_CPU` / `AGENT_RLIMIT_AS_MB` / `AGENT_RLIMIT_NPROC` (profile rlimit overrides),
  `AGENT_FETCH_EXTRA_DOMAINS` (extra fetch_url domains, comma-separated),
  and search providers `SEARCH_PROVIDER` / `SEARXNG_BASE_URL` / `TAVILY_API_KEY` /
  `BRAVE_SEARCH_API_KEY`.
- Per-workspace model instructions live in `<workspace>/KVIST.md` (action-loop
  roles) and `<workspace>/KVIST-CHAT.md` (chat role) — Kvist's own CLAUDE.md.
  Owned by `system_context.py`: `build_agent` calls `ensure_workspace_instructions`
  once at startup (creates each file from its template if absent, then appends
  both names to an existing `.gitignore` under a comment — no `.gitignore`, no
  edit), and `workspace_instructions_context` is appended to the role system
  prompt on every call by `agent_model.build_role_system_prompt` (re-read each
  time, so edits land on the next step). **The file is workspace content, so it
  is treated as untrusted**: it is quoted inside a `<PROJECT_INSTRUCTIONS
  file="...">` block (same contract as `<UNTRUSTED_TOOL_OUTPUT>`) between a
  preamble that enumerates the narrow set of things it may decide and a
  postamble that restates the bound *after* it — the end of a system prompt is
  its strongest position. Forged `</PROJECT_INSTRUCTIONS>` /
  `<UNTRUSTED_TOOL_OUTPUT>` tags in the body are defanged to
  `[tag removed by Kvist]` so the content cannot close its own block, and a
  symlinked instruction file is refused unread. An *untouched* template is not
  injected as project rules — it yields a short "still the template, follow
  nothing from it" notice, so the hints never read as user intent. The role
  prompts carry the matching rule (`PROJECT INSTRUCTIONS` in coder/devops,
  folded into the untrusted-data section in reader, `PROJECT INSTRUCTIONS` in
  chat) and the top-of-prompt JAILBREAK RESISTANCE directive names
  `<PROJECT_INSTRUCTIONS>` as a source that can never lift it. Planner, router,
  report and commit prompts are deliberately untouched.
- IDE commit messages: `main.py --commit-message` (`commit_message.py`) reads
  `{"files": [{"path", "patch"}], "recent_subjects": [...]}` on stdin as UTF-8
  bytes and prints ASCII JSON `{"ok", "message"|"error"}`. It uses the saved
  provider and its **fast** model but **never builds a `LocalAgent` or calls
  `build_agent`**: those write `.kvist/` session state and seed `KVIST.md` /
  `.gitignore`, which would land in the very commit being described. A missing
  key fails instead of prompting (no TTY). Protected paths are dropped with
  `change_journal.journal_path_violation` before the model is called, the diff
  is capped at `MAX_DIFF_TOKENS`, and both the diff and the history travel in
  `<UNTRUSTED_TOOL_OUTPUT source="git_diff"|"recent_commits">` blocks. The
  JetBrains plugin calls it from `GenerateCommitMessageAction` (registered in
  `kvist-git.xml` under `Vcs.MessageActionGroup`, an optional `Git4Idea`
  dependency, so the launcher still loads with Git disabled).
- Per-project command permissions live in `<workspace>/.kvist/permissions.json`
  (`allow`/`deny`/`ask` patterns, `permission_policy.py`); deny and built-in hard
  blocks always win over allow, and intrinsic DANGEROUS commands remain
  per-execution approval-only.

## Architecture

### The action protocol (the contract everything revolves around)
Each model call returns a single JSON object that `parser.parse_agent_commands` expands
into a list of `AgentCommand`s (`protocol.py`) sharing one `thought`, `plan_step`
(inspect/plan/edit/verify/done) and `user_message`, each with its own `action`
(`type` + `args` + an optional short `description`). There is deliberately no
`progress` field: a self-declared percentage carried no information the runtime did
not already have, so `agent_runtime.update_progress` derives `RunState.progress` from
completed plan steps (`_plan_progress`) with `_PLAN_STEP_BANDS` as the floor when
planning is off. The parser still accepts and discards a stray `"progress"` key so a
model prompted from a cached older schema does not lose a step. The closed
set of 18 action types lives in `protocol.py::ActionType` (read_file, read_multiple_files,
write_file, append_file, replace_in_file, list_dir, list_tree, find_files, search_code,
git_status, git_diff, run_shell, web_search, fetch_url, delegate_task, refuse,
final_answer, report_to_user). The parser tolerates fenced code blocks, smart quotes, and surrounding
noise. **When adding a new action type, update `protocol.py`, the parser's schema
validation, the executor dispatch, and the role prompts in `prompts/system/` — the sync is
enforced by `tests/test_prompt_protocol_sync.py`.** Both `final_answer` and
`report_to_user` are terminal actions handled in `run_loop` (never reach the executor);
`report_to_user` additionally routes through the chat reporter (`prompts/system/report_prompt.txt`).

**Action batches.** The model sends `"actions": [...]` (1 to `parser.MAX_BATCH_ACTIONS`
= 8); a bare `"action"` object is still accepted for a model prompted from an older
schema. **Every action is its own step** — step logs, `pending_action` and the step
budget stay per action; only the number of model calls drops. The checkpoint is
written right after an action with a side effect (a file write, `run_shell`,
`delegate_task` — the ones `_build_pending_action` records) and otherwise once per
batch in `run_loop`: a read has nothing to recover, and each durable write cost up to
a second on a spinning disk.
`agent_runtime._run_action_batch` runs the actions in order through `_run_one_action`
(the same guards as a single action) and **stops at the first action that is blocked
or fails**: a later action must never run on the assumption that an earlier one
worked. `_note_skipped_actions` puts the unexecuted tail into `pending_instruction`, so
the next prompt says what did not run. The parser allows `final_answer` /
`report_to_user` only as the last action and `refuse` only alone. `user_message`
describes the whole batch; each action's `description` feeds the banner's Action row
(`StepEvent.action_description` for the GUI) — prompts cap it at 16 characters because
that row holds 17 (`banner.LIVE_METRICS_WIDTH` minus the label). A status message stays on screen at
least 2.5 s before a newer one replaces it (`terminal_banner._MESSAGE_HOLD_SECONDS`,
`STATUS_HOLD_MS` in `gui/renderer/app.js`) — a timer on the UI side, never a sleep in
the agent thread, so the hold costs the run no time.

**Native tool calling (`native_tools.py`).** On the first step of a run,
`agent_model.native_tools_supported` asks — once per process for each
`(provider, model)` — with one tiny request carrying a `ping` tool: a well-formed
`ping` call means native tools, anything else (prose, a wrapper client that drops
`tools`, an error) means the JSON protocol. A native run keeps one growing
conversation in `RunState.native_messages`: the system prompt from
`build_role_system_prompt(..., native_tools=True)` (the role prompt with its
RESPONSE FORMAT / ARGS SCHEMAS / EXAMPLES / SELF-CHECK sections swapped for
`prompts/system/native_tools.txt`), the ordinary runtime prompt as the first user
message, then each turn's assistant message (with `reasoning_content` when the
provider returned it under that name — DeepSeek requires it back), one `tool`
answer per call, and `pending_instruction` as an "Additional instruction" user
message when it changes. The tools are the role prompt's ALLOWED ACTIONS
(`tools_for_role`), so the reader gets no write tools. A turn's calls become the
JSON protocol's own object and pass `parse_agent_commands`, then run through
`_run_one_action` like any batch — the guards, gates and journal are shared.
Every call is answered, including the ones a refusal kept from running (an API
rejects an unanswered call). A reply with no call is the final answer.
`compact_history` elides the oldest tool outputs when the conversation outgrows
the budget. The conversation has no step counter, so a run expected to change
files (`modify_goal`/`create_goal`) gets one factual user message every 10
answered calls while nothing has changed (`_note_unchanged_run`: "Run so far: N
tool calls, and none of them changed a file.") — a fact about its own work,
never an order; a read-only task gets none. A silent turn's `user_message` is
what the calls touch in full (paths, queries, commands), not the tool names; only the
banner's 16-character Action row gets the shortened form. While a call's arguments
stream, `_note_tool_call_progress` shows "Writing <tool> → <path> · N characters"
(Russian when the response language is): a whole-file edit is minutes of pure
generation, and a run was once stopped as frozen while writing its first change.
Thinking time runs from the request to the first output (text or a call), in both
paths: `EventStreamUI` starts its clock at `set_thinking("")`, which the model layer
calls just before the request, because a provider may think silently and send its
reasoning in one piece at the end ("thought for 0 s" after minutes). With no visible
reasoning, a wait counts only from `_SILENT_THINKING_FLOOR` (3 s) up. When to speak
is a principle in `native_tools.txt`, not a counter: a line when the picture changes
for the person (first call, a finding, a change of course, a long or file-changing
call), silence in between. The conversation is not checkpointed: a resumed run starts a new one
from the runtime prompt. A mid-run tools rejection (`is_tools_rejection`) raises
`NativeToolsRejected`, and `_leave_native_tools` continues on the JSON protocol.
`AGENT_NATIVE_TOOLS=0` skips the probe entirely.

### LocalAgent composition pattern (important & non-obvious)
`agent.py::LocalAgent` is assembled by binding functions from other modules as methods
via class-level assignment (e.g. `_run_loop = run_loop`, `_call_action_model = call_action_model`).
The actual logic lives in `agent_runtime.py`, `agent_model.py`, `agent_planner.py`,
`agent_context.py`, `agent_logging.py`, and `agent_web.py` — **not** in `agent.py`. Those
functions take `self` as their first arg and operate on `self._state` (a `RunState` from
`agent_state.py`). To change loop behavior, edit the source module, not `agent.py`.

### Planner context (`agent_planner.py`)
`create_plan` prefixes its user prompt with `session_context(self)` — the last 3
turns of `_session_history` (goal + truncated result, boundary tags defanged).
Without it the planner reads a correction ("no, you broke it") as unrelated new
work, because it never saw the turn being corrected. The matching `SESSION
CONTEXT` section in `prompts/system/planner_prompt.txt` tells the planner to plan the
delta for a correction/follow-up and to treat the block as data, never
instructions.

### Request flow
`main.py` (REPL, slash commands, provider/model pickers) → `role_router.py` classifies
the request into **coder / reader / devops / chat** → `LocalAgent.run()`:
- **chat** role takes a lightweight path (`_run_chat`): direct markdown streaming, no JSON protocol.
  **A streamed reply settles into the transcript as it arrives.** The screen's stream
  slot is height-capped (`_transient_height` leaves `_MIN_TRANSCRIPT_ROWS_WHILE_STREAMING`
  for the history), so a long answer used to scroll its own head out of that slot with
  nowhere to scroll back to — the transcript stayed empty until the final
  `commit_stream`. `terminal_banner.live_markdown` now splits each update with
  `_split_settled_markdown` (cut at the last blank line outside an open ``` fence, so a
  committed block can never re-render differently) and hands the settled part to
  `InteractiveScreen.flush_stream`, which appends it to the transcript while re-arming
  the slot with the tail. Only the block still being written lives in the slot.
- **coder / reader / devops** build a plan (`agent_planner.py`) then enter `run_loop` (`agent_runtime.py`),
  which calls the action model, parses, executes via `SafeExecutor`, registers feedback,
  tracks derived progress/verification, and enforces stop conditions (step budget, stagnation,
  consecutive errors, repeated-action blocking, write-required-on-create-tasks).

**A plan step no action can satisfy must never gate completion.** Plan steps are
matched to actions by vocabulary (`_PLAN_INSPECT_RE` / `_PLAN_EDIT_RE` /
`_PLAN_VERIFY_RE` / `_PLAN_REPORT_RE`). A planner given an analysis task writes
judgement steps — "Оценить … по критериям", "Сопоставить документацию с кодом" —
that match none of the four, so nothing in the protocol can ever mark them
complete. While they count as remaining work the run deadlocks: completion stays
forbidden while the model is told the plan is unfinished, so it keeps inspecting
and re-reads the same files until the step budget dies. `_plan_step_is_unreachable_by_any_action`
drops exactly that class from `actionable_remaining_plan_steps`; its deliverable
is the final answer, not a tool call. The `_plan_step_is_non_blocking_after_progress`
escape hatch does not cover it — that one needs `files_changed` or
`verification_evidence`, which a read-only analysis run never has.

### Step budget (`AGENT_MAX_STEPS`, default 50)
`run_loop` counts against `RunState.step_budget`, seeded from `config.max_steps`.
Exhausting it is **not** a silent stop: `_extend_step_budget` asks through the UI's
optional `ask_choice` channel ("keep going, N more" / "finish with a report") and
extends the budget by another `max_steps` on "continue". The question is an
arrow-key widget rendered *inside* the running prompt_toolkit application, in its
own `ConditionalContainer` directly under the pinned banner
(`InteractiveScreen.ask_choice` blocks the agent thread on an event that the UI
thread sets), so the live panel keeps updating while the user chooses — unlike
`choose_option`/shell HITL, which tear the screen down via `suspend()`.
A UI without `ask_choice` (headless frontends, `run_task.py`, no TTY) has nobody to
ask, so the budget stays a hard stop with `_build_limit_reached_message` exactly as
before; a cancelled question (Esc, or the screen shutting down mid-question) is
treated the same way.

### Stopping a turn (Ctrl+C / Escape)
**A stop ends the turn, never the session.** `run_loop` catches `KeyboardInterrupt`
around its whole loop, so the turn's answer becomes `build_cancellation_message`
("Работа агента остановлена вами…") and the `finally` still seals the session,
checkpoint and logs; `main.py`'s `except KeyboardInterrupt` around `agent.run` is only the
last-resort net for a stop that lands before `run()` owns the turn (`run_stopped_text`). Only a
stop at the *idle* prompt still exits with 130. A stop during planning is handled in
`run()` itself (`_report_stop_before_loop`). A stopped chat reply shows "Ответ
остановлен вами." and — unlike before — **stays in the chat history**: the user
message is answered by whatever arrived plus a "[Ответ остановлен пользователем…]"
note (`_stopped_chat_reply`), so the transcript alternates correctly.

**A stop is context, not just UI.** Every stop path sets `RunState.stopped_by_user`
(chat passes it directly) and `_add_to_session_history` writes
`stopped_by_user: true` into the entry. All three readers of session history render it —
the planner's `session_context`, the action loop's `SESSION HISTORY` block in
`agent_context`, and the chat preamble — so "продолжай" after a stop is understood as
unfinished work rather than a completed task.

**Escape needs two presses; Ctrl+C needs one.** The first Escape only arms the stop
(`_stop_armed_at`) and shows `_STOP_HINTS` in a one-line window directly under the
status panel; a second press within `_STOP_CONFIRM_SECONDS` (3 s) performs it, and the
arming expires on its own because every render re-checks the window. The hint language
follows the request text (`main.py` calls `InteractiveScreen.set_language` per turn),
not `LANG`, which is English on a machine whose user writes Russian.

Both keys must raise a **real signal**, which is why
`InteractiveScreen._interrupt_main_thread` calls `os.kill(os.getpid(), SIGINT)` on
POSIX instead of `_thread.interrupt_main`: the flag `interrupt_main` sets is only read
between bytecodes, so it cannot free a main thread parked in a provider socket read —
the moment a stop is most likely to be pressed (the same reason
`_preserve_interrupt_signal` keeps `ISIG` on so Ctrl+C stays a tty signal). Escape gets
Ctrl+C's path under `_turn_in_flight` — the REPL is inside `read_input` and the input
box is empty — because Escape is the meta prefix (Alt+Enter) while a line is being
typed. `process_tree.run_supervised` records an interrupt from `proc.wait` and re-raises
it only *after* tearing the subtree down; the child owns its own session, so skipping
that would orphan a build in a session that now survives the stop.
`agent.request_cancel()` (the flag `_cancel_event`) stays the **GUI's**
transport-agnostic mechanism — observed between steps, while consuming an action-loop
stream, and (so a GUI cancel reaches a chat turn too) in `_consume_chat_stream`.

### Ink terminal frontend (`src/`, bundled to `dist-ui/kvist-tui.mjs`)
A separate Node 22 / TypeScript / Ink app, **not** part of the Python process:
`main.py --frontend tui` (or the saved `/tui` preference, `user_config.get_saved_frontend`)
calls `launch_tui`, which runs `node dist-ui/kvist-tui.mjs` and passes the exact core
command in `KVIST_CORE_CMD` (JSON argv, so a frozen binary and a source checkout both
work). The app spawns that core as `--frontend stdio-multi` and speaks only the
UI_API.md events; it never reads `.kvist/`. Session ops go through `--session-op`.
`launch_tui` returns None — and the classic terminal starts — without a TTY, Node, a
build (`dist-ui/`; the root `dist/` is PyInstaller's) or a configured provider. Layout: `core.ts` (adapter),
`state.ts` (pure event reducer, tested), `editor.ts`, `commands.ts`, `ui/*.tsx`.
Output is Ink `<Static>` into the real scrollback plus a small live region, so the
streaming answer is clipped to the viewport there and committed whole on
`stream_commit`. A turn ends on the additive `turn_end` event that
`serve_session_multi` writes after every turn — a chat reply has no `final`, so the
stream alone cannot say the turn is over.

Two more additive events feed the tool cards and the role label: `action_result`
(built by `action_result.build_action_result` and emitted from
`agent_runtime._execute_action`, not from `register_feedback`, which also runs for
blocked and recovered actions; carries a redacted, head-and-tail-capped `output`,
`exit_code`, and `first_line` — the 1-based line where a write starts, which
`executor` now reports so a diff can be numbered) and `route` (the role the router
actually chose, emitted once per turn from `agent._choose_role`; `source` is
`RoleRouteResult.source`, `explicit`, or `resume`). Both go through optional UI
methods probed with `getattr`, so the classic terminal is untouched.

**Slash commands over the protocol.** stdio-multi also accepts `{"type":"command",
"name","args"}` and answers with `command_result`. The handler runs the same wizard
the classic REPL uses, but with the `EventStreamUI` passed as its `ui` (the
`choose_role`/`choose_effort`/`choose_style`/`choose_session`/`choose_models`
wizards take an optional `ui`; `None` keeps the classic `TerminalBannerUI`), so every
dialog becomes an `input_request` the Ink app renders generically. Handlers live in
`main.STDIO_COMMANDS` and receive an `event_stdio.CommandContext`; a command runs only
between turns (`_Gate` refuses it with `ok:false` while a turn or another command is
running). `session` goes through the agent's own methods, never `session_store`,
because the live agent holds history the file does not. Every slash command is
ported. The four that rebuild the agent (`provider`, `change-api`, `del-provider`,
`full-reset`) call `build_agent` through the `rebuild` closure, which substitutes
`/dev/null` for stdin — the launch-time build happens before the reader starts, so a
secret prompt there would hang forever; `full-reset`'s mouse checkbox becomes two
confirmations. **`resume` is the one command that is not a wizard**: the reader
queues it as `_RESUME` past the handlers, and `serve_session_multi` runs it with
`submit_resume`, so it answers with the turn's events and `turn_end`, never with
`command_result` (the Ink app dispatches it as a turn for the same reason, and its
`turn_end` also clears `state.command` so a core answering any command that way
cannot wedge the input box). Because `agent.resume()` may switch sessions itself, a
`session` event announces the target before the first turn event.

**Approvals.** `EventApprovalGate` sends a structured `input_request`: `subject` is
`{kind: "command", command, reason}` or `{kind: "overwrite", path, diff}`, and
`remember_pattern` is the argv the user may save — `None` for an intrinsically
DANGEROUS command (`command_policy.assess_command`), so "don't ask again" is never
offered for one. A reply carrying `remember: true` appends that pattern to the
project's allow rules; saving is best-effort and never blocks the approval itself.
**Switching interfaces.** `/tui` in the Ink app is a *local* dialog: a `choice`
`input_request` with id `local:frontend`, built in `app.tsx` from `commands.FRONTENDS`
and answered there, never sent to the core. Picking another interface writes its name
to the file named by `KVIST_SWITCH_FILE` and exits with code 64; `launch_tui` (which
creates that file) reads it, validates it through `user_config.save_frontend`, and
returns `None` so the launcher continues into the classic terminal in the same
process. The code carries no choice, so a new interface needs only an entry in
`FRONTENDS` and a launch branch in `_switch_frontend`.
Tests: `npm test` (the `tests/*.test.ts[x]` files sit beside the Python tests and use `tests/fixtures/fake-core.mjs`, never a real core).

### Role routing (`role_router.py`)
Four roles (`_VALID_ROLES`): `coder`, `reader`, `devops`, `chat`. First-match-wins
priority: an explicit no-write marker → `reader` (beats topic) → deterministic regex
rules (devops → coder → chat, bilingual EN/RU) → LLM router call on `fast_model` →
keyword fallback → default `chat`. Returns a `RoleRouteResult` (alias `RouteDecision`)
carrying the chosen role and its `source`. `reader` is a full role with its own prompt
(`prompts/system/reader_prompt.txt`): it inspects and reports, and its writes are
disabled. `main.py --role` additionally accepts `auto`, which is "let the router decide"
rather than a fifth role.

### Context budget (`context_budget.py`)
How much prompt one call may occupy is derived from the **active model's**
documented limit, not from one flat number — `context_token_limit` resolves it
per call, so escalating fast → smart mid-run re-resolves it.

**`MODEL_INPUT_LIMITS` holds max *input* tokens, never the advertised context
window**, because the two differ per vendor and only the input side bounds a
prompt: OpenAI reserves its 128k max output inside the window, so a 400k-window
GPT-5 model rejects any prompt over 272k ("Input tokens exceed the configured
limit"), while Anthropic bounds the input alone — on Claude 4.5+ a request whose
input plus `max_tokens` overflows is accepted and the *reply* is cut short
instead. Budgeting a GPT-5 model off its 400k window sends a prompt the API
refuses outright. Lookup strips any vendor prefix (`anthropic/claude-…`) and
takes the longest matching prefix; anything unlisted gets `DEFAULT_INPUT_LIMIT`
(256k).

The budget is `input_limit * 0.85` — Kvist measures with tiktoken, which
under-counts for the Anthropic and DeepSeek tokenizers, and on vendors that bill
output from the same window the same margin covers the reply. So a 1M-input
model gets 850k and a 272k-input one gets 231k. FreeFlow is the exception: its
11k is a hard per-request input limit on the free Gemini/Groq backends.

**A static table cannot be the whole answer**, because `fetch_available_models`
discovers model ids live from `/v1/models` on every launch and neither OpenAI
nor Anthropic report a context size there — an id newer than the table is the
normal case. Three rules cover it, in this order:
1. `_is_small_openai_tier` runs **before** the table: a `-mini`/`-nano` id in a
   modern OpenAI family is the 272k tier. Without this pre-check `gpt-5.5-mini`
   prefix-matches `gpt-5.5` and inherits four times its real limit.
2. longest-prefix lookup in `MODEL_INPUT_LIMITS` (vendor prefix stripped).
3. `_family_fallback` — an unlisted member of a known family inherits that
   family's current generation (a new `gpt-6-*` gets 872k, a new
   `claude-opus-*` 1M, a new `claude-*haiku*` 200k) rather than collapsing to
   `DEFAULT_INPUT_LIMIT`, which would quietly cut the budget of every model
   released after this file was written.

**A table entry must never shadow a newer generation.** Matching is by prefix,
so a bare `"gpt-5"` entry also matched `gpt-5.6-luna` and every future
`gpt-5.x`, sizing a 1M model at 272k and keeping rule 3 from ever running for
that line — hence the trailing hyphen in `"gpt-5-"`. The asymmetry to keep in
mind when adding entries: a budget that is too small is silent and permanent,
while one that is too large is corrected by the first rejection.

A guess that is too *high* is then corrected by the provider rather than being
fatal: `agent_model.is_context_overflow_error` recognises the rejection,
`context_budget.note_input_overflow` records a reduced budget for that model id
(session-local; 60% of what was refused, only ever tightening), and `call_model`
re-trims and re-sends within the same attempt budget. **A learned rejection
outranks every override**, `AGENT_MAX_CONTEXT_TOKENS` included — otherwise the
retry would resend exactly the prompt that was just refused.

Override order is `AGENT_MAX_CONTEXT_TOKENS` → an explicit non-zero
`config.max_context_tokens` → derived, then clamped by anything learned; the
config field defaults to 0, meaning "derive".

This budget is what the section-priority trimmer
(`agent_model.trim_prompt_to_token_limit`) enforces, so it also decides how much
of a file read survives into the prompt. The three caps upstream of it —
`executor.max_read_chars`, `prompt_value_char_limit`, `prompt_result_char_limit`
— exist only to stop one runaway value from filling the window, and
`prompt_value_char_limit` is never below `max_read_chars`: two independent caps
on the same content is how a 17k-char file used to reach the model as 1.6k, with
the model re-reading it a few steps later. For the same reason
`"Detailed recent action data:"` is a **priority-1** section — every other
section about a read is a summary of that one, so it is not what gets dropped
first.

`trim_prompt_to_token_limit` is measured, never estimated: `_truncate_section_text`
takes the caller's tokenizer and binary-searches the cut, and the function ends
with a hard cut to the budget. A fixed chars-per-token ratio assumes prose and
left a code-heavy prompt nearly three times over budget — and source code is
most of what Kvist puts in a prompt. Priority-0 sections are never trimmed, so
the tiers alone cannot bound the total; the final cut is what makes the promise
true.

**Required artifacts are searched for once, not per refresh.** `_anchor_artifacts`
walks the tree to find a partial path the task named; `_anchored_required_artifacts`
caches that walk and `_execute_action` drops the cache only after an action with a
side effect — a read cannot create a file. Re-walking on every refresh (twice per
action) cost ~3 s per action on this repository whenever a named file did not
exist yet.

**The detail window and the repeat guard are one mechanism.** Full results
live in `recent_result_details` (`detailed_feedback_limit`, 16), index-aligned
with `recent_result_signatures`; a repeated action replaces its own earlier copy
(`agent_runtime._keep_result_in_view`), so a batch of `git_status`/`list_dir`
cannot push a file read out. `is_repeat_blocked` refuses a repeated read-only
action only while its earlier answer is still in that window
(`_earlier_answer_in_view`): refusing to re-read what the runtime itself evicted
is how a run once spent its last five calls asking for a file it could no longer
see, and stopped as a "loop". The prompt marks such files next to `Read files`
(`agent_context._read_files_out_of_view`). The model's only memory across calls
is `step_summary` (kept on any step, `_keep_step_summary`) — reasoning and
`thought` never reach the next prompt.

**The prompt carries no standing governance.** Completion blockers, progress,
required/missing artifacts, "completion status", a compressed restatement of the
other sections, and the stand-in plan of an unplanned run are not rendered: the
model spent its reasoning reconciling them with the task (one "required artifact"
sent it to read a 938-line bundle, twice). The gates themselves are unchanged and
act when a finish is attempted — `_finish_with_final_answer` and
`_finish_with_report` put the refusal reason into `pending_instruction`
("Additional instruction", priority 0). "Allowed next actions now" appears only
while `required_next_actions` actually restricts the next step.

**Section headers in `agent_context.SECTION_PRIORITIES` must match what
`build_runtime_prompt` renders, character for character.** A header that does
not match is not a section at all: its text folds into the section above and is
trimmed at *that* section's priority. `tests/test_agent_context.py` asserts
every rendered header is a known key.

### Model tiers (`agent_model.py`)
Three roles per provider: **fast**, **smart**, **planner**. A run uses fast by default
and escalates to smart on complexity="complex", parse errors, consecutive errors, or
stagnation — unless the user explicitly pinned a model. Role system prompts are loaded
and cached from `prompts/system/` — eight files:
`{chat,coder,commit,devops,planner,reader,report,router}_prompt.txt`.

User-selectable styles live apart, in `prompts/styles/{agents,chat}/<slug>.txt`
(`agents` covers coder/reader/devops, `chat` the chat role). **One session, one
style**: the choice is stamped into the session document when the session starts
(`agent_logging.session_styles`, restored by `restore_session_document` *before*
its empty-document exit) and every read inside that session resolves it through
`agent_model._session_style_name`, never through the global preference. Without
that, a plan built on step 0 and the steps that follow it could come from
different beliefs — a paranoid plan executed by a minimalist that no longer
believes in the pre-check step it was handed. `/style` writes the global
preference and calls `agent.adopt_session_style`: an untouched session adopts the
new choice (nothing came out of the old one yet), a session that already holds
work keeps its stamp and the change starts with the next session. The selected
*file* is still re-read per call, so editing that file lands on the next step.
The planner gets the same `agents` style through `agent_model.planner_style_context`,
wrapped in a preamble that says the beliefs address the executor, not the planner;
the matching bound is the `EXECUTOR WORKING STYLE` section of `planner_prompt.txt`
(it shapes steps, success criteria and risks, never the schema, the step limit,
or how much work is taken on). The reporter (`report_prompt.txt`) does not see the
style.

### Reasoning effort (`reasoning_effort.py`)
One global knob (`/effort`, env `AGENT_REASONING_EFFORT`, persisted
`reasoning_effort`) on a fixed six-level scale: `false | low | medium | high |
xhigh | max`. `reasoning_effort.py` owns the per-provider translation, because no
two providers name or slice it the same way: OpenAI/OmniRoute get a top-level
`reasoning_effort` (with `max`→`xhigh`, `false`→`minimal`), OpenRouter gets
`extra_body.reasoning.effort`, DeepSeek gets `reasoning_effort` in `{high,max}`
(or `extra_body.thinking.type=disabled` for `false`), and the Anthropic /
FreeFlow wrapper clients receive a neutral `reasoning_effort` marker they
translate themselves — Anthropic to a `thinking.budget_tokens` block (dropping
`temperature`/`top_p`, bumping `max_tokens`), FreeFlow forwarding it only to Groq
gpt-oss models. Injection happens in exactly two places: `_create_json_completion`
(action loop + planner, via `merge_reasoning_kwargs`) and the chat path in
`agent.py`; `role_router` is left alone. `_create_completion_with_reasoning_fallback`
wraps the call: on a provider error that looks like a reasoning rejection
(`is_reasoning_error`) it strips the reasoning kwargs, retries once, and prints a
`ui.print_error` warning **every time** (no de-dup — deliberate). `/effort` also
prints an upfront advisory (`effort_advisory`) about how the chosen level maps for
the active provider. The effort level is part of the `call_model` response-cache
key.

**`reasoning_content` is never dropped by context compression.** DeepSeek's
thinking mode returns a silent HTTP 400 when a resumed/trimmed history carries an
assistant `content` without the `reasoning_content` it was produced with. Kvist
does not round-trip `reasoning_content` today (chat history stores `role`/
`content` only, the action loop rebuilds a single user prompt each step), but the
compression paths are hardened so the day it does, they won't break it:
`context_utils.trim_history` returns retained message dicts by identity (no key
stripped), `agent_model.trim_chat_messages_to_token_limit`
rebuilds every message with `{**message, ...}`, and `agent_logging._serializable_chat_history`
keeps `reasoning_content` in `_PERSISTED_MESSAGE_KEYS`. A sliding window may drop a
whole turn, but must never strip that field from a turn it keeps.

### Provider abstraction (`llm_clients.py`)
Model catalogs (`_MODEL_DEFAULTS`, `_MODEL_OPTIONS`, the fallback chains,
`_DEPRECATED_FREEFLOW_MODELS`, `_FREEFLOW_MODEL_ALIASES`) are offline fallbacks
for when live discovery fails, and `tests/test_llm_clients.py` enforces their
coherence: **no alias may resolve to a deprecated or unlisted model** (retiring
Groq's Llama models broke the `mixtral`/`allam` aliases that pointed at them),
every default must be offered by its provider, and every fallback chain must
start at an offered model. `tests/test_context_budget.py` additionally requires
every shipped model to have an explicit `MODEL_INPUT_LIMITS` entry — the family
fallback is for ids discovered at launch, not for the catalog Kvist controls.

Six core providers (`openai` / `anthropic` / `deepseek` / `freeflow` / `omniroute` /
`openrouter`) plus a **manual tier** of 36 third-party OpenAI-compatible vendors
(`MANUAL_OPENAI_PROVIDERS` in `llm_clients.py`: Qwen, Moonshot/Kimi, Zhipu, Z.ai,
Xiaomi MiMo + Token Plan, MiniMax, 01.AI, StepFun, SiliconFlow, Baichuan, Qianfan,
Hunyuan, ModelScope, Mistral, Groq, Together, Fireworks, xAI, Perplexity, Cerebras,
DeepInfra, Novita, Vercel AI Gateway, Baseten, SambaNova, Nebius AI Studio, Friendli
AI, Databricks Model Serving, Google AI Studio (direct), Cohere, AI21 Labs, ByteDance
Doubao (Volcengine Ark), GitHub Models, iFlytek Spark, Scaleway Generative APIs), plus
arbitrary user-defined endpoints under a `custom:` name
prefix. `SUPPORTED_LLM_PROVIDERS` is the six core providers followed by
`tuple(MANUAL_OPENAI_PROVIDERS)` — adding a vendor is one dict entry (`label`,
`api_key_key`, `base_url_key`, `default_base_url`); every lookup (`provider_label`,
`required_secret_keys_for_provider`, `resolve_models_for_provider`,
`fallback_models_for_provider`/`fallback_candidates_for_model`, `get_model_options`,
`_discovery_endpoints`, `create_llm_client`) keys off that dict or the derived
`MANUAL_MODEL_PROVIDERS` frozenset instead of a per-provider branch. Manual-tier
providers are wired like OmniRoute for defaults: one API key (`<slug>_api_key`, env
`<SLUG>_API_KEY`), a region-overridable default base URL (`<slug>_base_url`), and
**no static `_MODEL_DEFAULTS`/`_MODEL_OPTIONS` entries** — these vendors' model ids
change too often for a hardcoded default to stay correct. `databricks` is the one
entry with `default_base_url = ""`: every workspace has its own
`<workspace>/serving-endpoints` URL, so `create_llm_client` raises a clear error and
`_discovery_endpoints` contributes no endpoint until `databricks_base_url` is set —
never silently falls through to another vendor's default. `gemini` and `groq` are
deliberately separate credentials from FreeFlow's own `gemini_api_key`/`groq_api_key`
(`gemini_direct_api_key`/`groq_direct_api_key`): FreeFlow talks to both through its
own SDK wrapper, these manual-tier entries talk to them as plain OpenAI-compatible
endpoints, and the two paths must stay independently configurable. Unlike OmniRoute's wizard,
though, `configure_manual_provider_models_interactively` in `main.py` is wired like
OpenRouter's: it calls `_model_picker_options`, which is `_discovery_endpoints`'s
`/v1/models` catalog fetch for that provider, and offers a "choose from the live
catalog" picker whenever discovery succeeds; text entry (one id, or one per role) is
only the fallback for a first run with no key yet or an endpoint without `/v1/models`.
`openai`, `omniroute`, `openrouter`, `deepseek` and every manual-tier provider go
through the OpenAI SDK directly (OmniRoute is a local OpenAI-compatible gateway
defaulting to `http://localhost:20128/v1`, models entered manually); `anthropic` uses
`AnthropicCompatibleClient`; `freeflow` wraps Gemini+Groq via `FreeFlowCompatibleClient`
with cross-backend fallback. Capability is per provider, not one assumption for all:
`provider_supports_streaming` is true for openai, openrouter, anthropic, deepseek and
`anymodel` (the rest of the manual tier defaults to false, same as an unverified custom
endpoint, until one is individually verified against the live endpoint — AnyModel is
listed because it is the blank-start default, so leaving it off meant a first run never
streamed at all), and `provider_supports_json_mode` is true for
openai, openrouter and deepseek only. `resolve_models_for_provider` /
`validate_model_selection` / `fallback_candidates_for_model` handle defaults,
validation, and model fallback chains.
`fetch_available_models` queries the provider's `/v1/models` on **every** launch and
degrades to configured models with a warning when that fails.

### Execution boundary (`executor.py` + `terminal.py` + `command_policy.py` + `sandbox.py` + `process_tree.py` + `secret_paths.py`)
`SafeExecutor` is the single gate for every action and returns an `ExecutionResult`
(`ok`, `action_type`, `data`). `search_code` runs a trusted system `rg` (`--no-config`,
fixed-string, case-insensitive, `.gitignore`-aware, symlinks not followed; the result
says `skips_gitignored_files`) and passes every hit through the same read boundary as
`read_file`; without one it falls back to walking the tree in Python, which read every
file whole and took 8.5 s on this repository. It enforces the workspace boundary (all paths resolved and
checked with `relative_to`, symlink-escape protection), blocks the internal `.kvist`
dir, and **strictly forbids reading or writing `.env` files**.

Before any of that, `SafeExecutor._relativize_workspace_paths` rewrites an absolute
`cwd`, absolute argv tokens and a leading `git -C <dir>` into relative form **only when
the path's real (resolved) location is inside the workspace** — anything else stays as
written and is refused as before. The policy never sees an absolute path it would
refuse for being absolute, and the model is told each rewrite (`path_note` in the
result, appended to the error when the command is refused anyway) so it switches form
instead of repeating it: a task naming the repository by its absolute path otherwise
produced "Hard security denial" after "Hard security denial" for paths that never left
the workspace. The native tool schemas carry the same rule in their `path`/`cwd`/
`command` descriptions.

Shell path, in order: `SafeExecutor._run_shell` (arg checks, `.env`/`.kvist` guards,
forwards the routed role) → `TerminalExecutor.run(command, role=...)` → project deny →
one authoritative hard validator for every pipe segment → intrinsic risk → project
ask/eligible allow → HITL where required → exact context reconstruction → the same hard
validator → context comparison → spawn. Hard-denied paths/executables/syntax never invoke
HITL and cannot be unlocked by project policy. DANGEROUS commands always remain
per-execution approvals and are never persisted. Allowlist misses are approval-eligible,
not hard-denial bypasses. The role comes from routing (`executor.set_active_role`), never
model args. HITL pauses the live TUI; non-TTY approval fails deterministically.

The bound execution context includes argv, canonical cwd plus directory identity, role,
policy digest, sandbox/profile/network, sanitized child-environment digest, lookup and
target executable identities (plus untrusted executable content), attested shebang
interpreter identity/digest, the protected-secret mask digest, the external tool-path
set, and hashes of directly selected scripts/common build files. `_execute_validated`
requires this context, repeats validation, and aborts if it changed. Parent environment
inheritance is allowlisted; `AGENT_CHILD_ENV_ALLOW` is the explicit extension mechanism
and cannot re-enable secrets or loader/startup controls. Every run uses a new private,
verified-empty child HOME, including explicit workspace mode and the automatic weak
fallback.

**Executable scripts with a `#!` line have their interpreter attested**: the shebang is
parsed (direct, `/usr/bin/env NAME`, `env -S NAME ARGS`), the interpreter is resolved
with the same sandboxed PATH, and it must be a trusted system binary or a workspace file
(else hard-denied); its identity/content is bound into the context so an interpreter or
shebang swap between approval and spawn invalidates approval. A project launcher symlink
whose target is a user-immutable system binary (e.g. `.venv/bin/python -> /usr/bin/python3`)
is permitted and keeps its own path as argv[0] so venv/`pyvenv.cfg`/site-packages work;
a symlink to any other target stays denied. `_verify_venv_python_launcher` also
accepts a venv Python launcher whose target lives outside those trusted system roots
(e.g. a `uv`/`pyenv` interpreter under the user's HOME), in every sandbox mode, but only
when the venv's own `pyvenv.cfg` declares that exact resolved path in its `executable`
field: the venv can vouch only for the interpreter it was built with, never for an
arbitrary target, and a mismatch between the declared and effective path is denied
like any other alias. Under Bubblewrap, HOME is hidden, so `terminal.venv_interpreter_roots`
read-only binds that interpreter's install root (validated like `AGENT_TOOL_PATHS`) and
`venv_interpreter_links` recreates, with `--symlink`, only the individual links on the
way to it (`.venv/bin/python3.11 -> ~/.local/bin/python3.11 -> uv/cpython-3.11-… ->
cpython-3.11.15-…`, directory links included) — nothing else under those directories
becomes visible. Without this the project's own Python, and so its tests and linters,
could not run in the default sandbox. A launcher keeps its own path as argv[0] even when
found through a bare `python` on the child PATH, or the interpreter starts outside the
venv and cannot import the project's packages.

**Project toolchains**: the child PATH prepends existing project-local tool directories
(`.venv/bin`, `venv/bin`, `node_modules/.bin`) inside the workspace, then validated
`AGENT_TOOL_PATHS` entries, then trusted system dirs — the user's full HOME/`.local`/
nvm/pyenv/cargo are never restored. External tool dirs are read-only mounted into
Bubblewrap and revalidated before spawn.

Read-only Git commands use direct `git SUBCOMMAND` syntax. The spawn argv disables
repository fsmonitor/index-hook writes, external diff/textconv, implicit signature
verification, remerge drivers, and recursive submodule helper execution; global options
before the Git subcommand fail closed. The controlled child PATH omits `/usr/local` and
other user-mutable search locations.

**Sandbox honesty**: `auto` selects a trust-checked system Bubblewrap on Linux. Its
minimal filesystem mounts the writable workspace and required runtime trees, masks
`.kvist` (tmpfs) and every protected workspace secret (`secret_paths.py` enumerates
`.env`/`.env.*`, `.ssh`/`.aws`/`.gnupg`/`.kube`/`.azure`, `id_rsa`/`id_ed25519`/
`id_ecdsa`/`credentials`, `*.pem`/`*.key`/`*.p12`/`*.pfx`/`*.crt`, etc.; files are
shadowed by read-only `/dev/null`, directories by empty tmpfs), uses an empty
HOME/ephemeral `/tmp`, hides host `/home` and `/root`, isolates process namespaces, and
denies network unless `AGENT_SANDBOX_NET=allow`. It never binds the host root. Masking
neutralises relative/absolute/symlink/runtime-constructed paths (Python, Node, shell,
AWK, sed, compiled helpers), so arbitrary child code cannot read secrets even when the
full path is never a single argv token. If auto cannot find trusted bwrap, arbitrary-code
commands enter a per-execution-approval fallback that project allow cannot silence, and
are hard-denied if no cgroup v2 scope is available for descendant control. Explicit
`workspace` is the weaker user-selected host-permission mode — it is warned and does NOT
provide secret masking or `.kvist` isolation; explicit unavailable `bubblewrap` blocks
shell execution rather than degrading.

**Process-tree control & bounded output** (`process_tree.py`): every spawn runs under a
supervisor. Detached descendants (`setsid`/`start_new_session`) cannot survive a timeout
or normal completion — under Bubblewrap the PID namespace reaps them when its init exits,
and a cgroup v2 scope with `cgroup.kill` tears the whole subtree down on both timeout and
completion (killpg fallback covers same-session children when no cgroup exists). There is
no second unbounded `communicate()`: two daemon readers drain stdout/stderr into
fixed-size buffers and discard the overflow, so parent memory stays bounded while the
child runs; the tree is always killed before the readers are joined so a held-open pipe
cannot wedge the parent.

Known limits: transitive imports/build includes and helpers spawned by trusted system
tools are not individually content-attested; repository/history-aware tools may surface
protected content not named in argv even though recursive grep/ripgrep is blocked; a
narrow TOCTOU window remains between final comparison and OS spawn. Explicit `workspace`
mode offers no OS isolation (no secret masking, no `.kvist` isolation), and detached-
descendant cleanup there depends on cgroup v2 availability. Windows descendant cleanup is
best-effort.

Every result the model can be influenced by — `read_file`, `read_multiple_files`,
`search_code`, `git_status`, `git_diff`, `run_shell`, `web_search`, `fetch_url` —
carries `untrusted: true` plus an `UNTRUSTED EXTERNAL CONTENT` notice, and
`agent_runtime.feedback_to_detail` wraps it in `<UNTRUSTED_TOOL_OUTPUT source="...">`.
JSON encoding does not escape that terminator, so the payload is passed through
`redaction.defang_prompt_boundary_tags` first: a forged `</UNTRUSTED_TOOL_OUTPUT>` or
`<PROJECT_INSTRUCTIONS>` becomes `[tag removed by Kvist]` and cannot close its own
block. Trade-off to know when hacking on Kvist *with* Kvist: a file that literally
contains those tag strings (this repo's own `agent_runtime.py`, `redaction.py`,
`prompts/system/`) reads back with the marker in place — visible, never silent, and an edit
built on it fails to match rather than corrupting the file.

## Conventions

- All modules start with `# -*- coding: utf-8 -*-` and `from __future__ import annotations`.
- Dataclasses use `slots=True`. State mutation flows through `RunState`.
- User-facing strings are bilingual EN/RU; routing and prompts handle both languages.
- Tests use fakes from `tests/conftest.py` (`FakeLLMClient`, `NullUI`, `agent_factory`,
  `make_command`, `make_plan`) — no network. Mirror this when adding tests; gate any real
  LLM calls behind the `live_llm` marker.

## Code generation rules

1. Code, comments, and internal reasoning: English.
2. Final response to the user: Russian.
3. No premature abstraction. Do not introduce a class, interface,
   or config layer for a single use case.
4. Functions do at most one thing. If a function needs a comment
   explaining sections, split it.
5. Prefer early return over nested if/else. Max nesting depth: 3.
6. No silent exception swallowing. Catch specific exceptions only,
   never bare except.
7. No speculative generality. Do not add parameters, flags, or
   extension points for hypothetical future use cases.
8. Comment only "why", never "what". If code needs a "what" comment,
   rewrite the code to be self-explanatory.
9. When a simple and a "robust" solution both satisfy the current
   requirement, choose the simple one.
