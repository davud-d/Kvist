# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Kvist is a local, terminal-based coding agent that operates on one workspace
directory at a time. It plans a task, then runs an action/feedback loop where an LLM
emits one JSON action per step (read/write files, run shell, search web, etc.), the
agent executes it through a sandboxed executor, feeds the result back, and repeats
until completion. There is also a JetBrains plugin (`jetbrains-plugin/`, separate
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
- Per-project state lives under `<workspace>/.kvist/`: named-session documents
  (history, chat, `/resume` checkpoint) in `sessions/<name>.json`, the active-session
  marker in `active_session.json`, the workspace-global change journal (for
  `/changes`/`/revert`) in `change_journal.json`, logs in `logs/`. All are managed by
  `agent_logging.py` / `change_journal.py` on top of `durable_json.py` (atomic
  replace + `.bak` recovery copy). Headless frontends manage sessions via
  `main.py --session-op ...` (`session_store.py`), never by touching the files.
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
  `<PROJECT_INSTRUCTIONS>` as a source that can never lift it. Planner, router
  and report prompts are deliberately untouched.
- Per-project command permissions live in `<workspace>/.kvist/permissions.json`
  (`allow`/`deny`/`ask` patterns, `permission_policy.py`); deny and built-in hard
  blocks always win over allow, and intrinsic DANGEROUS commands remain
  per-execution approval-only.

## Architecture

### The action protocol (the contract everything revolves around)
Each loop step the LLM returns a single JSON object parsed by `parser.py` into an
`AgentCommand` (`protocol.py`): `thought`, `plan_step` (inspect/plan/edit/verify/done),
`user_message`, and an `action` with a `type` + `args`. There is deliberately no
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
- **coder / reader / devops** build a plan (`agent_planner.py`) then enter `run_loop` (`agent_runtime.py`),
  which calls the action model, parses, executes via `SafeExecutor`, registers feedback,
  tracks derived progress/verification, and enforces stop conditions (step budget, stagnation,
  consecutive errors, repeated-action blocking, write-required-on-create-tasks).

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

### Role routing (`role_router.py`)
Four roles (`_VALID_ROLES`): `coder`, `reader`, `devops`, `chat`. First-match-wins
priority: an explicit no-write marker → `reader` (beats topic) → deterministic regex
rules (devops → coder → chat, bilingual EN/RU) → LLM router call on `fast_model` →
keyword fallback → default `chat`. Returns a `RoleRouteResult` (alias `RouteDecision`)
carrying the chosen role and its `source`. `reader` is a full role with its own prompt
(`prompts/system/reader_prompt.txt`): it inspects and reports, and its writes are
disabled. `main.py --role` additionally accepts `auto`, which is "let the router decide"
rather than a fifth role.

### Model tiers (`agent_model.py`)
Three roles per provider: **fast**, **smart**, **planner**. A run uses fast by default
and escalates to smart on complexity="complex", parse errors, consecutive errors, or
stagnation — unless the user explicitly pinned a model. Role system prompts are loaded
and cached from `prompts/system/` — seven files:
`{chat,coder,devops,planner,reader,report,router}_prompt.txt`.

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
Six built-in providers (`SUPPORTED_LLM_PROVIDERS`), normalized names `openai` /
`anthropic` / `deepseek` / `freeflow` / `omniroute` / `openrouter`, plus arbitrary
user-defined endpoints under a `custom:` name prefix. `openai`, `omniroute`,
`openrouter` and `deepseek` go through the OpenAI SDK directly (OmniRoute is a local
OpenAI-compatible gateway defaulting to `http://localhost:20128/v1`, models entered
manually); `anthropic` uses `AnthropicCompatibleClient`; `freeflow` wraps Gemini+Groq
via `FreeFlowCompatibleClient` with cross-backend fallback. Capability is per provider,
not one assumption for all: `provider_supports_streaming` is true for openai,
openrouter, anthropic and deepseek (custom endpoints are opt-in via their saved
`streaming` flag, default off), and `provider_supports_json_mode` is true for openai,
openrouter and deepseek only. `resolve_models_for_provider` / `validate_model_selection` /
`fallback_candidates_for_model` handle defaults, validation, and model fallback chains.
`fetch_available_models` queries the provider's `/v1/models` on **every** launch and
degrades to configured models with a warning when that fails.

### Execution boundary (`executor.py` + `terminal.py` + `command_policy.py` + `sandbox.py` + `process_tree.py` + `secret_paths.py`)
`SafeExecutor` is the single gate for every action and returns an `ExecutionResult`
(`ok`, `action_type`, `data`). It enforces the workspace boundary (all paths resolved and
checked with `relative_to`, symlink-escape protection), blocks the internal `.kvist`
dir, and **strictly forbids reading or writing `.env` files**.

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
a symlink to any other target stays denied.

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
