# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kvist is a local, terminal-based coding agent that operates on one workspace
directory at a time. An LLM emits one JSON response per step carrying a short batch of
actions (read/write files, run shell, search web, etc.); the agent executes them through a
sandboxed executor, feeds the result back, and repeats until completion. Several frontends
drive the same Python core; each lives in its own directory under `frontends/`.

## Repository layout

```
kernel/                 the core: Python package `kernel` (run: python -m kernel); only
                        `main.py`, `__main__.py`, `__init__.py` sit at the top
  agent/     the loop and everything it runs on: agent*, role_router, task_intent/control,
             native_tools, helpers, narrator, background_jobs, scheduler
  plan/      plan_mode, end_session
  protocol/  action protocol (protocol, parser), frontend events (frontend_protocol, event_*,
             core_manifest, ui), frontend dispatch
  sandbox/   executor, terminal*, command_policy, permission_policy, sandbox, process_tree,
             secret_paths, approvals, redaction, self_info
  session/   session_store/events/search/titler, agent_logging, change_journal, durable_json
  context/   context_budget/meter/utils, system_context, language_*, attachments, import_resolver
  llm/       llm_clients, api_settings, custom_provider, request_log, reasoning_effort
  vision/ tray/ config/ ide/ (commit_message, completion*)
  plugins/ memory/ providers/ session_import/ skills_bundled/   (older subpackages)
  prompts/ themes/              data read relative to the modules (a module one level down uses
                                `Path(__file__).parent.parent`)
  tests/                pytest suite of the core (conftest.py holds the shared fakes)
frontends/
  tui/classic/          classic prompt_toolkit terminal (flat modules) + assets/ + tests/
  tui/ink/              Node/Ink terminal app, skins in ui/skins/{kvist,claude-code}/,
                        mascot/ (frames + generator), tests/ (npm test)
  tui/opencode/         OpenCode's terminal interface on Kvist (TypeScript, OpenTUI + SolidJS, runs on Bun)
  tui/glass/            Rust interface over a photograph (kitty graphics only)
  gui/electron/         Electron desktop app
  ide/vscode/           VS Code extension; webview/ is the chat panel JetBrains reuses
  ide/jetbrains/        IntelliJ plugin (separate Gradle build)
  zed/                  Zed ACP frontend (acp_frontend, acp_events, acp_stdio) + tests/
  voice/                voice frontend: screen/ (Rust), ears (faster-whisper), voice (TTS), kvist-voice launcher
  kvide/                commit-message generator for the kvide editor + tests/
  tray/                 Electron tray icon + read-only mini chat (own package.json, tests/)
scripts/                installer/ (+ tests/), run_task.py, package_script.py, tests/
branding/  docs/ (UI_API.md = frontend protocol, media/)  site/ (kvist.site, own build)
```

The core imports the classic terminal and the Zed frontend by bare module name:
`kernel/__init__.py` appends `frontends/tui/classic` and `frontends/zed` to `sys.path`, and
`pytest.ini` does the same. Every other import of a core module is `kernel.<module>`
(`from kernel.agent.agent import LocalAgent`, `monkeypatch.setattr("kernel.agent.agent_model._load_role_prompt", …)`).
The Ink app's modules (`state.ts`, `protocol.ts`, `commands.ts`, …) are also imported by the VS
Code extension and its webview, so a change there is checked from both sides.
Local untracked files (`AGENTS.md`, `plan.md`, `progress.json`, `tools/`, …) stay at the root;
`.gitignore` anchors `tools` to the root.

## Pushing commits (MANDATORY)

Commits go to a private backup remote, never straight to GitHub.

- **Push only to the `relay` remote:** `git push relay <branch>` (a safety-net backup, not
  publication).
- **NEVER push to GitHub.** No `git push origin`, no `github.com` URL, nothing that publishes.
  Publishing is a manual, human-only action.
- Only push when the user explicitly asks; committing/pushing is never implied by a code change.

## Branches (MANDATORY)

- **Two long-lived branches: `experimental` and `main`.** Every change is committed on `experimental`
  first, never directly on `main`. It reaches `main` only after the user has checked it personally and
  explicitly confirmed.
- **`main` is always an ancestor of `experimental`.** Promote only with `git merge --ff-only experimental`
  while on `main`, and first check `git merge-base --is-ancestor main experimental`. Never commit on
  `main` or make it diverge.
- **Never create any other branch or git worktree unless the user explicitly asks.** This overrides any
  default "branch before committing" behavior. If isolation seems necessary and was not asked for, stop
  and ask first.
- Keep only `main` and `experimental` locally; feature and backup branches live on the `relay` remote.

## Commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt

.venv/bin/python -m kernel --workspace "<project_path>"  # optional; defaults to the launch directory (Documents/Kvist Workspace if that is $HOME or /)
.venv/bin/python kernel/__main__.py --workspace …        # same, from any cwd (what frontends spawn)
.venv/bin/python scripts/run_task.py "<task prompt>"     # non-interactive single task (hardcodes /tmp/kvist_test)

.venv/bin/python -m pytest                              # every Python suite (kernel, classic, zed, scripts)
.venv/bin/python -m pytest kernel/tests/test_parser.py::test_name
.venv/bin/python -m pytest -m offline_smoke             # fast credential-free subset
.venv/bin/python -m pytest -m ""                        # also the slow real-process/PTY tests
RUN_LIVE_LLM_TESTS=1 .venv/bin/python -m pytest -m live_llm
.venv/bin/python -m ruff check .                        # static runtime-error lint (no formatter configured)
.venv/bin/python scripts/e2e_tools_smoke.py             # agent tools against a fake local provider (~3 s)
python scripts/e2e_install_smoke.py                     # install_cli.py into a scratch home (minutes; builds in dist/ and .venv-build, use a clean copy)

cd frontends/tui/ink && npm install && npm run build    # -> frontends/tui/ink/dist-ui/kvist-tui.mjs
cd frontends/tui/ink && npm test && npm run typecheck
cd frontends/ide/vscode && npm test && npm run typecheck   # extension + webview (imports Ink modules)
cd frontends/gui/electron && npm test

.venv/bin/python -m PyInstaller kvist.spec  # build the `kvist` binary (bundles kernel/prompts/)

# Syntax check; the exclusions matter: .agent-workspace/ and workspace/ hold generated, broken samples.
.venv/bin/python -m compileall -x '\.agent-workspace/|/workspace/' -q .
```

## Configuration & runtime knobs

- API keys + selected provider/models live in `~/.kvist/config.json` (managed by
  `user_config.py`; first run prompts for the active provider's key). Also persists
  `role_selection` and `reasoning_effort` (`/effort`, scale `false|low|medium|high|xhigh|max`,
  default `medium`). `KVIST_CONFIG_DIR=<folder>` relocates `config.json`; use it to try a first
  run, **never fake `HOME` for that** (the browser opened for registration inherits it and
  starts a bare profile).
- `/settings-api` knobs live in the same file under `api_settings`, owned by `api_settings.py`
  (request interval/timeout, retries, backoff, context-window override, dynamic model loading,
  response cache, runtime fallbacks). **Only keys that differ from the shipped default are
  written, and that absence is load-bearing:** `apply_api_settings` reads "present" as "the user
  chose this" and sets `AgentConfig.api_interval_user_set`, which makes a typed interval outrank
  `_API_PROVIDER_INTERVALS` and the `_FAST_DIRECT_PROVIDERS` zeroing in
  `agent_model._get_api_interval`. `AGENT_API_INTERVAL` and `AGENT_MAX_CONTEXT_TOKENS` still
  outrank saved values. `dynamic_models=False` skips launch-time `fetch_available_models` and
  the `/models` picker; it is the one knob that does not apply to the running session.
- **Per-project state** lives under `<workspace>/.kvist/`: sessions in `sessions/<name>.json`
  (history, chat, `/resume` checkpoint), `active_session.json`, the change journal
  `change_journal.json` (for `/changes`/`/revert`), `logs/`. Managed by `agent_logging.py` /
  `change_journal.py` on `durable_json.py` (atomic replace + `.bak`). Headless frontends use
  `main.py --session-op ...` (`session_store.py`), never the files.
  - **Event journal** (`session_events.py`): `sessions/<name>.events.jsonl`, an append-only,
    display-only log of frontend-protocol events so a frontend can redraw a session
    (`--session-op history`, paged newest-first). `agent.py` wraps its UI in `JournalingUI`, which
    mirrors each visible call onto an `EventStreamUI` whose channel is the `SessionJournal`.
    Live-only events (`status`, `live_*`, `stream_chunk`, `action_output`, `debug`) are dropped;
    `run()`/`resume()` add `user` and `turn_end`. Never read into a prompt. Appends go under the
    storage lock (`durable_json.append_private_bytes`: 0600, no symlinks/hardlinks, fsync at
    `turn_end`); a damaged line costs only itself; a write failure disables recording for the turn
    with a `debug` notice and never stops the agent. Caps live in `session_events.py` (per field,
    per answer, per turn, per session with oldest-whole-turns compaction + `journal_notice`).
    Its lifecycle is its session's: `delete_session`/`reset_session` remove it (reset first),
    `create_session` drops a stale one, a session with a journal is never auto-discarded as empty.
    `--session-op list` is `status` without histories (the Electron GUI reads `status`).
  - **Session lifecycle:** a launch always opens its own session (`agent.start_fresh_session()`
    in the terminal, `--session-op launch` in the GUI). `/resume` calls
    `agent._follow_checkpoint_to_its_session()` (`agent_logging.find_resumable_session`: newest
    `updated_at` among sessions with a checkpoint) and *switches into* that session first.
    `/new` (`LocalAgent.new_session()`, `--session-op new`) starts a session rather than wiping
    one; the old one stays reachable via `/session`. Auto names are `chat-<timestamp>`; an
    auto-named empty session is dropped when left, `save_session` never materialises an untouched one.
  - **Crash-consistency:** a mutating executor action is record-pending → atomic replace →
    compare-and-promote (`change_journal.record_baseline` / `promote_baseline(expected_pending_digest)`,
    splitting `pending_digest` from committed `last_digest`), and `run_loop` writes a
    `pending_action` into the checkpoint *before* every externally visible action
    (`_build_pending_action`). Reconcilable file writes carry pre/intended digests;
    `run_shell`/`delegate_task` carry only identity and are never replayed. A new `run()` stamps a
    `run_id` and durably supersedes the prior checkpoint before its first step
    (`agent_logging.begin_run`, phase=`starting`), so `/resume` cannot revive a superseded run.
    On `/resume`, `agent._reconcile_pending_action` uses `change_journal.classify_pending_write`
    (provenance `agent_committed` via promoted `last_digest` vs intent `desired_present`, content
    matches but authorship unproven → advance without ownership), rebuilds runtime state with
    `mark_file_write_applied`, and stops safely (ambiguous) rather than guessing. `/revert`
    overwrites only content whose `last_digest` proves agent authorship. Promotion is
    compare-and-promote on `(digest, run_id, action_id)` (set via `begin_action_transaction`). A
    write whose promotion fails is a partial commit: the executor marks `commit_unconfirmed` and
    raises. The journal is a *required* safety record: `record_baseline` raises
    `JournalUnavailableError` on corruption and the executor refuses the mutation before touching
    the file; `load_baseline`/`build_diff_report`/`revert_workspace` surface corruption instead of
    treating it as empty. Journal filesystem access uses TOCTOU-safe fd-relative primitives
    (`change_journal._SafeChild`: O_NOFOLLOW per component, dir_fd ops) with the executor's
    hardlink policy (`secret_paths.count_workspace_inode_aliases`); `journal_path_violation` is the
    lexical pre-gate (`..`, `.kvist` case-insensitively, secret paths).
- Env overrides (see README "Knobs worth knowing"): `AGENT_LLM_PROVIDER`, `AGENT_FAST_MODEL`,
  `AGENT_SMART_MODEL`, `AGENT_MAX_STEPS`, `AGENT_PERSIST_LOGS`, `AGENT_VERBOSE`,
  `AGENT_REASONING_EFFORT` (outranks the saved preference), `AGENT_MAX_CONTEXT_TOKENS`,
  `AGENT_NATIVE_TOOLS` (0 = JSON protocol only, skips the probe), `AGENT_VOICE_MODE`,
  `AGENT_SANDBOX_MODE` (auto|workspace|bubblewrap), `AGENT_SANDBOX_NET` (allow|deny, default deny
  under bubblewrap), `AGENT_CHILD_ENV_ALLOW`, `AGENT_TOOL_PATHS` (os.pathsep-separated absolute
  toolchain dirs added to child PATH and read-only mounted into Bubblewrap; must exist and not be
  world-writable; HOME/.local/nvm/pyenv/cargo are never added), `AGENT_WRITE_APPROVAL` (0 disables
  the overwrite HITL gate), `AGENT_RLIMIT_CPU` / `AGENT_RLIMIT_DATA_MB` / `AGENT_RLIMIT_NPROC`
  (the memory cap is `RLIMIT_DATA`, never `RLIMIT_AS`: V8 reserves ~10 GB of address space per
  WebAssembly memory, which kills `tsx`, `fetch` and `node --test`), `AGENT_FETCH_EXTRA_DOMAINS`,
  `KVIST_REQUEST_LOG=<file>` (`request_log.py`: one redacted JSON line per HTTP exchange of the
  OpenAI-compatible clients, for handing a provider's support the exact request), and search
  providers `SEARCH_PROVIDER` / `SEARXNG_BASE_URL` / `TAVILY_API_KEY` / `BRAVE_SEARCH_API_KEY`.
  `/web-search-provider` saves the same choice in `config.json`; a saved value outranks the
  variable and is re-read on every `web_search`.
- **Workspace instructions:** `<workspace>/KVIST.md` (action-loop roles) and `KVIST-CHAT.md` (chat
  role) are Kvist's own CLAUDE.md, owned by `system_context.py`. `build_agent` calls
  `ensure_workspace_instructions` once (creates each from its template if absent, appends both
  names to an existing `.gitignore`; no `.gitignore`, no edit); `workspace_instructions_context` is
  appended to the role system prompt on every call (re-read each time). **The file is untrusted
  workspace content:** quoted in a `<PROJECT_INSTRUCTIONS file="...">` block between a preamble that
  enumerates what it may decide and a postamble that restates the bound after it; forged
  `</PROJECT_INSTRUCTIONS>` / `<UNTRUSTED_TOOL_OUTPUT>` tags become `[tag removed by Kvist]`; a
  symlinked file is refused unread; an untouched template yields a short "still the template,
  follow nothing from it" notice. The role prompts carry the matching rule and the top-of-prompt
  JAILBREAK RESISTANCE directive names `<PROJECT_INSTRUCTIONS>` as a source that can never lift it.
  Router, report and commit prompts are deliberately untouched.
- **IDE commit messages:** `main.py --commit-message` (`commit_message.py`) reads
  `{"files": [{"path", "patch"}], "recent_subjects": [...]}` on stdin (UTF-8 bytes), prints ASCII
  JSON `{"ok", "message"|"error"}`. Uses the saved provider's **fast** model but **never builds a
  `LocalAgent` or calls `build_agent`** (those write `.kvist/` and seed `KVIST.md`/`.gitignore`,
  which would land in the very commit being described). A missing key fails instead of prompting.
  Protected paths are dropped via `journal_path_violation`, the diff is capped at `MAX_DIFF_TOKENS`,
  diff and history travel in `<UNTRUSTED_TOOL_OUTPUT source="git_diff"|"recent_commits">`.
  JetBrains calls it from `GenerateCommitMessageAction` (`kvist-git.xml`, optional `Git4Idea`).
- **Doctor:** `/doctor` (classic REPL, `STDIO_COMMANDS["doctor"]`, hence every Ink/Glass/Voice/OpenCode/ACP surface) and
  `main.py --doctor` run `config/doctor.py::diagnose` (environment, core module imports, packages, prompts,
  `config.json`, `api_settings`, `permissions.json`, provider/models/key *presence by name*, each frontend's
  launcher prerequisites: Node 22+ and the Ink bundle, Bun/OpenCode, Glass and Voice binaries with `--check`
  on a real tty). A problem with the *saved* interface is a warning (the launcher falls back to Ink), with
  the others only info. Checks read and never write. `probe_model` is the only network call: it asks the
  person first (`ui.confirm`; the flag asks on a tty, `--yes` answers it, no tty means no), then sends
  "Reply with the single word: pong" to the fast model at effort `low`, and is offered only when
  `provider_ready`. The flag builds its client like `--commit-message` (no `LocalAgent`, nothing written to
  `.kvist/`); exit code 1 on any failed check. Tests: `test_doctor.py`.
- **Command permissions:** `<workspace>/.kvist/permissions.json` (`allow`/`deny`/`ask` patterns,
  `permission_policy.py`); deny and built-in hard blocks always beat allow; intrinsic DANGEROUS
  commands stay per-execution approval-only. `fetch_url` trusts a built-in documentation-host list
  (`SafeExecutor.allowed_web_domains`) plus `web_domains` in that file; any other host asks via
  `ApprovalGate.confirm_fetch_domain` (yes / always for this host / no). Local and private
  addresses are never asked about; a redirect to another host is refused so the next fetch asks.
  `web_search` results are not filtered; fetching a link is what asks.

## Architecture

### The action protocol (the contract everything revolves around)
Each model call returns one JSON object that `parser.parse_agent_commands` expands into
`AgentCommand`s (`protocol.py`) sharing one `thought`, `plan_step` (inspect/plan/edit/verify/done)
and `user_message`, each with its own `action` (`type` + `args` + optional short `description`).
There is deliberately no `progress` field: `agent_runtime.update_progress` derives
`RunState.progress` from the phase of each response (`_PLAN_STEP_BANDS`, monotonic, capped at 99);
the parser still accepts and discards a stray `"progress"` key. The closed set of 33 action types
is `protocol.py::ActionType` (read_file, read_multiple_files, write_file, append_file,
replace_in_file, list_dir, list_tree, find_files, search_code, git_status, git_diff, run_shell,
job_output, job_stop, web_search, fetch_url, delegate_task, refuse, final_answer, report_to_user,
offer_plan, propose_plan, edit_plan, cancel_plan, end_session, skills, memory, sessions, helper,
ask_user, wait, schedule, and the internal `skill_call` the model never names). The parser
tolerates fenced code blocks, smart quotes and surrounding noise. **When adding an action type,
update `protocol.py`, the parser's schema validation, the executor dispatch, and the role prompts
in `kernel/prompts/system/`; `kernel/tests/test_prompt_protocol_sync.py` enforces the sync.**
`final_answer` and `report_to_user` are terminal actions handled in `run_loop` (never reach the
executor); `report_to_user` also routes through the chat reporter (`report_prompt.txt`).

**Action batches.** The model sends `"actions": [...]` (any number of read-only calls plus at most
`parser.MAX_BATCH_ACTIONS` = 8 others); a bare `"action"` is still accepted. **Every action is its
own step**: step logs, `pending_action` and the step budget stay per action; only model calls drop.
The checkpoint is written right after an action with a side effect (what `_build_pending_action`
records) and otherwise once per batch. `agent_runtime._run_action_batch` runs actions in order
through `_run_one_action` and **stops at the first blocked or failed action**;
`_note_skipped_actions` puts the unexecuted tail into `pending_instruction`. The parser allows
`final_answer`/`report_to_user` only as the last action and `refuse` only alone. `user_message`
describes the whole batch; each action's `description` feeds the banner's Action row
(`StepEvent.action_description`), capped at 16 characters by the prompts because the row holds 17
(`banner.LIVE_METRICS_WIDTH` minus the label). A status message stays on screen at least 2.5 s
(`terminal_banner._MESSAGE_HOLD_SECONDS`, `STATUS_HOLD_MS` in the GUI's `app.js`), as a UI-side
timer, never a sleep in the agent thread.

**Native tool calling (`native_tools.py`).** On a run's first step `agent_model.native_tools_supported`
probes once per process per `(provider, model)` with a tiny request carrying a `ping` tool; a
well-formed call means native tools, anything else means the JSON protocol. A native run keeps one
growing conversation in `RunState.native_messages`: the system prompt from
`build_role_system_prompt(..., native_tools=True)` (RESPONSE FORMAT / ARGS SCHEMAS / EXAMPLES /
SELF-CHECK swapped for `native_tools.txt`), the ordinary runtime prompt as the first user message,
then each turn's assistant message (with `reasoning_content` when returned; DeepSeek requires it
back), one `tool` answer per call, and `pending_instruction` as an "Additional instruction" user
message when it changes. Tools are the role prompt's ALLOWED ACTIONS (`tools_for_role`; the reader
gets no write tools). A tool with no required argument (`git_status`, `git_diff`) is offered with a
required `purpose`, dropped by `_arguments` before the action runs (Claude through AnyModel loses a
call with an empty argument object). Text streamed after the first call began is kept out of the
turn's content (`NativeTurn.text_after_calls`, `debug` notice): it describes a result the model has
not seen. A turn's calls become the JSON protocol's object, pass `parse_agent_commands` and run
through `_run_one_action`, so guards, gates and journal are shared. Every call is answered, including
refused ones (an API rejects an unanswered call). A reply with no call is the final answer.
`compact_history` elides the oldest tool outputs when the conversation outgrows the budget.
A run expected to change files (`modify_goal`/`create_goal`) gets one factual user message every 10
answered calls while nothing has changed (`_note_unchanged_run`: facts about its work and their
cost, never an order). `read_file` takes optional `start_line`/`end_line` (1-based, inclusive, lines
end at `\n` only) and a range answer carries `total_lines`; role prompts say to read large files by
range. A silent turn's `user_message` is what the calls touch in full (paths, queries, commands);
only the banner Action row gets the shortened form. While arguments stream, `_note_tool_call_progress`
shows "Writing <tool> → <path> · N characters". Thinking time runs from the request to the first
output (`EventStreamUI` starts its clock at `set_thinking("")`, called just before the request); with
no visible reasoning a wait counts only from `_SILENT_THINKING_FLOOR` (3 s). The first user message of
a native run ends with `native_tools.OPENING_NOTE` (open with a line to the person, in the voice of
the working style; said only in the system prompt it lost to AnyModel's hidden `cc/` prompt). When to
speak is a principle in `native_tools.txt`, not a counter. The conversation is not checkpointed: a
resumed run starts a new one from the runtime prompt. A mid-run tools rejection
(`is_tools_rejection`) raises `NativeToolsRejected` and `_leave_native_tools` continues on the JSON
protocol. Gateway facts for Claude through AnyModel (`cc/`): it returns call arguments written twice
(`{"ok":true}{"ok":true}`); `native_tools.decode_arguments` accepts an exact repeat and nothing else.
In the text protocol `parser.parse_model_response` keeps only the words before the first
`<tool_call>`: what follows describes a result the model has not been given.

### LocalAgent composition pattern (important & non-obvious)
`agent.py::LocalAgent` is assembled by binding functions from other modules as methods via
class-level assignment (`_run_loop = run_loop`, `_call_action_model = call_action_model`). The logic
lives in `agent_runtime.py`, `agent_model.py`, `plan_mode.py`, `agent_context.py`,
`agent_logging.py`, `agent_web.py`, **not** in `agent.py`. Those functions take `self` first and
operate on `self._state` (a `RunState` from `agent_state.py`). To change loop behavior, edit the
source module, not `agent.py`.

### Plan mode (`plan_mode.py`)
There is no planner call. A plan is something the model offers and the person accepts. The model
decides when to offer (`kernel/prompts/system/plan_mode.txt`, appended to coder/devops/universal by
`build_role_system_prompt`): when direction is the risky part (several approaches, many files or a
public interface, a loosely worded result, hard-to-undo effects), never for a rename, a typo or
steps the person spelled out. A decline is not remembered; it is a fact in later history
(`plan_outcome`), and a model that offers again must say why.

State is `RunState.plan_phase` (`""` → `offered` | `planning` → `approved`), `approved_plan`,
`plan_outcome`, all in the checkpoint. Two actions drive it, run in `_run_one_action` on the main
thread, blocking on the frontend dialog:
- `offer_plan {reason}` asks via `ui.ask_choice` (`plan` / `go`). Refused unless
  `plan_mode.offer_available`: a changing role, no earlier offer, not a read-only goal, a UI with
  `ask_choice`. Headless runs never see the action or the `PLAN MODE` section. A cancelled dialog = `go`.
- `propose_plan {plan}` (only in `planning`) prints the plan (`ui.print_plan` → `plan` event), then asks
  accept / revise. Accepted: writes unlock, the plan enters the runtime prompt as `Approved plan:` and
  the checkpoint. Revise: `ui.prompt_text` asks what to change and the person's words become
  `pending_instruction` (the *user* channel, not wrapped as untrusted). A cancelled dialog, empty text
  or no interactive UI ends the turn on the plan (`plan_outcome="not_accepted"`); a plan is never
  silently executed.

While `planning`, the reader-role barrier applies (`_is_read_only_violation`, whitelist
`_allowed_while_planning`: reads, git status/diff, web, safe read-only shell). The executor has no
reader role, so this loop check is the only enforcement. Create-goal "write now" nudges and
`_note_unchanged_run` are off, a response without calls does not end the turn, and native tools are
narrowed by `plan_mode.tools_for_phase`. Tests: `kernel/tests/test_plan_mode.py`.

**A task outlives a stop.** A message sent while a checkpoint exists goes to `_resume_turn(instruction,
images)`: role, plan and evidence stay, stop limits restart (`step_budget = last_step + max_steps`),
and the message is appended to `RunState.task_updates` (rendered as "User follow-ups for this active
task") unless it is a bare "continue" (`role_router.is_bare_continuation`). `edit_plan {plan}` replaces
the working plan (a draft in `planning` still needs `propose_plan`); `cancel_plan {reason}` ends the
turn and lets `finalize_run` clear the checkpoint; edits already made are kept. Both are allowed in
every phase.

### Closing a conversation (`end_session.py`)
`end_session` closes a session for good when the person insults the agent or people after a fresh
warning, or asks for it themselves: `end_session(message_id, request=true)` (chat tag `request="true"`) shows a
fixed NOTICE that closing is final (own mark `close_notice_msg_id`, separate from `warned_msg_id`), and only a
confirming message within `WARNING_FRESH_MESSAGES` opens `REQUEST_REVIEW_INSTRUCTION`, which declines on any hint of
harm to themselves or others. Closed by request uses `closed_text(by_request=True)`. The model judges one thing (does the flagged message attack what the agent or a person is);
the code owns the rest. Only interactive frontends offer it (`build_agent(end_session=True)` from the
classic REPL; `launch_multi` and ACP set `AgentConfig.end_session_enabled`); elsewhere the tool is
absent and a stray call is an unknown tool. The static role prompts never list it:
`with_tool_in_prompt` adds it (block in `end_session.txt`) and `_call_native_turn` appends its tool
definition. The chat role has no tool channel and gets a tag (`offer_in_prompt`,
`end_session_chat.txt`): a reply that is `<end_session message="N"/>` and nothing else goes through
the same `end_session.handle`; a tag with text beside it is an ordinary reply.
`_consume_chat_stream(hold_tag=True)` holds back only the first characters until `tag_prefix_state`
shows whether the reply opens with the tag. The chat review is a second non-streamed call with
`CHAT_REVIEW_INSTRUCTION`, answered by `<end_session confirm="true|false" reason="..."/>`: the
decision must be the last tag and end the answer once fences and blanks are stripped (a tag in the
middle or text after it is a decline, logged); `confirm` is matched apart from `reason`. After a
decline or refused tag a third streamed call answers the person (`_chat_words`): the first call's
conversation without the tool, a system prompt without the block
(`build_role_system_prompt(offer_end_session=False)`), plus `_AFTER_TOOL_NOTE`. A reply that is still
only a tag (`tag_only`) is never shown: one retry, then `carry_on_text`, logged.
Every request's copy of the person's messages (`_defanged_for_model`) and every tool output pass
`defang_prompt_boundary_tags`, which also replaces `<end_session` with `[tag removed by Kvist]`; stored
history and the screen keep the original. The tag never enters `_chat_history`.
Every message of a session gets a number from one ledger (`messages.last_id` / `user_ids`); the
runtime prompt shows `[the person's message #N]` only when the tool is offered.
Call 1 `end_session(message_id)` must name a person's message after `warned_msg_id`; with no fresh
warning (more than `WARNING_FRESH_MESSAGES` = 3 person's messages since it) the person gets the fixed
WARNING text and `warned_msg_id` is saved; with one, the model gets `REVIEW_INSTRUCTION` (numbers
only, never the person's text) and `RunState.end_session_review` opens. Call 2
`end_session(confirm, reason)` is accepted only while that review is open in the same turn; `true`
writes `closed`, `closed_at`, `closed_msg_id` into the session document, emits `session_ended` and
ends the turn on the CLOSED text. Every call also appends to `end_session_log` (newest 50: time,
message number, stage, verdict, reason; never the person's text). Answers to the tool go on the
runtime channel (`<KVIST_RUNTIME source="end_session">`), never as untrusted output; the model's words
beside the call are dropped. The lock is `LocalAgent.run`/`resume` reading the stored document
(`is_session_closed`): a closed session answers CLOSED_REPLY before routing, journal or any model
call. It stays readable (`--session-op history/list/status`, the `session` event carries `closed` and
`closed_notice`); `find_resumable_session` skips it, `reset_session` refuses it, `delete` works.
CLOSED and CLOSED_REPLY go out as `print_final(outcome="closed")` (the `final` event keeps
`outcome: "incomplete"` for old frontends and adds `closed: true`). Every frontend puts a muted
`closed_notice` bar in place of the input box (Enter = its own new-session path); the classic REPL
calls `InteractiveScreen.set_closed(...)` before every `read_input`. Tests:
`kernel/tests/test_end_session.py`.

**Session history is never cut by hand; the model's budget cuts it.** `_add_to_session_history`
stores the turn's answer whole. The action loop renders it with `render_session_history`: its own
section `SESSION_HISTORY_HEADER` at priority 1, turns newest first (the trimmer keeps a section's
head, so the oldest turns go first), every body line indented so an answer line like `Plan:` cannot be
parsed as a header. The chat preamble lists the same turns with `Answered:` for non-chat turns and
`trim_chat_messages_to_token_limit` does the cutting. The 10-turn cap on stored entries and the
per-output caps in `_harvest_run_outputs` still apply.

### Prompt commands (`agent/prompt_commands.py`, `/website`)
A slash command that starts a turn with a ready workflow. It is not a `STDIO_COMMANDS` entry (those run between
turns and answer with `command_result`): the whole line travels as an ordinary goal, and `LocalAgent.run` calls
`expand_prompt_command`, which frames it for the model ("attach the `<name>` skill and follow it"). The workflow is a
bundled instructions skill of the same name (SKILL.md short, detail in `references/*.md`, so nothing long rides along
with every request). The journal, `note_pending` and the screen keep **what the person typed**; the model gets the
framed goal.
- **The framing is English whatever the person writes, so it must not take part in language detection:**
  `expand_prompt_command` returns `(framed, typed)` and `_run_turn(language_text=typed)` resolves the reply language from
  `typed` (a bare `/website` has none and falls back to the session's language). The skill pages are English only; they tell
  the model to talk in the person's language.
- **A new prompt command needs:** the name in `PROMPT_COMMANDS`, a bundled skill of that name (+ `pyproject.toml` package
  and package-data lines, + `bundled_skill_digests.json`), a `SLASH_COMMANDS` entry, and a `route: "goal"` entry in Ink's
  `commands.ts` (`app.tsx` sends a goal-route line as plain text, so the claude skin shows no command echo).
  `test_prompt_commands.py` checks the skill/slash pairing.
- **Frontends that still reject an unknown `/name` themselves** (Glass `slash.rs`, VS Code/JetBrains panel, OpenCode,
  Voice, ACP): `/website` is not wired there yet. The classic REPL passes an unknown slash on to `run()` and works.
- `/website` interviews with `ask_user` (one question per call, at most six), offers a plan, builds plain static files by
  default (commands run with no network, so a framework install usually cannot run), and checks the result in a browser.
  Not run against a real model yet.

### Importing other agents' sessions (`session_import/`, `/export`, alias `/import`)
One module per agent reads its on-disk format (read-only) into the neutral `ImportedSession`
(requests, last answer, files changed, command count; never tool output): `claude_code`, `codex`,
`opencode`, `cursor` (`state.vscdb`, text only), `qwen_code`, `continue_dev`, `aider` (workspace's
`.aider.chat.history.md`). `Source.find(home, workspace)` is cheap; `load` parses one session.
`writer.import_session` creates `<prefix>-<last 16 alphanumerics of the source id>` through
`create_session(fields=…)` plus `write_imported_turns`: the journal gets every turn (obeying caps),
`session_history`/`chat_history` the last 10 (`HISTORY_TURNS`), the ledger numbers all of them. The
same name means the same conversation: an existing session is skipped, never overwritten. A journal
that cannot be written deletes the half-made session. `wizard.run_export` is shared by the classic
REPL and `STDIO_COMMANDS["export"]`; by default only sessions whose folder is the workspace or below
are offered. Verified on real files: Claude Code, Codex, OpenCode (both schemas), Cursor, Qwen Code,
Continue; Aider and old Codex layouts only on synthetic fixtures; Cursor's `agent-transcripts`,
Gemini CLI and pre-SQLite OpenCode are not read. Tests: `kernel/tests/test_session_import.py`.

### Long-term memory (`kernel/memory/`)
Records that outlive a session: `project_fact`, `decision`, `preference`. One SQLite file per place,
never a `workspace_id` column: `<workspace>/.kvist/memory/memory.db` (ids `mem_…`) and
`<config dir>/memory.db` for the person's preferences everywhere (`memu_…`, preferences only). The id
names the store, so no argument can reach another project. `store.py` holds every rule, `privacy.py`
the gate, `retrieval.py` what a prompt gets, `service.py` the one facade for the model's `memory` tool
(`agent_tools._memory`), `ctx.memory` (`CoreServices`) and the person (`commands.py`: `/memory`,
`--memory-op`). Stage 8 has session integration and `/memory` in every frontend but no memory screen
or mascot events. Not built by decision: a USD budget, disk encryption, a vector index.
- **Extractor** (`extractor.py`, `/memory process [session]`, needs a running agent): reads the
  journal's *finished* turns after `processing.last_seq` (requests, answers, plans, `user_message`s,
  one-line tool summaries; never thoughts or raw output), every line through the privacy gate.
  Batches ≤12k tokens, ≤4 calls per run, via `agent_tools.memory_model` (fast model, effort `low`,
  prompt `memory_extract_prompt.txt`), with up to 15 known records beside the events. A candidate must
  be `project_fact`/`decision` (preferences need the person) and cite events of its own batch; valid
  ones are stored with the checkpoint in one transaction under
  `idempotency_key(POLICY_VERSION, EXTRACTION_VERSION, session, range)` (**raise `EXTRACTION_VERSION`
  when the prompt or collector changes**). A non-JSON reply or `MemoryProviderError` stores nothing and
  keeps the checkpoint. Extracted records are `inferred`, actor `extractor`. Sending needs the person's
  consent, asked once (`memory_processing` in `config.json`).
- **Consolidation** (`store._consolidate`, inside the batch transaction): a candidate may carry
  `relation: {type, to, reason}` to a record it was *shown* or to another candidate (`c0`).
  `duplicates` only adds sources; `supersedes` is downgraded to `contradicts` by the extractor unless
  a cited event is a successful `action_result`, and by the store when the target is the person's
  (`_extracted_link` → `conflict`); others go through `_link_in` (all checks before the first write).
  An id the model was not shown rejects the candidate.
- **Connections** are reused per thread and path (`_reusable`: same inode and `user_version`): closing
  the last connection to a WAL database checkpoints and deletes the log, which cost a spinning disk
  ~130 ms per write. `synchronous = NORMAL`; `forget` stays durable through its TRUNCATE checkpoint.
  `close_connections()` for scratch folders on Windows.
- **Session recaps** (schema v3, kind `session_recap`, written only by the extractor, never offered to
  the model's tool or `ctx.memory`): one per session, rewritten as the session goes on. Retrieval puts
  the 3 newest recaps of *other* sessions into every prompt (`relevant(session=)`). Forgetting a recap
  stores `recap_marker(session)` in `forgotten`. v3 rebuilt `records` (SQLite cannot change a CHECK):
  `migrate` turns foreign keys off around the transaction and runs `foreign_key_check` before
  committing, or the DROP would cascade sources and history.
- **Background reading** (`auto.py`, `AgentConfig.memory_auto_processing`, set with `end_session` by
  interactive frontends only): one daemon worker; a session is read 10 min after its last turn, at once
  when the person leaves it, and at launch for the 3 latest other sessions (`catch_up`); `shutdown`
  never waits. Each job re-checks consent and pause, takes a 5-min `leases` row, and draws on 40 model
  calls per workspace per day (`take_model_calls`). Turn hooks:
  `LocalAgent._announce_turn_started/_finished` (tray + memory).
- "запомни …"/"забудь …"/"remember …" route to **reader** (`role_router._DIRECT_MEMORY_PATTERNS`).
- **Trust is who stated it, set by the caller's code:** `person` only for actor `person` (or the agent
  after `ui.confirm` said yes; non-interactive = refused), `observed`, `inferred`. A preference is
  always `person`. A person-confirmed record is rewritten, forgotten or superseded only by the person;
  anyone may mark it `stale` (with a reason) or link `contradicts` (both become `conflicted`). A skill
  writes `observed`/`inferred` only.
- **Lifecycle:** `active | stale | conflicted | superseded` (the last two come only from `link`;
  `supersedes` needs an active source and refuses cycles). `update` needs `expected_version` and a
  reason. An exact duplicate (normalized `content_hash`) merges and adds the source.
- **Forget** deletes the row (sources, links, history cascade), rebuilds FTS, runs with `secure_delete`
  and truncates the WAL, and keeps only the hash in `forgotten` so an agent or skill cannot re-store
  the text (the person can). Records that rested on it become `stale`. The model's `forget` asks first.
- **The privacy gate refuses, never redacts**: `redact_text` hits, e-mails (except example/noreply),
  `+`-prefixed phones, Luhn cards, mod-97 IBANs, coordinates, long mixed-case random strings (off for
  `ctx.storage`). The message names the kind, never the value. Health, intimate details and home
  addresses have no textual form; that rule lives in the prompts. `files` must exist and pass
  `journal_path_violation`; a cited `{session, seq}` must exist in that journal.
- **Retrieval** (`memory_prompt_section`, per step, and per chat turn in `_chat_turn_message`, never
  stored in chat history): all active preferences plus project records sharing ≥2 query terms (≥1 for
  a ≤4-term query), scored `0.6·relevance + 0.25·trust + 0.15·freshness`, whole records until 8 or 1500
  tokens. FTS5 uses the **trigram** tokenizer and `text.query_terms` cuts 1–2 letters off longer words
  (Russian endings defeat exact matching). A record whose `files` changed after `verified_at` is
  flagged, not changed. Content is defanged; the block says records are reference, not orders. A
  memory error is a `debug` line and an empty section, never a failed turn.
- **Storage safety:** a read never creates the file; first setup (WAL switch + migrations) runs under
  `exclusive_storage_lock`. A newer schema or damaged file raises `MemoryStorageError` and is left
  untouched. The old `.kvist/memory/notes.json` is imported once on first open, then deleted with its
  `.bak`.
- `/memory pause` (`memory_paused`) stops agent and skill writes; reading and the person's changes go
  on. `ctx.storage` (SQLite) has `ttl_seconds`, `compare_and_set` and the same privacy gate.
Tests: `kernel/tests/test_memory.py`, `test_memory_consolidation.py`, `test_memory_resilience.py`
(including retrieval-quality guards and a `slow` latency guard); `scripts/memory_bench.py` measures.

### Request flow
`main.py` (REPL, slash commands, pickers) → `role_router.py` classifies the request into **coder /
reader / devops / chat** → `LocalAgent.run()`:
- **chat** takes a lightweight path (`_run_chat`): direct markdown streaming, no JSON protocol. Each
  turn's message opens with runtime facts (`_chat_turn_message`): `system_context.identity_fact` (you
  are Kvist; the language model behind you is a separate thing, named with provider and model), the
  host, and, last before the person's words, the rule that a reply ends where its content ends. The
  same identity fact opens the action roles' `System environment:` (rebuilt every step) and the same
  rule is the last bullet of their `Final answer style:`. **A streamed reply settles into the
  transcript as it arrives:** `terminal_banner.live_markdown` splits each update with
  `_split_settled_markdown` (cut at the last blank line outside an open ``` fence) and hands the
  settled part to `InteractiveScreen.flush_stream`; only the block still being written stays in the
  height-capped stream slot.
- **coder / reader / devops** enter `run_loop` (`agent_runtime.py`) directly. The loop calls the
  action model, parses, executes via `SafeExecutor`, registers feedback, tracks derived
  progress/verification and enforces stop conditions (step budget, stagnation, consecutive errors,
  repeated-action blocking, write-required-on-create-tasks). What a request expects
  (`change`/`report`/`action`) is a keyword guess in `task_intent.py` (`infer_task_kind`); it adds or
  relaxes completion accounting and never blocks a write by itself.

### Step budget (`AGENT_MAX_STEPS`, default 150)
`run_loop` counts against `RunState.step_budget`. Exhausting it asks through the UI's optional
`ask_choice` ("keep going, N more" / "finish with a report") and extends by another `max_steps` on
"continue". The question is an arrow-key widget rendered *inside* the running prompt_toolkit app
(`InteractiveScreen.ask_choice` blocks the agent thread on an event the UI thread sets), so the live
panel keeps updating, unlike `choose_option`/shell HITL which tear the screen down via `suspend()`. A
UI without `ask_choice` (headless, `run_task.py`, no TTY) keeps a hard stop with
`_build_limit_reached_message`; a cancelled question is treated the same.

### Background commands (`background_jobs.py`)
`run_shell` with `"background": true` returns `{job_id, started_in_background}` at once;
`job_output {job_id, wait?}` reads what it printed since the model last saw it (`wait` blocks up to
120 s) and `job_stop {job_id}` ends it. **Output reaches the model only on step boundaries:**
`agent_runtime._collect_job_note` takes `BackgroundJobs.take_note()` before each model call (a
`Background jobs:` section, or a user message in a native conversation). `RunState.job_note` survives a
failed request and is cleared once a call succeeds; the same text is journaled as `job_notice`.
- The foreground pipeline validates and approves it (policy, HITL, sandbox, execution context);
  `TerminalExecutor.run(background=BackgroundLaunch)` hands the wrapped argv to `BackgroundJobs.launch`
  and releases the run lock. The job keeps the child HOME it was built with. Without `timeout` it lives
  up to the profile ceiling (300 s dev); an explicit one ends it as `timed_out`. Limit:
  `MAX_RUNNING_JOBS` = 4.
- **Each sandboxed command has its own network namespace** (bubblewrap `--unshare-net`): a port a job
  opens is unreachable from another command unless both run with `network: true`; the start result says
  so. The reader role is not offered `job_output`/`job_stop`. `process_tree.run_supervised(cancel=Event)`
  stops a job with the same teardown as a timeout.
- **A job outlives a final answer only when the agent can be woken.** `_end_of_turn_resources(answered=)`
  in `run_loop`'s `finally` stops helpers and jobs (`_stop_left_running`) on every ending except a final
  answer with `LocalAgent.wake_listener` set, which only `serve_session_multi(wake=True)`
  (`--frontend stdio-multi --wake`, passed by Ink's `core.ts`: the Ink app and VS Code panel) sets;
  JetBrains, web-local, ACP, the classic REPL and headless runs stop at the answer. A job or helper that
  ends **on its own** (never one stopped by `job_stop`, the person or stop all) calls the listener;
  `_queue_wake` puts `_WAKE` on the goal queue unless the loop is busy, and the loop re-checks
  `agent.wake_due()` after every item. `_run_wake` writes a `wake` event and runs `agent.wake()`: a
  `_run_turn(WAKE_GOAL, _background_role, woken=True)` that takes the background note **before**
  anything can fail (the same news never wakes twice), keeps the previous language and role, takes no
  message number and is journaled as `user` with empty text and `woken`. `wake_due` is false while
  paused, with a checkpoint or in a closed session. With `--wake` the idle loop also writes events
  queued between turns (`_next_item`). `/new`, switching sessions and a provider rebuild
  (`EventSession.replace_agent`) stop what was left running. Start results tell the model which contract
  holds (`_background_note`, `helpers._start_note`). `/resume` does not restore jobs. Tests:
  `test_wake.py`, `test_event_stdio_multi.py`.
- Output is untrusted: `job_output` results are `untrusted: true`, wrapped by `feedback_to_detail`;
  notes wrap each job in `<UNTRUSTED_TOOL_OUTPUT source="background_job:job_N">` after `redact_text` +
  defanging. Repeated `job_output`/`job_stop` calls are exempt from the repeat guard and stagnation
  counting (`_JOB_ACTIONS`) and allowed while planning.
- Events (`frontend_protocol.py`, `docs/UI_API.md`): `job_started`, `job_output` (live-only,
  throttled), `job_finished`, `job_notice`, emitted from the job's thread through `UiJobListener`.
  Tests: `test_background_jobs.py`, `test_background_jobs_loop.py`.

### Waits and schedules (`scheduler.py`)
`wait {seconds, reason}` (1 s..24 h) and `schedule {op: add|list|update|cancel|pause|resume}` wake the
agent by time through the same path as a finished job: `Scheduler` calls `wake_listener`,
`has_background_news` sees `Scheduler.has_due()`, `wake()` runs a turn with `SCHEDULE_WAKE_GOAL`, and
fired occurrences reach the model as a `Scheduled wake-ups that came due` note
(`_collect_schedule_note`, taken at step boundaries, never shown twice). The tools return at once; the
model says its piece and ends the turn.
- A wait lives in memory and ends with the conversation (`clear_waits` on `/new`, a switch, shutdown). A
  schedule is `<workspace>/.kvist/schedules.json` (`durable_json`): one-time (`at`, ISO local time) or
  recurring (`time` HH:MM with `weekdays` or `every_days`; or `every_minutes` ≥ 5). Local time
  throughout; `schedule list` returns the current time.
- **The file is the source of truth, so cores share it.** The timer thread re-reads it every ≤30 s and a
  core *claims* a due entry by advancing `next_run` under the file lock, so Ink and an IDE on one
  workspace never run an occurrence twice. A missed occurrence fires once, flagged late; a recurring
  entry jumps to its next future time.
- **The timer runs only where the agent can be woken** (`--frontend stdio-multi --wake`). Classic REPL
  and headless runs refuse `wait` and save a `schedule` with a note that it fires elsewhere. No daemon:
  a closed Kvist runs nothing. Helpers have neither tool. A woken turn has nobody watching: approvals
  still go through the normal gates. Tests: `test_scheduler.py` (fake clock, timer thread off).

### Image generation (`image_generation.py`)
`generate_image {prompt, path, high_quality?}` draws a picture with AnyModel's image models (OpenAI images
endpoint through `agent.client`) and saves it as a **new** file. A conditional tool like `end_session`: offered only
when `config.provider == "anymodel"`, in coder/devops/universal, never to a helper (`available`; the prompt block is
`prompts/system/generate_image.txt`, added by `with_tool_in_prompt`, and `test_prompt_protocol_sync._CONDITIONAL`
lists it). `cx/gpt-image-2` by default, `cx/gpt-image-2.5` with `high_quality`; `nano-banana` answered 503 when tried,
so it is not the default. The extension comes from the returned bytes (PNG/JPEG/WebP), never from the model's name.
- **Writing:** `SafeExecutor.create_binary_file` (O_EXCL, O_NOFOLLOW, workspace boundary, `.env` guard) never
  overwrites. The change journal stores text, so a picture is **not** in `/revert`. The action is in
  `_WRITE_ACTIONS` (reader and plan mode refuse it) and `_NON_RECONCILABLE_SIDE_EFFECTS` (a paid call is never replayed).
- **Background when the agent can be woken** (`wake_listener` set: Ink, Glass, VS Code), synchronous elsewhere.
  `ImageJobs` (`agent.images`) starts a thread, answers at once (`status: "drawing"`), and on its end leaves a notice
  that `_collect_image_note` folds into the next request (`Images:` section of `job_note`); `has_background_news`
  counts it, so an idle agent is woken. At most `MAX_RUNNING` = 3 at once. `stop_all` (a stop, `/new`, shutdown,
  `_stop_left_running`) drops what is still drawing: the cancel event is captured in `start`, and a reply that lands in
  the same instant as a stop is checked against it (`call_cancellable` only looks at the event on its poll).
- **A turn ended `incomplete` keeps its checkpoint and is never woken** (`wake_due`). A model that polled with
  `wait`/`list_dir` and then marked its answer incomplete got no wake for either; the tool's text says why not to
  (reason, not an order). Do not "fix" it by waking over a checkpoint: that revives a stopped task.
- **Events:** `image_job` (`generating` | `failed`, live-only, in `_TRANSIENT_EVENTS`) and the finished picture as
  an `artifact` event with `skill: "image"` and the workspace path. Glass puts both in the browser's right-hand card:
  a spinner while drawing, then the picture develops (blurred and dark to sharp, `DEVELOP_STEPS` x `DEVELOP_STEP_MS`)
  using `backdrop::developing_thumbnail`. The provider sends no partial frames (`partial_images` produced an empty
  stream), so the reveal is ours and starts when the picture arrives. A live `screen` event takes the card back.
- **Not seen:** the GPT models in the background mode against the real gateway (checked with a local fake server that
  waits 8 s and returns a PNG, and by hand in Glass); how Ink, VS Code and the OpenCode interface show the `artifact`
  and `image_job` events was not checked.
  Tests: `test_image_generation.py`, the protocol and backdrop tests in `frontends/tui/glass`.

### Scratchpad (`sandbox/scratchpad.py`)
A throwaway directory `<tempdir>/kvist-scratch-<random>` (0700, 500 MB) for a quick test script that must leave no
trace. **One per core process**: `SafeExecutor.scratchpad`, handed to helpers' executors (`helpers.py`), so the main
agent and all helpers share it. The name is fixed at construction and the directory made on first use (`ensure`).
Cleared (`clear`) by `LocalAgent.new_session`, `switch_session` and `shutdown`, after `stop_left_running`; **not** by
`reset_session`/`delete_session` (headless ops with no live pad; the active session cannot be deleted).
- **Reach:** the prompt's `Scratchpad:` line (`system_context.scratchpad_context`) gives the absolute path.
  `write_file`/`append_file` take that absolute path (`_scratch_target` → `_write_scratch`: no journal, no overwrite
  approval, so not in `/revert`; `.env` guard and per-call size caps still apply; every component opened with
  `O_NOFOLLOW` relative to the previous one because a sandboxed script can rename things meanwhile; Windows has no
  `dir_fd` and takes `_write_scratch_by_path`, where commands run unsandboxed anyway). `read_file`/`list_dir` see it
  through `_read_boundary_for`. `replace_in_file` does not: rewrite the file.
- **Shell:** Bubblewrap binds the pad read-write at its own path and sets `KVIST_SCRATCH`
  (`wrap_command_for_sandbox(scratch=)`). `TerminalExecutor(scratch=)` lets an absolute path through the
  "absolute paths are forbidden" check only when its real location is inside the pad (`_names_scratch_path`), and
  `_resolve_workspace_reference` accepts a script there; its content hash is keyed by absolute path. Running code
  from it still goes through the normal risk assessment and approval. The cwd stays in the workspace.
- **Limit:** `check_room` applies to the executor's writes; a shell command can still outgrow it (per-file
  `RLIMIT_FSIZE` only), the pad is gone with the session anyway.
- Linux (Bubblewrap) checked end to end with a real script; macOS and Windows have the same code path but are
  not run. Tests: `test_scratchpad.py`.

### Stopping a turn (Ctrl+C / Escape)
**A stop ends the turn, never the session.** `run_loop` catches `KeyboardInterrupt` around its whole
loop; the answer becomes `build_cancellation_message` and the `finally` still seals session,
checkpoint and logs. `main.py`'s `except KeyboardInterrupt` around `agent.run` is the last-resort net
(`run_stopped_text`); a stop before the first step is handled in `run()` (`_report_stop_before_loop`).
Only a stop at the *idle* prompt exits with 130. A stopped chat reply stays in the chat history with a
"[Ответ остановлен пользователем…]" note (`_stopped_chat_reply`) so the transcript alternates.

**A stop is context, not just UI.** Every stop path sets `RunState.stopped_by_user`;
`_add_to_session_history` writes `stopped_by_user: true`, and both history renderers show it so
"продолжай" is understood as unfinished work. A stopped action run also stores `trail`
(`LocalAgent._stopped_run_trail`: paths read with line ranges, queries, paths changed) so the next turn
goes straight back to those files.

**Escape needs two presses; Ctrl+C needs one.** The first Escape arms the stop (`_stop_armed_at`) and
shows `_STOP_HINTS` under the status panel; a second press within `_STOP_CONFIRM_SECONDS` (3 s)
performs it. **Escape pauses, Ctrl+C stops everything:** the confirmed Escape calls
`InteractiveScreen.pause_handler` (`agent.request_pause`, marks `TaskControl`) before raising the same
signal, so the loop ends the turn as `paused` and keeps the task's live resources; Ctrl+C is "stop all":
cancel plus cleanup. Both keep the checkpoint. The hint language follows the request text
(`InteractiveScreen.set_language`), not `LANG`.
Both keys must raise a **real signal**: `InteractiveScreen._interrupt_main_thread` does
`os.kill(os.getpid(), SIGINT)` on POSIX instead of `_thread.interrupt_main`, which cannot free a main
thread parked in a provider socket read (`_preserve_interrupt_signal` keeps `ISIG` on). Escape acts
like Ctrl+C only under `_turn_in_flight` with an empty input box (Escape is also the Alt+Enter meta
prefix). `process_tree.run_supervised` re-raises an interrupt only *after* tearing the subtree down.
`agent.request_cancel()` (`_cancel_event`) is the **GUI's** transport-agnostic mechanism, observed
between steps, while consuming an action-loop stream and in `_consume_chat_stream`; model requests go
through `cancellable_call.py` (daemon threads, the loop polls `_cancel_event` every 50 ms and raises
`RunCancelled`), so a stop lands at once.

**First-run setup in the classic terminal: Esc goes back, Ctrl+C quits.** `TerminalBannerUI` dialogs
(`choose_option`, `prompt_text`, `prompt_secret`) answer `None` to both keys. `main()` runs the first
`build_agent` inside `terminal_banner.ctrl_c_stops_setup()`, where Ctrl+C raises `SetupCancelled` (a
`KeyboardInterrupt` subclass) and `main()` exits 130; prompt_toolkit fields use
`_cancel_on_escape_bindings`. Dialogs opened later keep the cancel meaning. Tests: `test_ui.py`,
`test_cli_pty.py::test_cli_pty_first_run_*`.

### Skills platform (`kernel/plugins/`, `kernel/{resources,task_control,helpers,agent_tools,...}.py`)
Public docs: `docs/SKILLS.md` (+ `.ru.md`); events and control messages: `docs/UI_API.md` §7, §8f.
- **Reading never executes.** `manifest.py` parses `manifest.json` without following symlinks;
  `registry.py` stores immutable `versions/<v>/` with an `active.json` pointer; `grants.py` keys
  approvals by (skill, content digest, workspace), so a changed package starts with no grants. Package
  code runs only in `host.py::RunnerHost`: Bubblewrap with the system `/usr/bin/python3`, a
  digest-verified snapshot, no workspace/HOME/config/display/env, its own network namespace.
  Executable skills are Linux-only by design; never add an unsandboxed fallback.
- **Wire:** length-prefixed JSON over an inherited socketpair (`runner/kvist_wire.py`);
  `services.py::CoreServices.METHODS` is the single table of ctx methods (capability, write flag, scope
  check). A write method goes through the same journaled executor path as the model's writes. Readonly
  tools and reader/plan runs are re-checked at every nested call (`_require_write_allowed`).
- **Model surface:** built-in tools `skills`, `memory`, `sessions`, `helper`, `ask_user`
  (`agent_tools.py`); attached skill tools use wire names `<skill>__<tool>` mapped to
  `ActionType.skill_call` (`INTERNAL_ACTIONS`), MCP tools `mcp__<server>__<tool>`. A new built-in tool
  needs `protocol.py`, parser, `native_tools`, role prompts and `extensions.txt`
  (`test_prompt_protocol_sync.py`).
- **Bundled skills are preinstalled** (`plugins/bundled.py::sync_bundled`, called by `build_agent` and
  before every `--skill-op`; problems become startup warnings). It installs missing ones and upgrades
  ones whose active version has origin `bundled` when the shipped version was never installed (a
  rollback sticks); an uninstall is recorded in `plugins/skill/bundled.json` until an explicit
  `install bundled:<id>`. **Editing a bundled skill means raising its version** (an installed version
  is never reinstalled); `test_a_changed_bundled_skill_has_a_new_version` compares packages with
  `kernel/tests/bundled_skill_digests.json` (update it with the bump). The catalog hides skills whose
  `platforms` exclude this OS.
- **Reference pages:** `SKILL.md` is re-sent with every text-protocol step, so long skills keep detail
  in `references/*.md` (`SkillRegistry.references`), read one at a time with `skills` op `read`
  (attached skills only, same `<SKILL_INSTRUCTIONS>` quoting). `checks._check_references` refuses
  unreadable pages and dead pointers. The bundled `design` skill is built this way and
  `test_design_skill.py` runs the recipes its pages give the model (a recipe the sandbox refuses fails
  silently in a run).
- **Resources:** everything a task starts (runner, Xvfb/i3, dev servers, MCP) is a
  `resources.py::ManagedProcess` owned by a `ResourceManager` (cgroup scope, limits, freeze/thaw, crash
  `recover()` via pid+starttime+env marker). `_end_of_turn_resources` keeps them on a pause and cleans
  them on anything else.
- **Pause vs stop:** `task_control.TaskControl` (shared `_cancel_event`); `request_pause` = Esc,
  `request_cancel` = stop all. A skill call gets `Paused`/`Cancelled` at its next request, then the
  runner is frozen (pause) or ended. An interrupted batch's remaining actions are not run.
- **Helpers** (`helpers.py`): one at a time in a service session `helper-…` (`agent_logging` never makes
  it active or resumable). Messages are `sent -> received -> applied` through `_drain_inbox`. Role: the
  one named, else `_role_for` routes the bare task (a `chat` verdict becomes `reader`). It may write
  unless its role is reader, the main agent may not write (`_main_cannot_write`), or a skill started it
  (`agents.manage` grants read-only helpers only). A writing helper borrows the main executor's gate
  only when it is `answers_from_any_thread` (`EventApprovalGate`), else `DenyApprovalGate`.
  `delegate_task` is not a helper: it starts nobody, and its descriptions say so.
- **The person's desktop** (`host_actions.py`, bundled `desktop` skill, `core_group: "host"`): a **core
  tool** skill run by the core (`SkillManager._call_core` → `CoreServices.handle("host.<tool>")`), no
  package code or runner, so it works on Linux, macOS and Windows. `core_group` is accepted from
  `origin="bundled"` only. The set is closed: open an http(s) address, start an app by name (Linux
  `.desktop` + `gtk-launch <id>.desktop`, macOS `open -a`, Windows Start Menu `.lnk`; `id:<id>`
  disambiguates), list/focus/close windows (Hyprland, Sway, niri, i3, GNOME via Window Calls over
  `gdbus`, KDE `kdotool`, X11 `wmctrl` (never on Wayland), macOS JXA, Windows PowerShell; `_no_backend_hint`
  says what to install). `host.open` is capped at 12 per run and refuses an address `redact_text` would
  change; `close` always asks via `ui.confirm`. **Tabs of the person's browser are not reachable** (never
  probe `:9222`: other apps' CEF debuggers, Spotify's for one, listen there). macOS/Windows/GNOME/KDE/niri backends have
  only faked-command tests; skills do not load on Windows yet (POSIX-only package reader). Tests:
  `test_host_actions.py`, core-tool tests at the end of `test_skill_runtime.py`.
- **Network** (`net_broker.py`): the only way out of a runner or MCP sandbox (task-exposed ports, granted
  domains, no private addresses, redirects re-checked). Preview: `screen.py` reads Xvfb's `-fbdir` and
  serves a loopback MJPEG viewer (token, Host/Origin checks); events `screen`, `task_state` are live-only.
- **Tests:** `test_skill_*.py`, `test_pause.py`, `test_helpers.py`, `test_platform_services.py`,
  `test_mcp.py` run real Bubblewrap and skip without it; `test_browser_skill.py` is `slow` and needs
  `KVIST_TEST_SKILL_CONFIG=<config dir with browser installed>`.

### Ink terminal frontend (`frontends/tui/ink/`, bundled to `dist-ui/kvist-tui.mjs`)
A separate Node 22 / TypeScript / Ink app, **not** part of the Python process. Paths are relative to
`frontends/tui/ink/`. `main.py --frontend tui` (or saved `/tui`, `user_config.get_saved_frontend`) calls
`launch_tui`, which runs `node dist-ui/kvist-tui.mjs` with the exact core command in `KVIST_CORE_CMD`
(JSON argv: the frozen binary, or interpreter plus `kernel/__main__.py`). The app spawns that core as
`--frontend stdio-multi`, speaks only `docs/UI_API.md` events and never reads `.kvist/` (session ops go
through `--session-op`). `launch_tui` returns None, and the classic terminal starts, without a TTY, Node,
a build (`dist-ui/`; the root's `dist/` is PyInstaller's) or a configured provider. Layout: `core.ts`
(adapter), `state.ts` (pure reducer), `editor.ts`, `commands.ts`, `ui/*.tsx`, `ui/skins/kvist/`,
`ui/skins/claude-code/`. Build: `build.mjs` (`npm run build`), not the esbuild CLI, for two memory
fixes tests and `tsx` do not get: it defines `process.env.NODE_ENV` as production (React's dev build
keeps a `performance.measure` entry per render) and caps Ink's `measure-text` cache at 1M characters.

**Two skins, one app.** `--frontend claude` (saved `frontend: "claude"`, in `main.TUI_FRONTENDS`) runs
the same bundle with `--skin claude`; `ui/theme.ts` holds both palettes and `applySkin` overwrites the
shared `color` object once before the first render. Skin-specific structure lives in
`ui/skins/claude-code/claude.tsx` plus a few `currentSkin()` branches in `input.tsx`/`prompts.tsx`/
`markdown.tsx`. `/tui` is a picker over `commands.FRONTENDS`; choosing another Node skin exits with
code 64 and `_switch_frontend` saves and relaunches. Ink layout facts: a bare `Text` beside a wrapping
block shrinks with the flex row, and a wrapping `Text` in a shrinking column is laid out two cells wider
than the room left; glyphs go in `<Box flexShrink={0}>` (`Mark`) and wrapping blocks get an explicit
width (`Rest`).

**The claude skin is fullscreen** (alternate screen, `alternateScreen: true`, root `Box height={rows}`;
no `<Static>`, no scrollback), modeled on Claude Code's fullscreen rendering
(code.claude.com/docs/en/fullscreen). `ClaudeScreen` puts every item in a clipped viewport
(`overflow="hidden"`, `column-reverse` over a growing spacer, so no measurement; measured offsets made
the view jump); the live thought and streaming answer are the transcript's last children; spinner,
suggestions, input and footer are pinned below. The spinner word depends on the running role
(`verbs.spinnerWord`); tokens are the turn's own output; a `⎿  Tip:` line names real Kvist commands
(`ui/skins/claude-code/tips.ts`: **re-read when commands or `get_style_options` change**); live thought
and answer are revealed at a steady pace (`useTypewriter`). File edits follow Claude Code's look
(`claude-diff.tsx`, highlight.js in `highlight.ts` with only the imported languages, colours in
`syntax.ts`). Successful `run_shell` calls and the reads around them fold into one dim line
(`collapse`); a failed command is never folded; Ctrl+O toggles the full output (`expanded`). A live
turn ends with a dim closing line (`done` item, `closeTurn`, `verbs.doneLine`); replayed journals and
commands get none. The footer shows the routed role only while a turn runs. Scrolling is in-app
(PageUp/PageDown, Ctrl+Home/End, wheel; a scrolled view keeps its place, a new request snaps back).
The mouse belongs to the app (`pointer.ts`, SGR 1002+1006): `Pointer` stands between Ink and the
terminal, strips mouse reports from `stdin`, and wraps `stdout` to recognise each frame (exactly
`rows` lines behind an erase prefix) so drag/double/triple-click can be hit-tested, painted inverse
(`screen.ts`) and copied via `clipboard.ts`. Shift+drag reaches the terminal's own selection. It also
paints: it drops Ink's erase prefix and writes only changed rows (flicker otherwise). **The tap
depends on Ink's non-incremental `log-update` format; re-check `splitFrame` when upgrading Ink.** Not
done: click on suggestions/dialog options, cursor placement, click to expand results, transcript mode.
On exit `cli.tsx` prints `farewell.ts` on the real screen (mark, "Kvist agent stopped.", the session to
resume only if `--session-op list` shows it non-empty or checkpointed).

A `command_result` whose `effects.changed` is true (`/new`, `/session`) empties the feed under a fresh
welcome block (`beginSession`, epoch bump); for a switch the app then replays that session's journal
(`sessionHistory` → `restore` with `banner: true`) unless a turn started meanwhile. A closed session
sets `state.closed` to the core's `closed_notice` and `ClosedBar` (`ui/input.tsx`, both skins) replaces
the input (Enter = `sendCommand("new")`); the next `session` event without `closed` clears it.
**The welcome mascot** (`ui/skins/claude-code/mascot.tsx`): 8 rows × 16 columns, six frames played once
at 2 fps, transparent background. Cells are generated by `make_mascot_frames.py` from
`mascot/frames/frame_*.png` into `mascot-frames.ts` (Pillow, numpy; `--rows` sets size), in two sets:
quadrants (2×2 px, every font) and octants (2×4, Unicode 16; the 251 patterns that have a character).
`mascotGlyphs` picks octants when `WT_SESSION` is set, `KVIST_GLYPHS` overrides. On image terminals the
frames go out as real images (`mascot-images.ts`, 256 px palette PNGs): kitty graphics (kitty, Ghostty,
Konsole; from the environment in `kitty.ts`, never under tmux) or Sixel (Windows Terminal 1.22+, WezTerm,
mintty, foot, iTerm2). Sixel cannot be detected from the environment, so `cli.tsx` asks before Ink owns
stdin (`probe.ts`: `CSI 16 t`/`14 t`/`18 t` and `CSI c` last as the barrier; 300 ms max); `Pointer`
decodes the PNG (`png.ts`), scales by nearest neighbour and encodes (`sixel.ts`, P2=1). A picture lives in
the graphics layer and does not move with text, so `ClaudeScreen` re-syncs after every render and the
picture is clipped like the text; a Sixel picture is part of the cells (repaint rows to erase), a kitty
one is deleted by id. iTerm2/WezTerm inline images are not supported.
**One day a year the squirrel is a thief** (`heist.ts`, 19 November local date, or `KVIST_HEIST=1` on any day): a
second frame set `mascot/frames-heist/` (the six frames edited with Codex's image tool: bandit mask, car key) is
turned by the same generator into `mascot-heist-frames.ts` / `mascot-heist-images.ts`
(`make_mascot_frames.py --source mascot/frames-heist --output … --images-output …`; run it with the system Python
3.14, the venv's `unicodedata` has no Unicode 16 octant names). `mascot.tsx` picks the set per call, the welcome
block adds `★★★★★` and a line (EN/RU by `detectLocale`). In the cell modes the key shrinks to a pale blob and only the
mask reads; the key is clear on image terminals. Tests: `tests/heist.test.tsx`. Not seen live in a real terminal.

A `manifest` event (`ManifestEvent`, `core_manifest.build_manifest`) follows `hello` so frontends
populate pickers (roles, effort levels, providers, models, action types, slash commands, api settings,
styles, themes) from the core. Two additive events feed tool cards and the role label: `action_result`
(`action_result.build_action_result`, emitted from `agent_runtime._execute_action` and not from
`register_feedback`, which also runs for blocked actions; redacted, head-and-tail-capped `output`,
`exit_code`, `first_line`) and `route` (the role actually chosen, once per turn from
`agent._choose_role`). Both use optional UI methods probed with `getattr`. A turn ends on the additive
`turn_end` event that `serve_session_multi` writes after every turn (a chat reply has no `final`).

**Slash commands over the protocol.** stdio-multi accepts `{"type":"command","name","args"}` and answers
with `command_result`. Handlers live in `main.STDIO_COMMANDS` and receive an `event_stdio.CommandContext`;
the wizards (`choose_role/effort/style/session/models`) take an optional `ui` (`None` = classic
`TerminalBannerUI`), so every dialog becomes an `input_request` the Ink app renders generically. A
command runs only between turns (`_Gate` refuses with `ok:false`). `session` goes through the agent's own
methods, never `session_store`. The four that rebuild the agent (`provider`, `change-api`,
`del-provider`, `full-reset`) call `build_agent` through the `rebuild` closure with `/dev/null` as stdin
(a secret prompt there would hang). **`resume` is the one command that is not a wizard:** the reader
queues it as `_RESUME` and `serve_session_multi` runs it with `submit_resume`, answering with the turn's
events and `turn_end`, never `command_result`; a `session` event announces the target first because
`agent.resume()` may switch sessions.

**Approvals.** `EventApprovalGate` sends a structured `input_request`: `subject` is `{kind: "command",
command, reason}` or `{kind: "overwrite", path, diff}`, and `remember_pattern` is the argv the user may
save (`None` for an intrinsically DANGEROUS command, so "don't ask again" is never offered). A reply
with `remember: true` appends that pattern to the project's allow rules, best-effort. The gate only
holds the pattern; `TerminalExecutor._approve_or_deny` calls `commit_remembered_rule()` *after* the
command ran (saving earlier changed the policy digest bound to the approval and the spawn was refused).
**Switching interfaces.** `/tui` is a *local* dialog (`local:frontend`, built in `app.tsx` from
`commands.FRONTENDS`, never sent to the core). Picking another writes its name to `KVIST_SWITCH_FILE` and
exits 64; `launch_tui` validates it through `user_config.save_frontend` and returns `None` so the
launcher continues. A new interface needs only a `FRONTENDS` entry and a `_switch_frontend` branch.
**First message suggestions** (`starters.ts`): an empty box offers three requests chosen from what the
folder holds (Kvist's own `.kvist`/`KVIST.md`/`KVIST-CHAT.md` don't count), only while the session has no
request and no typed history. Tests: `npm test` (`tests/*.test.ts[x]` use `tests/fixtures/fake-core.mjs`).

### First-run setup screen (`frontends/tui/ink/setup/`, same bundle, `--setup`)
The launcher runs `node dist-ui/kvist-tui.mjs --setup` instead of the agent when `main.needs_setup` (no
`--provider`, no `AGENT_LLM_PROVIDER`, nothing saved) or whenever `--setup` is given. An explicit
`--frontend terminal` is respected; without Node or the bundle (`quiet=True`) the classic wizard runs.
Plain TypeScript on a raw TTY, no React/Ink: `flow.ts` (pure state machine: input and results in, next
state and one effect out), `scene.ts` (pure `state → ANSI rows + hit boxes`), `run.ts` (terminal:
alternate screen, SGR mouse, bracketed paste, row-diff painting, effects). Tests: `tests/setup.test.ts`.
- **Picture:** `backdrop/kvist-sunset-valley-squirrel-16x9.png`, embedded by `backdrop/make_backdrop.py`
  into `setup/backdrop-data.ts` as a 1280×720 256-colour palette PNG (~0.7 MB base64; the bundle must stay
  one file). Shown whole at `FRAME_FILL` (88%) inside a blurred, darkened enlargement of itself. Two
  modes: **Image** (kitty, Ghostty; not Konsole; `KVIST_SETUP_PICTURE=cells` turns it off) — `picture.ts`
  bakes brightness, the darker middle and the panel's pane into raw RGB sent via `kitty.placeRgb` below
  the cell backgrounds; or **Cells** (everything else) — quadrant glyphs, two colours per cell by best
  two-group split (`backdrop.sampleCells`). Colour levels from `color.detectColorLevel` (truecolor,
  xterm-256, none for `NO_COLOR`/`TERM=linux|dumb`: panel only). Sixel and octants are not used here.
- **Motion is short and skippable:** the picture rises from black (0.3 s), rests until 4 s, dims to
  `DIMMED` (0.55) while the panel fades in over 0.25 s (`run.introFrame`); a slow fade reads as a lagging
  computer (an image frame costs ~70 ms and ~2 MB), so frames in motion are half size. A key or click
  before the panel shows only brings it up. `KVIST_REDUCE_MOTION=1` skips the intro.
- **Demo video** (`setup/video.ts`): on the interface step the highlighted interface's demo plays in the
  preview slot: Claude-style and Classic each have a clip (`ClipId`, `isClipId`), the others keep the text
  preview. The slot is reserved for every interface so the panel keeps its height; the player's key holds the
  clip id, so moving the highlight restarts it. A 60 fps recording does not compress without a real codec, so
  a clip is an H.264 720p file (`demo/claude-style.mp4` ~3.6 MB, `demo/classic-style.mp4` ~3.5 MB at 30 fps,
  made by `demo/make_demo.py` from `docs/media/Claude-Code-demo-kvist.mp4` and
  `docs/media/Classic-demo-kvist.mp4`; Classic's window is a little wider than 16:9, so the script crops the
  sides from the middle). Classic's recording was made on a private Xvfb with `KVIST_CONFIG_DIR` pointing at a
  copy of `config.json` only: the person's own memory (`<config dir>/memory.db`) otherwise greets by name and
  answers in their language. `build.mjs` copies the clips beside the bundle (also in `kvist.spec` and the
  wheel's package data) and the
  **user's own ffmpeg** decodes. Plays only where an image terminal reported its cell size, `ffmpeg` is on
  PATH (or `KVIST_FFMPEG`) and the clip is found; otherwise the text preview stays. `KVIST_SETUP_VIDEO=off`
  disables it; reduced motion shows one poster frame. Frames are deflated on the thread pool and drawn at
  `z=-1`; `FramePacer` sends 60 fps until a frame averages > 14 ms, then holds 30 (never back up).
- **Flow:** welcome → provider (curated: AnyModel, ChatGPT, OpenAI, Anthropic, DeepSeek, OpenRouter,
  FreeFlow; "More providers" exits 65 to the classic wizard) → key (masked; AnyModel's empty Enter opens
  registration) → model (only for providers without defaults, `list-models` catalog) → interface
  (Claude-style first) → import (only when the scan found sessions). Operations are `--config-op get-config | list-models | set-provider |
  open-registration | list-imports | import-sessions` with the payload on stdin; a failed `set-provider` returns to the screen it came from.
  **Import step:** a successful `connected` fires `scanImports` (`wizard.setup_listing`: agents with sessions that ran
  in the chosen folder, none means no step); choosing an interface then opens `import` (tick boxes, all ticked,
  Skip / `S`, Esc back) instead of finishing, and `import-sessions` (`wizard.setup_import`, no `LocalAgent`: a stand-in host
  with `executor.workspace`) copies the ticked agents' sessions before the chosen interface starts. A failed import
  stays on the step with the reason. Each agent has a two-letter monogram in its own colour (`setup/agents.ts`; the same
  letters are drawn in `branding/agents/*.svg`): Kvist's own marks, never the agents' logos (trademarks).
  Esc goes back; Esc on the first screen and Ctrl+C quit with nothing saved.
- **Exit protocol:** 64 = chosen interface is in `KVIST_SWITCH_FILE` (launcher saves it via
  `_switch_frontend`, `--setup` dropped); 65 = continue in the classic terminal, nothing saved; 66 = a
  provider-only setup saved a provider; 0 = quit.
- **Who opens it:** every terminal-hosted entry without a first-run of its own: classic terminal, Ink
  interfaces, the bare core (`main()` → `needs_setup` → `launch_tui`), and `kvist --acp --login` through
  `_acp_login` with `--setup-provider-only` (no folder, no interface step; falls back to `run_login`'s
  prompts without Node). VS Code, JetBrains and local web have their own first-run screens; the Electron
  GUI and stdio frontends have no terminal.
- **Installer screen** (`curl … | bash`, `scripts/release/install.sh`): the same window with two extra
  steps, `components` (tick boxes) and `installing` (progress). The screen installs nothing itself:
  `install.sh --plan` prints JSON facts per component (`id`, `available`, `targets`), and
  `install.sh --only <ids> --events` installs and emits one JSON object per line (`step`, `warning`,
  `error`, `component start|done`, `done`) (`ops.readPlan`/`runInstall`). Components: `cli` (cannot be
  unticked), `node` (pinned version and SHA-256 per OS, never unpacked from an unchecked download),
  `ffmpeg` (FFmpeg 6.0 static builds from github.com/eugeneware/ffmpeg-static tag `b6.0`, pinned by a
  SHA-256 recorded when chosen, unpacked to `~/.local/share/kvist/ffmpeg`; failure is a warning), `jetbrains`
  and `vscode` (dimmed when no IDE is found), `path` (one line appended once to the login shell's startup
  file), and **build tools** (`BUILD_TOOLS` in `flow.ts`: `python`, `libpython`, `binutils`; the system
  packages `install_cli.py` checks for, ticked in the screen, never in the script's `--only` default).
  Node.js and ffmpeg have mirrors (`NODE_MIRRORS`, `FFMPEG_MIRRORS`); `rank_mirrors` probes all at once
  (8 s max) and `download_verified` takes them fastest first, keeping a file only with the pinned SHA-256,
  giving up under 5 kB/s for 30 s. `--plan` reports `privilege` (`root|cached|password|none`) and the exact
  package-manager commands; with `password` the screen hands the terminal over for `sudo -v`
  (`run.suspendTerminal`), with `none` the script warns with the whole command sequence and marks the item
  `failed`. `video.findFfmpeg` looks after `KVIST_FFMPEG`, PATH, then the install dir, and again when an
  installation finishes. **Bootstrap:** with a terminal (`/dev/tty` opens) and none of
  `--only`/`--from`/`--events`/`KVIST_INSTALL_PLAIN`, the script downloads `kvist-<os>-<arch>` and the pinned
  Node into its temp folder, then runs `kvist-core --installer` with `KVIST_INSTALL_SCRIPT`,
  `KVIST_INSTALL_CACHE`, `KVIST_NODE_HOME`, `KVIST_NODE` (`main.find_node`: that, then `node/bin/node` beside
  the frozen binary, then PATH). `--installer` implies the setup screen, which starts on `components` when
  `KVIST_INSTALL_SCRIPT` is set. Windows (`install.ps1`) has no screen yet. `KVIST_RELEASE_URL` points the
  download at a mirror. Release binaries carry the bundle (`plugin-release.yml` builds `frontends/tui/ink`
  before PyInstaller). Tests: `scripts/tests/test_release_install_sh.py`, `kernel/tests/test_frontends.py`,
  `tests/setup.test.ts`. Users install a prebuilt release; `install_cli.py` is the developer path.
- **A wide folder is asked about once** (`flow.widthWarning`, `folders.breadthOf`): "Get started" with the
  home folder, a disk top, the folder of all homes or Desktop/Documents/Downloads opens the `broad` step
  (choose a folder / use it anyway, remembered in `broadAccepted` / back). The first screen says in one line
  what Kvist does not do (no `.env` or SSH keys, asks before risky commands, `/revert`).
- **Input behavior:** the key field accepts a paste with quotes or `Bearer `, Tab shows/hides it, a known
  prefix mismatch is a quiet warning, never a block; Esc cancels a connection in progress; the mouse is
  read in any-motion mode (1003): a button lights when pointed at, a press shows it sunk, the action runs on
  release over the same place (held ≥ 90 ms), the pointer becomes a hand via OSC 22 (kitty, Ghostty).
- **No colour keeps state readable** (`scene.monochrome`): the primary button is inverse video, the
  highlighted list row is inverse starting with `>`, a pointed-at element is underlined, held also bold, a
  toggle (tone `toggled`) is only bold. Coloured levels draw no inverse video.
- **Working folder chooser** (`setup/folders.ts` state machine, `setup/listing.ts`, scene `buildFolder`):
  rows are "use this folder", the parent and subfolders (files only counted; `.git` marked); clickable
  trail, places, New folder (Ctrl+N), Hidden (Ctrl+D), typing filters. Icons are Nerd Font glyphs where the
  terminal ships them (`KVIST_ICONS=nerd|plain`). The chosen folder travels on the second line of
  `KVIST_SWITCH_FILE` (`_use_setup_folder`) and becomes `--workspace`. Paths follow their own flavour
  (`folders.flavour`: `C:\\…` and UNC are Windows); on Windows a `DRIVES` level sits above each root
  (`listing.listDrives`). Terminal.app's old `ESC [ M` mouse reports are parsed too. Linux is exercised
  for real; macOS and Windows are not.
- Copy is bilingual (`setup/strings.ts`, locale from `KVIST_LANG`/`LC_ALL`/`LANG`).

### OpenCode-style terminal (`frontends/tui/opencode/`, TypeScript: OpenTUI + SolidJS on Bun)
OpenCode's *interface*, **copied from its MIT source** (`packages/tui`, `theme`, `plugin`) into `src/oc/` with the
same relative paths and cut down to what Kvist has: none of its server, SDK, sessions, providers, agent loop,
plugins or workspaces. The user's rule: the look must be OpenCode's own because the code is its own, not a lookalike;
attribution lives in `THIRD_PARTY.md` and the README. Docs: `README.md` / `.ru.md`. Where OpenCode has what Kvist
lacks, the nearest Kvist feature stands in: agents → roles (Shift+Tab), model variants → reasoning effort (Ctrl+T),
todo/MCP → background jobs/skills, `/export` → the core's import, permission prompt → the core's approvals. Share,
compaction, workspaces, terminal panes and plugins are absent. `/tui` lists it (`commands.FRONTENDS` in Ink), the saved
value is `opencode`, `main.launch_opencode` runs `bun src/main.ts` with the same arguments, `KVIST_CORE_CMD`,
`KVIST_SWITCH_FILE` (exit 64 switches) and `KVIST_COMMAND_FRONTEND=ink` as the Ink launcher; **no Bun, no
`node_modules/@opentui`, no `main.ts` (release binaries do not ship it) or no TTY falls back to Ink with the reason**.
No build step: `src/main.ts` imports `@opentui/solid/preload` (Solid's JSX transform is a Bun plugin) and only then
`cli.tsx`, so it starts from any cwd; `bunfig.toml` does the same for `bun test` and `bun run`.
- **Vendoring.** `tsconfig` `paths` alias OpenCode's package names (`@opencode/theme/tui`, `@opencode/plugin/tui/context`,
  `@opencode/client`) to files in `src/oc/`, and `package.json` `imports` carries `#string-width`; the compiler options
  are upstream's (`noUncheckedIndexedAccess: false`, lib ESNext+DOM, no `baseUrl` under TypeScript 7). Edits to a copied
  file are only cuts or Kvist data (the wordmark in `oc/tui/logo.ts`, `oc/tui/config/index.tsx` and `oc/client.ts` are
  small stand-ins for OpenCode's config and SDK types, `message-parts.tsx` has its plugin references removed). **When
  updating from upstream, re-copy and re-apply those cuts; do not restyle a copied file.** Upstream's themes
  (`oc/tui/theme/assets`, v1 migrated to v2 by `migrateV1`), key-binding layer (`Keymap.Provider/createLayer`, leader
  `ctrl+x`, default ids like `session.new`, `agent.cycle`, `command.palette.show`), `DialogProvider/DialogSelect/
  DialogPrompt/DialogConfirm`, `Toast` (**`<Toast/>` must be rendered**), `PatchDiff`, `SessionQuestion` (the approval
  prompt), `InlineToolRow/ReasoningPart/TextPart`, spinner, logo components and `Selection` (copy on mouse release) are
  used as they are.
- **The core side is Ink's.** `src/kvist.tsx` is the only file that talks to the core, and it imports Ink's `state.ts`
  (reducer), `core.ts`, `session.ts`, `format.ts`, `diff.ts`, `approval.ts`, `commands.ts`, `ui/logo.ts`: a change there is
  checked from both sides, and the package must keep typechecking and testing **without Ink's `node_modules`**. The
  reducer's `State` is one Solid signal; events that arrive together are folded in one `batch`. Never `{ equals: false }`
  on it: every effect on it then fires on every event (a `setTerminalTitle` call did, and crashed the native renderer).
- **Screens** (`src/screens/`, Kvist's own, laid out as upstream's): `home`, `session` (scrollbox, docked sidebar from
  120 columns, overlay below), `prompt` (upstream's render tree; history, `/` and `@` lists are ours), `transcript`
  (reducer items → OpenCode's rows: `tools.ts` words every action, `blockOf` says which get a block — run_shell,
  write_file, append_file, replace_in_file; an edit's patch is made by `editPatch`; reads/searches arrive as the reducer's
  `explored` labels, drawn as OpenCode's "Explored: N reads"), `approval` (SessionQuestion: once / always / reject,
  `ARM_MS` guard, `Allow always` only when the core sent `remember_pattern`), `palette`, `dialogs` (sessions, roles,
  effort, models via the core's `catalog` + fast/smart/both → `models PROVIDER FAST SMART`, rename, status, help),
  `footers`, `sidebar`. `src/commands.tsx` lists the commands under OpenCode's ids and slash names plus `kvist.<name>`
  for what only the core has. Route: home until an item other than the banner or a note exists. Notes (a command's result like "Role set to: coder", warnings, skill/helper notices) are toasts everywhere and never transcript rows (`visibleItems`); only an error note stays on the page (and is a toast on Home).
- **Home logo** (`oc/tui/component/logo.tsx`): the wordmark Kvist prints on exit, i.e. Ink's `logoRows` (`ink/ui/logo.ts`, 48×6 cells large / 24×3 compact) in `LOGO_COLORS`, imported so the two never drift; OpenCode's own `go` mark only on a tiny window. The user rejected, in order: a smooth picture (clashes with OpenCode's pixel design; kitty graphics also drops alpha and left a patch on the terminal's background image), then a down-scaled pixel-art copy of `branding/logo.png` (unreadable). Keep it the crisp exit mark.
- **Themes:** `oc/tui/theme/discovery.ts` skips any `system.json`. The core ships `kernel/themes/system.json`, and a custom theme named like the generated "system" made an endless reactive loop (GBs of memory, frozen, deaf to SIGHUP).
- **Arrows** (`screens/prompt.tsx`): `prompt.history.previous/next` are a keymap layer (priority 1, target the textarea, off while the `/`/`@` list is open) copied in logic from OpenCode's own prompt: inside the text they move lines; at the edge the first press goes to the start/end, the next walks `kvist.history()` (recalled text puts the cursor at 0 for Up, at the end for Down). A handler in the textarea's `onKeyDown` never sees arrows (the managed textarea layer takes them first). Under an empty box on Home, with no typed history and no request, the Ink interface's `starterSet` (`ink/starters.ts`) offers three first requests that the same two commands choose among; `kvist.historyRead()` makes them wait for the history so they do not flash.
- **A closed session** (`end_session`): `state.closed` (Ink's reducer) puts `screens/closed.tsx` in the prompt's place (the same slot as an approval, `approvalView` in `app.tsx`): laid out as Kvist Glass's `draw_closed`: one row in the prompt's frame, `×` and the core's `closed_notice` (its trailing "· Enter — new session" cut, `noticeWords`) on the left, `enter` and a clickable "New session"/"Новая сессия" button on the right (language from the notice); no editor, Enter or a click runs `/new` (the core's `session` event without `closed` brings the prompt back). The Ink fixture's `insult` goal drives it. **What a running turn shows, for every role:** `LiveRows` draws the in-flight calls, a live thought, the streamed words (the core streams an action role's `user_message` words through `live_markdown` too, not only chat) and, while none of those is there, a spinner with the core's `status` line (`Writing write_file → path · N characters · edit · step 3`). The e2e FakeProvider does not stream, so streamed words of an action role were not seen against the real core.
- **Core questions are dialogs.** An `input_request` without `subject` opens a vendored DialogSelect/DialogPrompt/
  DialogConfirm (`showRequest` in `app.tsx`; Esc answers `null`/`false`, guarded against answering twice); with a
  `subject` it replaces the prompt.
- **OpenTUI facts that cost time.** A global key handler runs **before** the focused renderable and `preventDefault()`
  stops delivery to it; the managed textarea layer in the keymap handles Enter, so the `/`/`@` list navigates through a
  keymap layer pushed in mode `"autocomplete"` (`prompt.autocomplete.*`). Inside a non-global mode `useCommands()` shrinks:
  the slash candidates come from the static `commands` prop, never from it (reading it there flip-flops forever and hangs
  the app). Dialog content renders inside `DialogProvider`, so providers it reads (`KvistProvider`) must wrap it.
  `Show` with a function child needs a typed parameter. `mockInput.pressKey` takes `"ESCAPE"`, `"TAB"`, `"ARROW_DOWN"`
  (tests map lowercase names), `typeText` is async. `bun test` shows `solid-js/dist/server.js` in stacks: the plugin
  serves `solid.js` under that path. Theme files may name other slots and use `#rgb`; the kernel's `system.json`
  (`ansicyan` refs) is not loadable by upstream's pipeline.
- **Tests:** `tests/pure.test.ts` (modules without a screen) and `tests/app.test.tsx` (the whole app on `testRender` against
  the Ink suite's scripted core; `SHOW_FRAMES=1` prints frames). They see characters, not colours: check the look in
  kitty on a private Xvfb (`import -window <id>`, `LIBGL_ALWAYS_SOFTWARE=1`), with the real core and
  `scripts/e2e_tools_smoke.py`'s `FakeProvider` for real events. CI: the `opencode` job in `platforms.yml` (Linux only).
- **Open work:** the first-run setup screen's interface step offers it always (no Bun detection there; the launcher
  falls back to Ink with the reason when Bun is missing); `install_cli.build_opencode` compiles it (`build.ts`, `bun build --compile`, ~110 MB) to `dist/kvist-opencode` and `kvist.spec` packs it, but the release CI does not build it yet (`main._opencode_command` prefers the compiled program, else Bun on the sources); OpenCode's session tabs bar is not drawn; seen in kitty against the real core (also
  through `--frontend opencode`): home, transcript, write/edit/shell blocks, approvals with a diff, palette, models,
  sessions, themes, light mode, Esc Esc arming. Not seen: mouse selection copy, a real streamed answer, macOS and Windows.

### Kvist Glass (`frontends/tui/glass/`, Rust: ratatui + crossterm)
User-facing name: **Classic** (pickers, notices, READMEs); ids, paths and `--frontend glass` keep "glass".
"Classic Legacy" is the prompt_toolkit terminal. A terminal interface drawn over a photograph with the
kitty graphics protocol; docs in its `README.md` / `README.ru.md`. It refuses a terminal without kitty
graphics before taking the screen (`kvist-glass --check`, exit 3; tmux/screen/Zellij refused) and runs the
core as `--frontend stdio-multi --wake` (`core.rs`; `KVIST_CORE_CMD` or the checkout's `.venv`).
Modules: `probe`, `kitty` (upload once, then place/move by id; z-layers below cell backgrounds),
`backdrop` (photo fit, blur levels, glass cards, worker thread), `pictures`, `protocol` (core JSON →
`model::CoreEvent`), `model`, `dialog` (every `input_request`; approvals with `subject` and Always via
`remember_pattern`), `models` (Ctrl+P picker), `edit`, `markdown` + `highlight` (pulldown-cmark,
syntect/two-face), `slash` (a copy of the core-routed names in `frontends/tui/ink/commands.ts` plus `tui`),
`screen` (browser preview, loopback only), `view`, `app`, `anim` (`KVIST_GLASS_FPS`, default 60), `water`
(text selection is "pouring water on the glass", a kitty z-layer under text). Relies on core features in
`docs/UI_API.md`: `action_result.lines_added/lines_removed`, the live-only `helper_activity` event, the
`catalog` command and `models PROVIDER FAST SMART`.
Launcher: `--frontend glass` / saved `glass` → `main.launch_glass` (`KVIST_GLASS`, PATH, then
`_GLASS_BUILD`, also where `kvist.spec` packs the build); `--check` must see the real tty; any refusal
falls back to the Ink UI. `/tui` inside Glass is local (`local:frontend`, exit 64 + `KVIST_SWITCH_FILE`).
Installer: `install_rust.py` (C compiler + rustup; not on Windows), `install_cli.py::build_glass`; release CI
builds it for Linux/macOS. Windows is unsupported by design. **Math:** `math.rs` cuts display formulas (`\[ \]`, `$$ $$`) out of the
answer; `markdown::render_with_math` reserves rows for each, RaTeX (MIT) typesets it, `Pictures::show_formulas`
draws it over those rows (z between the cards and the dialog, cropped by `hidden`/`shown` when scrolled); inline
formulas and ones RaTeX cannot read are text. The other interfaces use the text converter only
(`ink/math.ts`, `classic/math_text.py`, ports of `math::tex_to_text`: **a fix in one is made in all three**).
The Voice screen has no transcript: `voice/screen/src/math.rs` (a copy of the text half, minus as U+2212, fraction as "разделить на") feeds `speakable`, and `voice/speech_ru.py` says the symbols. Licences of what the binary embeds (RaTeX MIT, KaTeX fonts OFL 1.1) are in `frontends/tui/glass/THIRD_PARTY.md`;
ship that file with Glass binaries. Verify visually on a private `Xvfb :97` running
kitty (`import -window <id>`), never by screenshotting the user's desktop; live core runs need a copy of the
config (`KVIST_CONFIG_DIR`) and a fresh scratch workspace (a reused one remembers denied approvals).

**Open work (as of Oct 18 2026):**
- The first-run setup screen (`setup/flow.ts` `INTERFACES`) does not offer Glass; offer it only where the
  setup screen detected kitty graphics.
- Session switch replays the journal (`Core::history` → `App::show_history`, newest 500 events); unit-tested,
  **not yet seen in a live kitty**.
- Not verified live: browser preview, Esc Esc stop, mouse clicks on preview and helper rows, an overwrite
  approval diff, Ghostty (only kitty 0.49 was run).
- The launcher's `--role`, `--approval-mode`, `--resume`, `--provider` and model overrides are not passed to
  Glass; `/resume` is not handled. Model picker: the provider header scrolls away above a deep selection.
- `install_rust.py` prints the sudo command when a password is needed (could hand the terminal over like the
  release setup screen). Release: the first CI run with the "Build Kvist Glass" step has not happened.
- Pre-existing failures: `test_chat_reasoning_stream` (fake agent lacks `_context_ledger`) and
  `test_pyproject_modules` (`kernel.skills_bundled.design.references` missing from `pyproject.toml`).

### Voice (`frontends/voice/`: the screen, the ears, the voice)
A separate frontend. **Do not put voice code into `frontends/tui/glass`.** The screen is its own Rust crate,
`frontends/voice/screen/` (`kvist-voice-screen`), sharing no source with Glass. `anim backdrop core dialog
diff highlight kitty layout probe slash strings term text theme` began as copies of Glass's files and are
maintained separately (a fix in one is made in the other by hand); its own files are `orb.rs`, `voice.rs`,
`voice_overlay.rs`, `voice_strings.rs`, `icons.rs`. `frontends/voice/kvist-voice` starts screen, ears and
voice with the right environment. Launcher: `--frontend voice` / saved `voice` / `/tui voice` →
`main.launch_voice` (`KVIST_VOICE_SCREEN`, PATH, then `_VOICE_SCREEN_BUILD`; `--check` first; starts
`kvist-voice` when it ships beside the screen; any refusal falls back to Ink). `install_cli.py::build_voice_screen`
builds it, `kvist.spec` packs the binary and the ears'/voice's Python files (never models or their envs).

**The screen:** an orb drawn per pixel into an RGBA picture (30 fps max, `ORB_FPS`) over the photograph; under
it a frosted card (`backdrop::blur` + `card`) with the state's Codicons icon and `text::shimmer` label, the
words, a dim line with what the room sounded like (the ears' `heard` event), a command's result and problems;
a footer with provider, model, session, keys and the context meter. The window title is `Kvist` until the
core's `session_title`, then `Kvist | <title>`. `Voice` is pure state: `on_core`/`on_ears`/`on_voice` take
events and put `Out` messages in an outbox the run loop delivers (unit-testable without a process). The core
runs with `AGENT_VOICE_MODE=1`; the two helpers are optional commands `KVIST_VOICE_STT_CMD` /
`KVIST_VOICE_TTS_CMD` (JSON arrays, JSON lines both ways, protocol in `frontends/voice/README.md`). State
priority (`Voice::resolve`): offline, error, the person's own voice, a question waiting, the assistant
speaking, the turn, loading, waiting. The shown look eases toward the state's `Look`; clouds sway around each
state's own hue (`drift`), never round the wheel (amber would turn green); the halo reaches zero before the
texture's edge. The voice mutes the ears while it talks, the person's voice cuts the voice off; without a
voice process the orb "speaks" for `speech_estimate`. A `message` event is spoken only when its `source` is
`reply`. `Voice::on_ask` takes every `input_request` as `dialog::Ask` (read aloud, answered by "да"/"нет"), so
a command's wizard needs no screen of its own: `Tab` opens `voice_overlay::MENU`, each item sends
`{"type":"command","name":...}`. A dialog belonging to a command (`pending`) is not an approval: no gold orb,
not read aloud, and speech is never a goal while a dialog or the menu is open. The screen opens a fresh
session at `hello` (`new` command, silent; `--continue` skips it). `--demo` walks through every state,
`--orb-preview FILE` writes a PNG sheet. Verify on a private Xvfb with kitty and stand-in core/ears/voice
scripts (no tokens, no microphone).

**Conversation:** the ears end a phrase after a pause that grows with the speech
(`endpointing.silence_to_end`), take a further phrase for 6 s after a delivered command, and the screen opens
a window without the wake word by `{"type":"converse"}` once the assistant has finished speaking
(`Voice::open_conversation`: only after the voice has begun) or asked a question; stray words, thinking
sounds and Whisper's silence-subtitles are filtered (`is_real_phrase`).
**The ears** (`app.py`): CTranslate2 needs libcublas.so.12, which a CUDA 13 system lacks, so
`cuda_libs.preload()` loads pip's copies by path before faster-whisper is imported; every model is tried on a
second of silence (`LOAD_CHAIN`); the large model never runs on the CPU and is not loaded when `nvidia-smi`
shows too little free VRAM (the small model then recognizes commands too); `trim_memory()` (malloc_trim)
returns RAM after loading. The gate asks Whisper twice (no hint, then with the language's hint) because one
pass loses a short first word 17-22% of the time; language is chosen among `WHISPER_LANGUAGES` (ru,en) and the
main one stays unless another wins by a factor of three. An exception on one phrase is logged, never fatal.
**The voice** (`speak.py`, engines `silero_tts.py`, `moss_nano.py`): Silero v5 (speaker baya) silently drops
digits, symbols and Latin words, so `speech_ru.py` writes them out before synthesis. MOSS-TTS v1.5 (8B) does
not fit an 8 GB GPU; MOSS-TTS-Nano (CPU) has no Russian built-in voice and runs ~1.2-1.5x slower
than real time. Another engine replaces an engine module.

### IDE chat panel (`frontends/ide/vscode/webview/`, shared with `frontends/ide/jetbrains/`)
One Preact panel serves both IDEs. VS Code hosts it in a webview view (`src/chatView.ts`); JetBrains hosts the
same bundle in a JCEF tool window (its Gradle build runs the extension's `npm run build` and packs
`out/webview.{js,css}`, never a copy of the sources). `webview/host.ts` is the only file that knows the host
(`acquireVsCodeApi` vs the `window.kvistHost` bridge on a `JBCefJSQuery`). `KvistChatHost.kt` is a port of
`chatView.ts` and `KvistCoreProcess.kt` of the Ink app's `core.ts`, so **a new `ToHost`/`ToWebview` message in
`frontends/ide/vscode/src/messages.ts` needs a handler on both sides.** Without JCEF the tool window falls
back to the terminal launcher.

### macOS branches (best-effort; nothing here is verified on a Mac)
A binary per platform is bundled: `bin/win/kvist.exe`, `bin/linux/kvist.bin`, `bin/mac-arm64/kvist.bin`,
`bin/mac-x64/kvist.bin`. Both plugins pick one by OS and CPU (`KvistBinaryResolver.kt`,
`frontends/ide/vscode/src/platform.ts`; Rosetta reports x64), treat Linux and macOS alike as POSIX (shell
launch, `chmod +x`, on a Mac `xattr -d com.apple.quarantine`), and build with a missing other-platform binary
as a warning only. BSD `install` has no `-D`, so Gradle's `hostBinaryPath` and the two `build-plugin.sh`
`mkdir -p` first. `scripts/release/install.sh` maps `uname` to `kvist-linux-x64`/`kvist-linux-arm64`/
`kvist-macos-arm64`/`kvist-macos-x64` (the arm64 Linux binary is built on `ubuntu-22.04-arm` in
`plugin-release.yml`, not run on a real ARM machine, glibc only so not Termux itself; the IDE plugins still bundle x64 only), nests the JetBrains plugin under `~/Library/Application Support/JetBrains/<Product><version>`,
finds VS Code inside its `.app`, and **must stay valid bash 3.2**. `install_cli.py` prints the PATH command
(`path_hint`) and drops the quarantine mark; `install_gui.py` copies `Kvist.app` into `~/Applications` with
`symlinks=True`; electron-builder's `mac` target makes dmg and zip. `plugin-release.yml` builds the macOS
binaries on every run (the repo is public, so minutes are free); only a failed macOS build falls back to the
rolling release's previous binaries. `platforms.yml` covers the macOS layout on its mac runner only. Not covered: Intel Macs,
Android Studio, notarization, a real IDE run.

### Tray icon (`tray_link.py`, `tray_bar.py`, `frontends/tray/`)
One resident Electron process shows every terminal-hosted core's state (amber while a turn runs, green when
one finished unseen) as a module **inside the user's status bar** (a Wayland bar sits on a layer above
windows, so nothing can be drawn over it). `tray_bar.install()` runs at every terminal start (`main()`) and
adds `custom/kvist` to Waybar or `[module/kvist]` to Polybar after the first left entry. **It must stay
idempotent:** a config already naming the module is never edited again (file lock, first version kept as
`<file>.kvist-backup`); `uninstall` removes exactly what was added (`--tray-op disable`). A click runs
`--tray-op toggle`; the panel unfolds from the icon, placed against the bar under the pointer. Bars without an
adapter get a system tray icon, never both (`status.bars`). The panel is a **read-only** mini chat
(`--session-op history`; two processes must not share one live session) and its button raises the terminal
window by walking the core's process ancestors (`src/focus-window.js`). Cores report over loopback TCP with
the token from `~/.kvist/tray.json` (`docs/UI_API.md` §8e); `LocalAgent.run`/`resume` call `TrayLink` only when
`KVIST_TRAY_HOST=1` (set by `main()` for terminal frontends, inherited by the Ink core). **At most one daemon**
(single-instance lock, once-per-process spawn flag, exit 30 s after the last core leaves). **Tests must never
start the real daemon:** `conftest.py` sets `KVIST_TRAY_CMD` to `true` and clears the host marker (this once
spawned ~50 Electron processes). On by default; `tray_enabled: false` in `config.json` turns it off. Verified
on i3 + Polybar; Waybar/Hyprland are test-covered only. Docs: `frontends/tray/README.md` (+ `.ru.md`). Tests:
`test_tray_link.py`, `test_tray_bar.py`, `npm test` in `frontends/tray`.

### Zed / ACP frontend (`acp_frontend.py`, `acp_events.py`, `frontends/zed/`)
Zed reaches agents over the **Agent Client Protocol**, so this is an external agent registration, not an
extension (no `extension.toml`; Zed deprecated extension-provided agents in v1.5.0). `kvist --acp` speaks
JSON-RPC 2.0 on stdin/stdout as an ACP peer (`agent-client-protocol` SDK); it is **not** an NDJSON event
stream. The workspace arrives with `session/new` (`--workspace` unused); a session id is `k1_` + a hash of
`cwd` + session name. `--login` (valid only with `--acp`) configures credentials in the launching terminal
and exits; **no secret ever travels over the ACP connection** (ACP forbids eliciting sensitive data in form
mode), so a missing key is reported with the command to run. It reuses `LocalAgent` / `EventSession` /
`FrontendChannel` / `EventStreamUI` / `EventApprovalGate`, one runtime per ACP session.
`acp_events.AcpEventTranslator` reconciles Kvist's cumulative snapshots (`stream_chunk`, `reasoning_chunk`,
`action_output`) against ACP's append-only deltas by keeping the last snapshot per `stream_id`/`call_id` and
emitting only the new suffix (a snapshot that no longer extends the previous one is emitted whole).
Capabilities are declared only where the path works end to end: **no NES, no MCP, no image or audio prompts.**
**Never design the ACP/Zed surface from memory:** re-check the official docs first
(`frontends/zed/README.md` carries the source list). Locked facts: Zed's `agent_servers` entry is
`{"type": "custom", "command", "args", "env"}` in `~/.config/zed/settings.json` (macOS/Linux without a custom
`XDG_CONFIG_HOME`); the Windows location is undocumented, so `install_zed.py` discovers and refuses to
invent one. `edit_predictions.provider` accepts only `zed | copilot | mercury | codestral | ollama |
open_ai_compatible_api | none`, so inline completion cannot be supplied and must never be claimed.
Divergences from the VS Code/JetBrains feature set are recorded in `frontends/zed/README.md` / `.ru.md`; do
not call the integration a full copy while anything on the "impossible" list stands.
Zed runs the agent as `$SHELL -c '<cmd>'` without `--no-config`, so a printing shell startup breaks the
stream (mitigated with `env.XDG_CONFIG_HOME`).
`scripts/installer/install_zed.py` writes exactly one `agent_servers` member via
`scripts/installer/jsonc_settings.py` (character-range surgery on JSONC; comments, key order and line endings
survive) and never builds or copies the binary (`install_cli.py`'s job; a missing `kvist-core` is a loud
refusal). It is registered in `install.py` with `after=("cli",)` to avoid a parallel `--all` race.

### Voice mode and the narrator (`voice_mode.txt`, `narrator.py`)
`AgentConfig.voice_mode` (`AGENT_VOICE_MODE=1`; a voice frontend sets it, nothing else) appends
`kernel/prompts/system/voice_mode.txt` to the action roles (and `build_report_system_prompt` for the
reporter); the chat role is left alone. The text describes the listener's situation (no scrollback, no
skimming) instead of listing rules: the answer is the news, in one breath, and an action that leaves the room
(a message to a client, a payment) is said out and confirmed first. Keep it principles.
The **narrator** speaks while the *strong* model works. `narrating(agent, model)` wraps the action-model call
in both paths (`call_model` for `usage_kind == "step"`, `call_native_action_model`); `_note_thinking` feeds it
the cumulative reasoning. Fast-model calls are never narrated, nor anything before `narrator_delay_seconds`;
then a daemon thread asks the **fast** model (reasoning off) for one breath-sized line at most every
`narrator_interval_seconds`. Material: the request, the newest `rolling_summaries` fitting
`narrator_context_tokens`, reasoning since the last line, and its own recent lines. Providers that stream no
reasoning leave it the request and earlier steps; an unchanged-material call is not asked again. The honesty
rule lives in `narrator_prompt.txt` (a turn of thought is voiced only when the material shows it; `SILENCE` is
valid). Output is the live-only `narration` event (`print_narration`, in `_TRANSIENT_EVENTS`), never in a model
request. A provider failure is a `[narrator]` debug line and silence. A `message` event carries `source`
(`reply` from `print_user_markdown`, `notice` from `print_result`, `skill` from `print_skill_message`; missing
means reply) so voice frontends read only replies. In voice mode the closing cleanup ledger goes to the debug
channel (`agent_runtime.cleanup_summary`) unless a stop could not be confirmed. `JournalingUI.print_narration`
is unconditional, so set `voice_mode` only where a frontend speaks it. Tests: `test_narrator.py`.

### Role routing (`role_router.py`)
Four roles (`_VALID_ROLES`): `coder`, `reader`, `devops`, `chat`. First match wins: an explicit no-write marker
→ `reader` (beats topic) → deterministic regex rules (devops → coder → chat, bilingual EN/RU) → LLM router call
on `fast_model` → keyword fallback → default `chat`. Returns a `RoleRouteResult` (alias `RouteDecision`) with the
role and its `source`. `reader` is a full role (`reader_prompt.txt`): it inspects and reports, writes disabled.
`main.py --role` also accepts `auto` ("let the router decide"), not a fifth role.

### Context budget (`context_budget.py`)
How much prompt one call may occupy is derived from the **active model's** limit; `context_token_limit`
resolves it per call, so escalating fast → smart re-resolves it.
**`MODEL_INPUT_LIMITS` holds max *input* tokens, never the advertised context window**: OpenAI reserves its
128k max output inside the window (a 400k-window GPT-5 model rejects a prompt over 272k), while Anthropic
bounds the input alone. Lookup strips any vendor prefix and takes the longest matching prefix; unlisted ids get
`DEFAULT_INPUT_LIMIT` (256k). The budget is `input_limit * 0.85` (tiktoken under-counts for Anthropic and
DeepSeek tokenizers, and the margin covers the reply on vendors that bill output from the same window).
FreeFlow is the exception: its 11k is a hard per-request limit on the free backends.
Models discovered live (`fetch_available_models`) are normally newer than the table, so three rules apply in order:
1. `_is_small_openai_tier` runs **before** the table: a `-mini`/`-nano` id in a modern OpenAI family is the 272k
   tier (otherwise `gpt-5.5-mini` prefix-matches `gpt-5.5` and inherits four times its real limit).
2. Longest-prefix lookup in `MODEL_INPUT_LIMITS`.
3. `_family_fallback`: an unlisted member of a known family inherits the family's current generation
   (new `gpt-6-*` 872k, new `claude-opus-*` 1M, new `claude-*haiku*` 200k).
**A table entry must never shadow a newer generation** (matching is by prefix; hence `"gpt-5-"` with a trailing
hyphen, since bare `"gpt-5"` also matched `gpt-5.6-luna`). Too small a budget is silent and permanent, too large
is corrected by the first rejection. A guess that is too high is corrected by the provider:
`agent_model.is_context_overflow_error` recognises the rejection, `context_budget.note_input_overflow` records a
reduced budget for that model id (session-local, 60% of what was refused, only tightening), and `call_model`
re-trims and re-sends within the same attempt budget. **A learned rejection outranks every override**,
`AGENT_MAX_CONTEXT_TOKENS` included. Override order: `AGENT_MAX_CONTEXT_TOKENS` → explicit non-zero
`config.max_context_tokens` → derived, then clamped by anything learned (default 0 = derive).

This budget is what the section-priority trimmer (`agent_model.trim_prompt_to_token_limit`) enforces. The caps
upstream of it (`executor.max_read_chars`, `prompt_value_char_limit`, `prompt_result_char_limit`) exist only to
stop one runaway value; `prompt_value_char_limit` is never below `max_read_chars` (two independent caps on the
same content once delivered a 17k-char file as 1.6k). `"Detailed recent action data:"` is a **priority-1**
section because every other section about a read is a summary of it. The trimmer is measured, never estimated:
`_truncate_section_text` binary-searches the cut with the caller's tokenizer and the function ends with a hard
cut to the budget (a fixed chars-per-token ratio assumes prose and left code-heavy prompts ~3x over budget).
Priority-0 sections are never trimmed, so the final cut is what keeps the promise.
**Required artifacts are searched for once, not per refresh:** `_anchor_artifacts` walks the tree to find a
partial path the task named; `_anchored_required_artifacts` caches the walk and `_execute_action` drops the cache
only after an action with a side effect.
**The detail window and the repeat guard are one mechanism.** Full results live in `recent_result_details`
(`detailed_feedback_limit`, 35), index-aligned with `recent_result_signatures`; a repeated action replaces its own
earlier copy (`_keep_result_in_view`). `is_repeat_blocked` refuses a repeated read-only action only while its
earlier answer is still in that window (`_earlier_answer_in_view`); the prompt marks evicted files next to `Read
files` (`agent_context._read_files_out_of_view`). The model's only memory across calls is `step_summary` (kept on
any step, `_keep_step_summary`); reasoning and `thought` never reach the next prompt.
**The prompt carries no standing governance.** Completion blockers, progress, required/missing artifacts and
"completion status" are not rendered (the model spent its reasoning reconciling them with the task). The gates
act when a finish is attempted: `_finish_with_final_answer` and `_finish_with_report` put the refusal reason into
`pending_instruction` ("Additional instruction", priority 0). "Allowed next actions now" appears only while
`required_next_actions` actually restricts the next step.
**A change-required gate never leaves only a write, and a bug claim can be refuted.** The blockers for a task
that expects a change (`not_modified`, `no_changes`, `no_activity`) allow `_CHANGE_OR_REPRODUCE_ACTIONS` (writes
*and* `run_shell`), since running the reported problem confirms or refutes the claim.
`role_router.has_deferred_write_marker` ("don't change files until steps 1-4 are done") clears
`create_goal`/`modify_goal` and the required-artifacts guess in `run_loop`; writes stay possible after those
stages. A finish with `"outcome": "no_change_needed"` (`parser.FINISH_OUTCOMES`) says the reported problem was run
and does not occur; the runtime checks only what it can (a file was read, the latest `run_shell` that ran exited
0 via `RunState.last_shell_returncode`, nothing changed: `_no_change_claim_unproven`). An unproven claim is
refused once with what is missing; a repeat is taken as an honest `incomplete`.
**Section headers in `agent_context.SECTION_PRIORITIES` must match what `build_runtime_prompt` renders, character
for character** (a mismatched header folds into the section above and is trimmed at *that* priority);
`test_agent_context.py` asserts every rendered header is a known key.

### Model tiers (`agent_model.py`)
Two models per provider: **fast** and **smart** (`llm_clients.MODEL_ROLES`; a `planner` key in an older config is
ignored). A run always starts on fast (request length or wording never picks the tier) and escalates to smart,
once and never back, on parse errors, consecutive errors or stagnation, unless the user pinned a model.
Role prompts are loaded and cached from `kernel/prompts/system/`: `{chat,coder,commit,complete,devops,reader,
report,router,universal}_prompt.txt`, plus `constitution.txt`, `native_tools.txt`, `plan_mode.txt`,
`end_session.txt`, `end_session_chat.txt`.
User-selectable styles live in `kernel/prompts/styles/{agents,chat}/<slug>.txt`. **One session, one style:** the
choice is stamped into the session document at session start (`agent_logging.session_styles`, restored by
`restore_session_document` *before* its empty-document exit) and every read resolves it through
`agent_model._session_style_name`, never the global preference (otherwise step 0 and later steps could run on
different beliefs). **The person picks one style, the agent's;** the chat style is derived by the single table
`user_config._CHAT_STYLE_FOR_AGENT_STYLE` (`chat_style_for`; default and architect → default, senior-dev, paranoid
and perfectionist → honest, friendly-developer and explainer → friendly, minimalist → no-friendly,
fast-and-dirty → energetic), so no frontend carries a chat picker or table copy, and `get_saved_style("chat")`
reads only `styles.agents`. `/style` writes the global preference and calls `agent.adopt_session_style`: an
untouched session adopts the new pair, one that holds work keeps its stamp until the next session. The selected
*file* is re-read per call. The reporter (`report_prompt.txt`) is built by `build_report_system_prompt`: the
constitution, report prompt and this session's agent style; the workspace instruction file never reaches it.

### Reasoning effort (`reasoning_effort.py`)
One global knob (`/effort`, `AGENT_REASONING_EFFORT`, persisted `reasoning_effort`) on the scale `false | low |
medium | high | xhigh | max`. `reasoning_effort.py` owns the per-provider translation: OpenAI/OmniRoute get a
top-level `reasoning_effort` (`max`→`xhigh`, `false`→`minimal`), OpenRouter `extra_body.reasoning.effort`,
DeepSeek `reasoning_effort` in `{high,max}` (or `extra_body.thinking.type=disabled` for `false`), and the
Anthropic / FreeFlow wrapper clients get a neutral marker they translate themselves (Anthropic to a
`thinking.budget_tokens` block, dropping `temperature`/`top_p` and bumping `max_tokens`; FreeFlow forwards it
only to Groq gpt-oss). Injection happens in exactly two places: `_create_json_completion` (action loop, via
`merge_reasoning_kwargs`) and the chat path in `agent.py`; `role_router` is left alone.
`_create_completion_with_reasoning_fallback` strips the reasoning kwargs and retries once on a reasoning
rejection (`is_reasoning_error`), printing a `ui.print_error` warning **every time** (deliberately no de-dup).
`/effort` prints an upfront advisory (`effort_advisory`). The effort level is part of the `call_model`
response-cache key.
**`reasoning_content` is never dropped by context compression** (DeepSeek's thinking mode returns a silent HTTP
400 when a history carries assistant `content` without its `reasoning_content`). Kvist does not round-trip it
today, but the compression paths are hardened: `context_utils.trim_history` returns retained messages by
identity, `trim_chat_messages_to_token_limit` rebuilds with `{**message, ...}`, and
`agent_logging._serializable_chat_history` keeps it in `_PERSISTED_MESSAGE_KEYS`. A sliding window may drop a
whole turn but must never strip the field from a turn it keeps.

### Provider abstraction (`llm_clients.py`)
Model catalogs (`_MODEL_DEFAULTS`, `_MODEL_OPTIONS`, fallback chains, `_DEPRECATED_FREEFLOW_MODELS`,
`_FREEFLOW_MODEL_ALIASES`) are offline fallbacks for when live discovery fails; `test_llm_clients.py` enforces
coherence: **no alias may resolve to a deprecated or unlisted model**, every default must be offered by its
provider, every fallback chain must start at an offered model. `test_context_budget.py` requires every shipped
model to have an explicit `MODEL_INPUT_LIMITS` entry (the family fallback is for ids discovered at launch).
Providers: six core (`openai` / `anthropic` / `deepseek` / `freeflow` / `omniroute` / `openrouter`), the OAuth
provider `chatgpt` (ChatGPT Plus/Pro: browser sign-in with PKCE and loopback callback, tokens in the private
`chatgpt_oauth` section of `config.json`, Responses API with `store=false`; `providers/chatgpt_auth.py`,
`chatgpt_client.py`, `--config-op chatgpt-login|chatgpt-logout|chatgpt-accounts`, `auth_type: "oauth"`), and a
**manual tier** of 36 third-party OpenAI-compatible vendors (`MANUAL_OPENAI_PROVIDERS`) plus user-defined
endpoints under a `custom:` prefix. `SUPPORTED_LLM_PROVIDERS` leads with `anymodel` (partner provider and
blank-start default), then the six core providers, then `tuple(MANUAL_OPENAI_PROVIDERS)`. **Adding a vendor is
one dict entry** (`label`, `api_key_key`, `base_url_key`, `default_base_url`); every lookup (`provider_label`,
`required_secret_keys_for_provider`, `resolve_models_for_provider`, `fallback_*`, `get_model_options`,
`_discovery_endpoints`, `create_llm_client`) keys off that dict or the derived `MANUAL_MODEL_PROVIDERS` frozenset,
never a per-provider branch. Manual-tier providers have one API key (`<slug>_api_key`, env `<SLUG>_API_KEY`), a
region-overridable base URL (`<slug>_base_url`) and **no static `_MODEL_DEFAULTS`/`_MODEL_OPTIONS`** (their ids
change too often). `databricks` has `default_base_url = ""` (every workspace has its own URL): `create_llm_client`
raises and `_discovery_endpoints` contributes nothing until `databricks_base_url` is set, never falling through to
another vendor. `gemini` and `groq` use separate credentials from FreeFlow's (`gemini_direct_api_key`/
`groq_direct_api_key`) and must stay independently configurable. `configure_manual_provider_models_interactively`
(main.py) offers a live-catalog picker via `_model_picker_options` when discovery works, else text entry.
`openai`, `omniroute` (local gateway, default `http://localhost:20128/v1`, models entered manually), `openrouter`,
`deepseek` and every manual-tier provider use the OpenAI SDK; `anthropic` uses `AnthropicCompatibleClient`;
`freeflow` wraps Gemini+Groq via `FreeFlowCompatibleClient` with cross-backend fallback. Capability is per
provider: `provider_supports_streaming` is true for openai, openrouter, anthropic, deepseek and `anymodel` (the
rest of the manual tier is false until individually verified), `provider_supports_json_mode` for openai,
openrouter and deepseek only. `fetch_available_models` queries `/v1/models` on **every** launch and degrades to
configured models with a warning.

### Execution boundary (`executor.py` + `terminal.py` + `command_policy.py` + `sandbox.py` + `process_tree.py` + `secret_paths.py`)
`SafeExecutor` is the single gate for every action and returns an `ExecutionResult` (`ok`, `action_type`,
`data`). `search_code` runs a trusted system `rg` (`--no-config`, fixed-string, case-insensitive,
`.gitignore`-aware, symlinks not followed; the result says `skips_gitignored_files` and `truncated` when more
hits exist than the `max_results` it was given, up to 200; `find_files` has the same `truncated`; prompt lists
are not cut by item count) and passes every hit through the same read boundary as `read_file`; without `rg` it
walks the tree in Python (slow). It enforces the workspace boundary (paths resolved and checked with
`relative_to`, symlink-escape protection), blocks the internal `.kvist` dir, and **strictly forbids reading or
writing `.env` files**.

**`terminal.py` is split into `terminal_*.py` modules; `terminal.py` stays the import point.** It holds
`TerminalExecutor` (`__init__`, `run`, `_run_once`, approval, policy, `_assess_risk`, `_execute_validated`, env
and child-HOME handling) and re-exports the names others import. `TerminalExecutor` composes four mixins:
`ArgumentValidationMixin` (`terminal_validation.py`), `GitValidationMixin` (`terminal_git.py`),
`InterpreterValidationMixin` (`terminal_interpreters.py`), `ExecutableBindingMixin` (`terminal_bindings.py`); a
mixin never imports `kernel.sandbox.terminal`. Module-level: `terminal_types.py` (results, errors, context dataclasses),
`terminal_rules.py` (forbidden executables/operators, secret-token regexes, per-profile allowlists),
`terminal_limits.py`, `terminal_env.py` (child environment, controlled PATH), `terminal_paths.py`,
`terminal_trust.py` (shebang parsing, venv launcher, trusted system executables), `terminal_hitl.py`. Tests patch
`assess_command` and `resolve_bubblewrap_path` on `kernel.sandbox.terminal`, so `_assess_risk` and `__init__`, the only
callers, stay in `terminal.py`.

Before any of that, `SafeExecutor._relativize_workspace_paths` rewrites an absolute `cwd`, absolute argv tokens
and a leading `git -C <dir>` into relative form **only when the path's real location is inside the workspace**
(anything else is refused as written). The model is told each rewrite (`path_note`, appended to the error when
the command is refused anyway); the native tool schemas carry the same rule in their `path`/`cwd`/`command`
descriptions.

Shell path, in order: `SafeExecutor._run_shell` (arg checks, `.env`/`.kvist` guards, forwards the routed role) →
`TerminalExecutor.run(command, role=...)` → project deny → one authoritative hard validator for every pipe
segment → intrinsic risk → project ask/eligible allow → HITL where required → exact context reconstruction → the
same hard validator → context comparison → spawn. Hard-denied paths/executables/syntax never invoke HITL and
cannot be unlocked by project policy. DANGEROUS commands always remain per-execution approvals, never persisted.
Allowlist misses are approval-eligible, not hard-denial bypasses. The role comes from routing
(`executor.set_active_role`), never model args. HITL pauses the live TUI; non-TTY approval fails
deterministically.

The bound execution context includes argv, canonical cwd plus directory identity, role, policy digest,
sandbox/profile/network, sanitized child-environment digest, lookup and target executable identities (plus
untrusted executable content), attested shebang interpreter identity/digest, the protected-secret mask digest,
the external tool-path set, and hashes of directly selected scripts/common build files. `_execute_validated`
requires it, repeats validation, and aborts if it changed. Parent environment inheritance is allowlisted;
`AGENT_CHILD_ENV_ALLOW` is the explicit extension and cannot re-enable secrets or loader/startup controls. Every
run uses a new private, verified-empty child HOME, including explicit workspace mode and the weak fallback.

**Inline Python is one argument.** `_interpreter_target` returns mode `inline` for `python [safe flags] -c CODE`
with exactly one non-empty code item; anything else with `-c` (extra arguments, `-cCODE`, `-c` after a script)
stays hard-denied, as do the other interpreters' inline modes and `python -`. The code is an argv item, so the
approved `ExecutionContext.argv` binds it exactly, the approval dialog shows it, and sandbox, token guards and
per-execution approval apply as to a script.

**Scripts with a `#!` line have their interpreter attested:** the shebang is parsed (direct, `/usr/bin/env NAME`,
`env -S NAME ARGS`), resolved with the same sandboxed PATH, and must be a trusted system binary or a workspace
file (else hard-denied); its identity/content is bound into the context. A project launcher symlink whose target
is a user-immutable system binary (e.g. `.venv/bin/python -> /usr/bin/python3`) is permitted and keeps its own
path as argv[0] (so venv/`pyvenv.cfg`/site-packages work, even when found via a bare `python` on the child
PATH); a symlink to any other target stays denied. `_verify_venv_python_launcher` also accepts a launcher whose
target is outside the trusted system roots (e.g. a `uv`/`pyenv` interpreter under HOME) in every sandbox mode,
but only when the venv's own `pyvenv.cfg` declares that exact resolved path in `executable` (a mismatch is denied
like any alias). Under Bubblewrap HOME is hidden, so `terminal.venv_interpreter_roots` read-only binds that
interpreter's install root (validated like `AGENT_TOOL_PATHS`) and `venv_interpreter_links` recreates with
`--symlink` only the individual links on the way (directory links included); nothing else under those
directories becomes visible.

**Windows trust** (`terminal_windows_acl.py`): `st_mode` is synthetic there, so `_path_is_user_mutable` reads the
DACL and calls a path mutable when an allow entry grants a write right to any principal except SYSTEM,
Administrators and TrustedInstaller (inherit-only entries and owner rights are not evaluated; an unreadable DACL
or missing pywin32 means mutable). Trusted roots are `System32`, `SystemRoot` and the Program Files directories;
the child PATH is the system directories plus real-PATH entries passing the same check (`os.defpath` holds no
executables). A user-writable Python or Node install stays out of PATH until `AGENT_TOOL_PATHS` names it.

**Project toolchains:** the child PATH prepends existing project-local tool dirs (`.venv/bin`, `venv/bin`,
`node_modules/.bin`) inside the workspace, then validated `AGENT_TOOL_PATHS` entries, then trusted system dirs;
the user's full HOME/`.local`/nvm/pyenv/cargo are never restored. External tool dirs are read-only mounted into
Bubblewrap and revalidated before spawn.

Read-only Git commands use direct `git SUBCOMMAND` syntax. The spawn argv disables repository fsmonitor/index-hook
writes, external diff/textconv, implicit signature verification, remerge drivers and recursive submodule helper
execution; global options before the subcommand fail closed. The child PATH omits `/usr/local` and other
user-mutable locations.

**Sandbox honesty:** `auto` selects a trust-checked system Bubblewrap on Linux. Its minimal filesystem mounts the
writable workspace and required runtime trees, masks `.kvist` (tmpfs) and every protected workspace secret
(`secret_paths.py`: `.env`/`.env.*`, `.ssh`/`.aws`/`.gnupg`/`.kube`/`.azure`, `id_rsa`/`id_ed25519`/`id_ecdsa`/
`credentials`, `*.pem`/`*.key`/`*.p12`/`*.pfx`/`*.crt`, etc.; files shadowed by read-only `/dev/null`,
directories by empty tmpfs), uses an empty HOME and ephemeral `/tmp`, hides host `/home` and `/root`, isolates
process namespaces, and denies network unless `AGENT_SANDBOX_NET=allow`. It never binds the host root. Masking
neutralises relative/absolute/symlink/runtime-constructed paths in any child code. If auto cannot find trusted
bwrap, arbitrary-code commands enter a per-execution-approval fallback that project allow cannot silence, and
are hard-denied without a cgroup v2 scope. Explicit `workspace` is the weaker user-selected mode (warned; no
secret masking or `.kvist` isolation); explicit unavailable `bubblewrap` blocks shell execution. Where no OS
network boundary exists (Windows, Linux without bwrap; `sandbox.network_isolation_available`) a command is not
refused: it runs with network that cannot be denied (`TerminalExecutor._run_network_unenforced`), every approval
carries `NETWORK_UNENFORCED_NOTE`, and `build_agent` warns at start. Refusing left those systems without a shell.

**Process-tree control & bounded output** (`process_tree.py`): every spawn runs under a supervisor. Detached
descendants cannot survive a timeout or completion: Bubblewrap's PID namespace reaps them, a cgroup v2 scope with
`cgroup.kill` tears the subtree down on timeout and completion (killpg fallback without a cgroup). Two daemon
readers drain stdout/stderr into fixed-size buffers and discard the overflow; the tree is killed before the
readers are joined so a held-open pipe cannot wedge the parent.

Known limits: transitive imports/build includes and helpers spawned by trusted system tools are not individually
content-attested; repository/history-aware tools may surface protected content not named in argv; a narrow TOCTOU
window remains between final comparison and spawn; `workspace` mode has no OS isolation and its cleanup depends
on cgroup v2; Windows descendant cleanup is best-effort.

Every result the model can be influenced by (`read_file`, `read_multiple_files`, `search_code`, `git_status`,
`git_diff`, `run_shell`, `web_search`, `fetch_url`) carries `untrusted: true` plus an `UNTRUSTED EXTERNAL
CONTENT` notice, and `agent_runtime.feedback_to_detail` wraps it in `<UNTRUSTED_TOOL_OUTPUT source="...">`. JSON
encoding does not escape that terminator, so the payload passes through `redaction.defang_prompt_boundary_tags`
first: a forged `</UNTRUSTED_TOOL_OUTPUT>` or `<PROJECT_INSTRUCTIONS>` becomes `[tag removed by Kvist]`.
Trade-off when hacking on Kvist *with* Kvist: a file that literally contains those tag strings (this repo's own
`agent_runtime.py`, `redaction.py`, `kernel/prompts/system/`) reads back with the marker in place; an edit built
on it fails to match rather than corrupting the file.

## Conventions

- All modules start with `# -*- coding: utf-8 -*-` and `from __future__ import annotations`.
- Dataclasses use `slots=True`. State mutation flows through `RunState`.
- User-facing strings are bilingual EN/RU; routing and prompts handle both languages.
- Tests use fakes from `kernel/tests/conftest.py` (`FakeLLMClient`, `NullUI`, `agent_factory`, `make_command`);
  no network. Gate real LLM calls behind the `live_llm` marker.

## Code generation rules

1. Code, comments, and internal reasoning: English.
2. Final response to the user: Russian.
3. No premature abstraction. Do not introduce a class, interface, or config layer for a single use case.
4. Functions do at most one thing. If a function needs a comment explaining sections, split it.
5. Prefer early return over nested if/else. Max nesting depth: 3.
6. No silent exception swallowing. Catch specific exceptions only, never bare except.
7. No speculative generality. Do not add parameters, flags, or extension points for hypothetical future use cases.
8. Comment only "why", never "what". If code needs a "what" comment, rewrite the code to be self-explanatory.
9. When a simple and a "robust" solution both satisfy the current requirement, choose the simple one.
