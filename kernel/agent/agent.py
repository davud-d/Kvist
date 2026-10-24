# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import threading
import uuid
from typing import Any, Callable

from rich.console import Console
from rich.text import Text

from kernel.agent.agent_context import (
    build_runtime_prompt,
    build_summary,
    compact_for_prompt,
    format_dependency_versions_for_prompt,
    format_web_facts_for_prompt,
    truncate_for_prompt,
)
from kernel.session.agent_logging import (
    clear_checkpoint,
    create_session,
    delete_session,
    discard_session_if_empty,
    find_resumable_session,
    generate_session_name,
    get_journal_path,
    is_valid_session_name,
    list_sessions,
    load_active_session,
    load_checkpoint,
    load_session,
    peek_checkpoint,
    persist_logs,
    begin_run,
    read_session_document,
    adopt_session_style,
    restore_session_document,
    rename_session,
    session_style,
    sanitize_for_log,
    save_active_session,
    save_checkpoint,
    save_session,
    store_session_title,
)
from kernel.agent.agent_tools import memory_model, memory_prompt_section
from kernel.memory.auto import AutoProcessor
from kernel.context.context_meter import ContextLedger, begin_call, note_generated, note_pending
from kernel.agent.agent_model import (
    call_action_model,
    call_model,
    call_model_streaming,
    _create_completion_with_reasoning_fallback,
    _create_json_completion,
    _emit_usage,
    count_tokens,
    init_tiktoken_encoding,
    _TOKEN_ENCODING_UNINITIALIZED,
    prefer_smart_model,
    safe_extract_completion_content,
    append_reasoning_delta,
    safe_extract_reasoning_delta,
    safe_extract_stream_delta,
    select_action_model_for_run,
    trim_prompt_to_token_limit,
    trim_chat_messages_to_token_limit,
)
from kernel.agent.agent_runtime import (
    action_signature,
    build_cancellation_message,
    build_early_stop_message,
    build_error_feedback,
    build_limit_reached_message,
    build_safety_refusal_message,
    execute_command,
    feedback_error_for_model,
    feedback_to_detail,
    feedback_to_line,
    finalize_run,
    normalize_error_for_model,
    refresh_completion_requirements,
    push_bounded,
    push_unique,
    record_error,
    record_user_message,
    register_feedback,
    register_final_answer,
    register_safety_refusal,
    render_feedback,
    reset_run_state,
    mark_file_write_applied,
    run_loop,
    seal_run_status,
    update_progress,
    update_recovery_tracking,
    update_state_from_success,
    update_verification_evidence,
    _is_read_only_violation,
)
from kernel.plan import end_session
from kernel.session.change_journal import JournalUnavailableError, classify_pending_write
from kernel.session.durable_json import JsonStorageCorruptError, JsonStorageError
from kernel.config.deps import detect_dependency_versions
from kernel.agent.agent_state import AgentConfig, RunState
from kernel.agent.agent_web import (
    append_known_version_to_query,
    contains_package_token,
    extract_fact_from_content,
    extract_version_token,
    normalize_message,
    prepare_fetch_url_command,
    prepare_web_search_command,
    summarize_search_results,
)
from kernel.sandbox.executor import SafeExecutor
from kernel.agent.helpers import HelperManager
from kernel.agent.image_generation import ImageJobs
from kernel.agent.scheduler import Scheduler
from kernel.plugins.skills import SkillManager
from kernel.plugins.resources import Owner, ResourceManager
from kernel.agent.task_control import TaskControl
from kernel.config.performance_logger import PerfLogger
from kernel.protocol.protocol import Action, AgentCommand
from kernel.llm.reasoning_effort import merge_reasoning_kwargs
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text, redact_value
from kernel.agent.role_router import RoleRouteResult, is_bare_continuation, resume_role, route_role_decision_with_llm, route_role
from kernel.context.system_context import (
    current_os_context,
    current_project_context,
    current_workspace_context,
    scratchpad_context,
    current_sandbox_context,
    current_shell_context,
    current_time_context,
    identity_fact,
)
from kernel.sandbox.self_info import ancestor_chain, describe_host, detect_host
from kernel.session.session_events import JournalingUI, SessionJournal
from kernel.session.session_titler import generate_session_title
from kernel.tray.tray_link import TrayLink, is_terminal_host
from kernel.protocol.ui import AgentUI
from kernel.context.language_policy import resolve_response_language


class ChatRequestCancelled(Exception):
    """The user interrupted a chat request.

    Raised instead of letting ``KeyboardInterrupt`` escape the chat path, so
    the interrupt cancels one request rather than the whole REPL session.
    """


# How much read/searched content a run contributes to session history, so a
# later turn in a different role can cite it instead of re-reading. At 1200/600
# chars a chat follow-up was handed a fraction of the file the previous run had
# read and truthfully reported it only had part of it. Session history is
# persisted and re-injected, so the ceiling stays finite — but it is now large
# enough to carry a real source file, not an excerpt.
_SESSION_READ_CONTENT_CHARS = 40000
_SESSION_MULTI_READ_CONTENT_CHARS = 20000
_SESSION_MULTI_READ_MAX_FILES = 10
_SESSION_MAX_HARVESTED_OUTPUTS = 12
_SESSION_SHELL_OUTPUT_CHARS = 4000

# Preamble that hands harvested session output to a later chat turn. It states
# the content is available (so the role uses it) but must NOT forbid admitting
# absence: if the harvester missed something, the role has to say "no data"
# rather than invent it. Positive availability + explicit honesty, no gag.
_CHAT_HISTORY_PREAMBLE = (
    "(Before you respond: here is what happened earlier in this session, "
    "newest turn first. 'Answered:' is what the agent told the user in that "
    "turn. The 'Output:' blocks below are real command results and real file "
    "contents that were read for you — treat them as available to you, "
    "exactly as if you had produced them yourself, and answer the user's "
    "question directly from them when they contain the answer. If the answer "
    "is not present in the content below, say so honestly and ask to read it "
    "rather than inventing it.)"
)
# The rule stands in the constitution too, but a model drops it there and holds
# to it when it is the last thing before the person's message.
_REPLY_END_NOTE = (
    "(Runtime: end the reply where its content ends. A concrete next step is content; "
    "a general offer of help is not.)\n\n"
)
# Added once when chat's answer after end_session came back as the tag again.
_WORDS_NOTE = "(Runtime fact: answer the person in words.)"
# Chat's answer after end_session is asked without the tool's block, and with it
# goes the rule against answering an insult in words: a declined review came
# back as a reproach ("an insult does not solve the task").
_AFTER_TOOL_NOTE = (
    "(Runtime fact: answer the work, or how the person is doing if they are struggling. "
    "Do not reproach, lecture or comment on how the person speaks.)"
)
# The goal of a turn the runtime starts, not the person: the helpers or jobs an
# answered turn left running have ended. What they reported comes with it as
# the loop's background note.
WAKE_GOAL = (
    "(Runtime: no new message from the person. Work you left running after your last answer has ended; "
    "the background note in this request says what it reported. Tell the person what it means for their "
    "request, and continue the task only if it still needs work.)"
)
# The goal of a turn started by a wait or a schedule coming due. The note in the
# request carries the task text the model wrote when it set the wake-up.
SCHEDULE_WAKE_GOAL = (
    "(Runtime: no new message from the person. A wait or schedule you set has come due; the scheduled "
    "wake-up note in this request says which, with the task you recorded for it. Carry that task out now, "
    "and tell the person what you did or found.)"
)


def _no_response_message(language: str | None, detail: str | None) -> str:
    """The chat turn's answer when no model returned one; ``detail`` is redacted error text."""
    suffix = f": {detail}" if detail else ""
    if language == "ru":
        return f"Модель не ответила{suffix}. Попробуйте ещё раз или переключите провайдера через /provider."
    return f"The model did not return a response{suffix}. Try again, or use /provider to switch to a different provider."


def _defanged_for_model(messages: list) -> list:
    """The conversation as the model sees it: the people's messages with forged runtime tags defanged.

    A copy: the stored history and the screen keep what the person wrote.
    """
    return [
        {**message, "content": defang_prompt_boundary_tags(message["content"])} if message["role"] == "user" else message
        for message in messages
    ]


def _end_session_command(args: dict[str, Any]) -> AgentCommand:
    return AgentCommand(plan_step="inspect", action=Action(type="end_session", args=args))


def _runtime_message(text: str) -> dict[str, str]:
    """The runtime's answer to the chat role's tag, on its own channel."""
    return {"role": "user", "content": end_session.runtime_block("", text)}


class LocalAgent:
    def __init__(
        self,
        client: Any,
        executor: SafeExecutor,
        config: AgentConfig | None = None,
        *,
        ui: AgentUI,
        console: Console | None = None,
        perf_logger: PerfLogger | None = None,
        session: str | None = None,
        service_session: dict[str, Any] | None = None,
    ) -> None:
        """``session``/``service_session`` make a helper: it works in that
        service session and never reads or writes the active-session marker."""
        self.client = client
        self.executor = executor
        self.config = config or AgentConfig()
        from kernel.vision.vision import describe_images
        self.executor.image_reader = lambda images: describe_images(self, images, self._state.user_goal)
        self._cache: dict[str, str] = {}
        self._state = RunState()
        # Set from any thread (e.g. a transport reader) to stop the current
        # action-loop turn between steps or mid-generation. Lives on the agent,
        # not on RunState, so it survives a per-run state reset and is cleared
        # explicitly at the start of every turn. Transport-agnostic on purpose.
        self._cancel_event = threading.Event()
        # Pause, stop-all and their states; shares the interrupt event above.
        self.control = TaskControl(self._cancel_event)
        self.control.listeners.append(self._announce_task_state)
        self.service_session = service_session
        self.is_helper = service_session is not None
        # Set on a helper: its runs never change the workspace, whatever role it has.
        self.force_read_only = False
        # Set on a helper: messages from the main agent, drained before each request.
        self.inbox_source: Any = None
        self._wake_listener: Callable[[], None] | None = None
        # The role of the answered turn that left helpers or jobs running; their wake-up turn keeps it.
        self._background_role: str | None = None
        self.console = console or Console()
        self._perf_logger = perf_logger or PerfLogger(executor.workspace)
        # Everything the agent shows is also recorded to the session's event
        # journal, so a frontend can redraw the session later.
        self._journal = SessionJournal(
            executor.workspace,
            lambda: get_journal_path(self, self.current_session),
            lambda message: self._ui.debug_print(message),
        )
        self._ui = JournalingUI(ui, self._journal)
        self._tray = (
            TrayLink(executor.workspace, lambda: self.current_session, debug=lambda message: self._ui.debug_print(message))
            if is_terminal_host() and not self.is_helper
            else None
        )
        self._memory_auto = (
            AutoProcessor(executor.workspace, lambda: memory_model(self), lambda message: self._ui.debug_print(message))
            if self.config.memory_auto_processing and not self.is_helper
            else None
        )
        self.resources = ResourceManager(executor.workspace)
        self.executor.service_dir_factory = self._service_dir
        self.skills = SkillManager(self)
        self.helpers = None if self.is_helper else HelperManager(self)
        self.scheduler = None if self.is_helper else Scheduler(executor.workspace)
        self.images = ImageJobs()
        # Loading tiktoken's rank table costs tens of MiB and is unnecessary
        # for session recovery and chat-only runs. count_tokens initializes it
        # on the first context-budget calculation.
        self._encoding = _TOKEN_ENCODING_UNINITIALIZED
        self._current_step = 0
        self._context_ledger = ContextLedger()
        # Wall-clock of the last model call (performance log).
        self._last_model_duration = 0.0
        # Last resolved user-facing language for this session; used as the
        # fallback when a new request has no detectable language.
        self._last_response_language: str | None = None
        if session is not None:
            self.current_session = session
            create_session(self, session)
        else:
            self.current_session = load_active_session(self)
        self.executor.set_active_session(self.current_session)
        self._session_history: list[dict[str, Any]] = []
        # Set by restore_session_document / rename_session; None until a title
        # exists (generated in the background, or set by hand via /rename).
        self._session_title: str | None = None
        self._title_manual: bool = False
        # Session whose title generation already started, so the early request
        # (first action batch) and the end-of-turn one never both run.
        self._title_requested_for: str | None = None
        # Set by load_checkpoint; describes a mutation interrupted mid-flight.
        self._pending_action: dict[str, Any] | None = None
        # Identifies the active run; a new run() stamps a fresh id.
        self._run_id: str | None = None
        # Lifecycle phase of a resumed checkpoint (set by load_checkpoint).
        self._resumed_phase: str = "executing"
        load_session(self)
        if not self.is_helper:
            self._recover_leftover_resources()

    def _service_dir(self, label: str):
        owner = Owner(session=self.current_session, run_id=str(self._run_id or "run"), kind="run")
        resource = self.resources.temp_dir(owner, label)
        return resource.path

    def _recover_leftover_resources(self) -> None:
        """Stop what a crashed core of this workspace left running; never touch anything else."""
        report = self.resources.recover()
        for outcome in report.outcomes:
            state = "stopped" if outcome.stopped else f"not confirmed ({outcome.error})"
            self._ui.debug_print(f"[resources] left by an earlier crash: {outcome.label}: {state}")
        from kernel.context.attachments import AttachmentStore

        AttachmentStore(self.executor.workspace).expire()

    def _announce_task_state(self, state: str, reason: str) -> None:
        printer = getattr(getattr(self, "_ui", None), "print_task_state", None)
        if printer is not None and not self.is_helper:
            printer(state, reason)

    def _harvest_run_outputs(self) -> list[str]:
        """Collect the substantive content a later turn needs to answer about.

        Beyond shell stdout this captures the *contents* of files the run read
        (read_file / read_multiple_files) and code-search hits. Without it a
        follow-up handled by another role (e.g. chat after coder) receives only
        the request text and truthfully reports it has no file content — the
        role change silently drops the very data the user is asking about.
        """
        outputs: list[str] = []
        for log_entry in self._state.step_logs[-10:]:
            if log_entry.get("status") != "ok":
                continue
            action_type = log_entry.get("command", {}).get("action", {}).get("type", "")
            data = log_entry.get("feedback", {}).get("data", {})
            if action_type == "run_shell":
                stdout = data.get("stdout", "")
                if stdout:
                    outputs.append(stdout[:_SESSION_SHELL_OUTPUT_CHARS])
            elif action_type == "read_file":
                content = data.get("content", "")
                if content:
                    outputs.append(
                        f"[read {data.get('path', '')}]\n{content[:_SESSION_READ_CONTENT_CHARS]}"
                    )
            elif action_type == "read_multiple_files":
                files = data.get("files", [])
                for file_item in files[:_SESSION_MULTI_READ_MAX_FILES]:
                    content = file_item.get("content", "")
                    if content:
                        outputs.append(
                            f"[read {file_item.get('path', '')}]\n"
                            f"{content[:_SESSION_MULTI_READ_CONTENT_CHARS]}"
                        )
                # Never drop files silently — a hidden gap here is BUG-1 again.
                if len(files) > _SESSION_MULTI_READ_MAX_FILES:
                    hidden = files[_SESSION_MULTI_READ_MAX_FILES:]
                    hidden_paths = ", ".join(
                        str(item.get("path", "")) for item in hidden
                    )
                    outputs.append(
                        f"[{len(hidden)} more file(s) were read this step but not "
                        f"stored in history: {hidden_paths}]"
                    )
            elif action_type == "search_code":
                hits = data.get("results", [])
                if hits:
                    lines = [
                        f"{hit.get('path', '')}:{hit.get('line', '')}: {hit.get('text', '')}"
                        for hit in hits[:10]
                    ]
                    outputs.append(
                        f"[search '{data.get('query', '')}']\n" + "\n".join(lines)
                    )
        return outputs[-_SESSION_MAX_HARVESTED_OUTPUTS:]

    def _stopped_run_trail(self) -> dict[str, list[str]]:
        """What a user-stopped run had already read, searched for and changed.

        A native run's conversation is not kept, so the next turn starts blind
        and reads the same files again: a stop after 40 steps of reading once
        cost 48 more. Paths and queries are enough to go straight back to them.
        """
        trail: dict[str, list[str]] = {"read": [], "searched": [], "changed": []}
        for log_entry in self._state.step_logs:
            if log_entry.get("status") != "ok":
                continue
            action = log_entry.get("command", {}).get("action", {})
            args = action.get("args", {})
            action_type = action.get("type", "")
            if action_type == "read_file":
                lines = f":{args.get('start_line', 1)}-{args['end_line']}" if "end_line" in args else ""
                key, items = "read", [f"{args.get('path', '')}{lines}"]
            elif action_type == "read_multiple_files":
                key, items = "read", [str(path) for path in args.get("paths", [])]
            elif action_type == "search_code":
                key, items = "searched", [str(args.get("query", ""))]
            elif action_type in {"write_file", "append_file", "replace_in_file"}:
                key, items = "changed", [str(args.get("path", ""))]
            else:
                continue
            trail[key].extend(item for item in items if item and item not in trail[key])
        return {key: items for key, items in trail.items() if items}

    def _add_to_session_history(
        self,
        user_goal: str,
        role: str,
        result: str,
        stopped_by_user: bool = False,
    ) -> None:
        """Record a finished or user-stopped turn in session history for context."""
        outputs = self._harvest_run_outputs()

        entry = {
            "goal": user_goal,
            "role": role,
            # Stored whole: the per-model prompt budget decides what survives.
            "result": result,
            "reply_id": end_session.next_message_id(self, from_person=False),
        }
        if self._state.message_id:
            entry["msg_id"] = self._state.message_id
        if outputs:
            entry["outputs"] = outputs
        if self._state.plan_outcome:
            entry["plan_outcome"] = self._state.plan_outcome
        if stopped_by_user:
            entry["stopped_by_user"] = True
            trail = self._stopped_run_trail()
            if trail:
                entry["trail"] = trail
            self._journal.mark_stopped()
        safe_entry = redact_value(entry)
        assert isinstance(safe_entry, dict)
        self._session_history.append(safe_entry)
        # Keep last 10 entries
        if len(self._session_history) > 10:
            self._session_history = self._session_history[-10:]
        self.save_session()
        # Fallback for a turn that ended before its first action batch ran
        # (a chat reply, an immediate answer): name the brand-new session now.
        if len(self._session_history) == 1 and not stopped_by_user:
            self._request_session_title(user_goal, result)

    def _request_session_title(self, user_goal: str, agent_reply: str = "") -> None:
        """Start naming the active session once, unless it already has a title."""
        if (
            self._title_manual
            or self._session_title
            or self._title_requested_for == self.current_session
        ):
            return
        self._title_requested_for = self.current_session
        self._start_title_generation(self.current_session, user_goal, agent_reply)

    def _start_title_generation(
        self, session_name: str, user_goal: str, agent_reply: str
    ) -> None:
        """Spawn the title-generation call on its own thread.

        Split from ``_generate_and_store_title`` so a test can call the worker
        directly and synchronously instead of racing a background thread.
        ``AGENT_SESSION_TITLES=0`` skips the spawn entirely (default off in the
        test suite via tests/conftest.py: the thread would otherwise race a
        test's own assertions and tmp_path teardown against a real LLM call).
        """
        if os.getenv("AGENT_SESSION_TITLES", "1") == "0":
            return
        thread = threading.Thread(
            target=self._generate_and_store_title,
            args=(session_name, user_goal, agent_reply),
            daemon=True,
            name="kvist-session-title",
        )
        thread.start()

    def _generate_and_store_title(
        self, session_name: str, user_goal: str, agent_reply: str
    ) -> None:
        ui = self._ui
        title = generate_session_title(
            self.client,
            self.config,
            user_goal,
            agent_reply,
            report=lambda message: ui.debug_print(f"[title] {message}"),
        )
        self._store_session_title(session_name, title)
        notify = getattr(ui, "print_session_title", None)
        if notify is not None:
            notify(session_name, title)

    def new_session(self, name: str | None = None) -> str:
        """Save the current session, then create and activate a fresh empty one.

        Without a name Kvist allocates a timestamped one. This is the ``/new``
        contract: a new session always *starts*, it never overwrites — the
        previous conversation stays on disk and is reachable through
        ``/session``. Other named sessions and the workspace change journal are
        never touched. Returns the new session's name.

        Durable-first: the new session is committed to disk before any
        in-memory state is discarded, so a failed write leaves both the on-disk
        and the in-memory session intact and usable.
        """
        if name is not None and not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name is not None and name == self.current_session:
            raise ValueError(f"Session {name!r} already exists")
        # Their wake-up turn would land in a conversation that never asked for it.
        self.stop_left_running()
        self.executor.scratchpad.clear()
        self.save_session()
        previous = self.current_session
        resolved = name or self._generate_session_name()
        if not self._create_session(resolved):
            raise ValueError(f"Session {resolved!r} already exists")
        self._save_active_session(resolved)
        self.current_session = resolved
        self.executor.set_active_session(resolved)
        self._reset_run_state()
        self._restore_session_document({})
        self._pending_action = None
        self._discard_session_if_empty(previous)
        self._memory_session_left(previous)
        return resolved

    def start_fresh_session(self) -> tuple[str, str | None]:
        """Open a launch in its own session.

        Starting Kvist never continues the previous launch's conversation — a
        new request would otherwise be planned against stale context. Returns
        the new session's name plus the session still holding a resumable
        checkpoint (``None`` when there is none), so the caller can point the
        user at ``/resume``; ``resume`` itself walks there on its own.
        """
        try:
            resumable = self._find_resumable_session()
        except JsonStorageCorruptError:
            resumable = None
        session = self.new_session()
        if self._memory_auto is not None:
            self._memory_auto.catch_up(exclude=session)
        return session, resumable

    def _save_session_marker(self) -> None:
        """Keep compatibility with callers that create an empty session marker."""
        self.save_session()

    def switch_session(self, name: str) -> None:
        """Save the current session, then activate an EXISTING named session.

        Switching never creates a session: a missing target fails before any
        state is mutated, so a typo cannot silently fork history. Creation is
        the explicit job of ``new_session(name)``. Switching also never
        touches the workspace: conversation state and workspace rollback
        (change journal) are separate concepts. Leaving an auto-named session
        that was never used drops it, exactly as starting a new one does.
        """
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name == self.current_session:
            return
        # Read (and thereby validate) the target before mutating anything:
        # a corrupt or missing target must leave the active session intact.
        target_document = self._read_session_document(name)
        if not target_document:
            raise ValueError(f"Unknown session: {name!r}")
        self.stop_left_running()
        self.executor.scratchpad.clear()
        self.save_session()
        previous = self.current_session
        self._save_active_session(name)
        self.current_session = name
        self.executor.set_active_session(name)
        self._reset_run_state()
        self._restore_session_document(target_document)
        self._discard_session_if_empty(previous)
        self._memory_session_left(previous)

    def _memory_session_left(self, session: str) -> None:
        """The person moved on: read the session they left while they work in the next one."""
        if self._memory_auto is not None:
            self._memory_auto.session_left(session)

    def _set_environment_facts(self) -> None:
        """Recompute the cheap, per-run environment facts shown in the prompt.

        Each fact is a single compact line (or empty) so the always-on
        "System environment" section stays small no matter how much is added
        here — one line per fact, never a dump.
        """
        self._state.system_os = current_os_context()
        self._state.system_shell = current_shell_context()
        self._state.system_project = current_project_context(self.executor.workspace) or ""
        git_header, git_details = self.executor.git_context() or ("", "")
        self._state.system_git = git_header
        self._state.system_git_details = git_details
        self._state.system_workspace = current_workspace_context(self.executor.workspace)
        self._state.system_scratchpad = scratchpad_context(self.executor.scratchpad)
        self._state.system_sandbox = current_sandbox_context(
            self.executor.sandbox_mode, False
        )
        chain = ancestor_chain()
        self._state.system_host = describe_host(chain, detect_host(chain))

    def request_cancel(self) -> None:
        """"Stop all": end this turn, its helpers, running calls and every process the task owns.

        Callable from any thread. It sets flags only — it never touches an
        in-flight action or the transport. The loop observes it between steps
        (safe-stop) and while consuming a model stream (mid-generation); the
        cleanup runs on the loop's own thread when it leaves.
        """
        if self.control.state == "paused":
            # A paused task has no running turn, only what it kept alive:
            # "stop all" ends that now, on the caller's thread.
            self._stop_paused_task()
            return
        if not self.control.request_cancel("user_cancelled"):
            # No turn is running (or it already ended): still mark the interrupt
            # so a turn that is just starting does not proceed.
            self._cancel_event.set()

    def _stop_paused_task(self) -> None:
        from kernel.agent.agent_runtime import _end_of_turn_resources, cleanup_summary

        self.control.request_cancel("user_cancelled")
        self.control.reached_safe_point()
        summary = cleanup_summary(self, _end_of_turn_resources(self))
        if summary:
            self._ui.print_result(summary)

    def request_pause(self) -> bool:
        """Pause the main agent and its helpers at their next safe point, keeping the task's state.

        Returns False when there is nothing running to pause. The interface
        shows "pause requested" until the loop reports that it reached the
        safe point; only then is the task "paused".
        """
        return self.control.request_pause("user_paused")

    def stop_helper(self, agent_id: str) -> dict[str, Any]:
        """The person stopped one helper; the main agent is told and does not restart it on its own."""
        if self.helpers is None:
            raise ValueError("this agent has no helpers")
        return self.helpers.stop(agent_id, by="person")

    @property
    def wake_listener(self) -> Callable[[], None] | None:
        """Called from a job's or helper's thread when work an answered turn left running ends on its own.

        Set by a transport that can start a turn by itself (``wake``); while it
        is None, a final answer stops helpers and background jobs as before.
        The listener must not run the turn on the calling thread.
        """
        return self._wake_listener

    @wake_listener.setter
    def wake_listener(self, listener: Callable[[], None] | None) -> None:
        self._wake_listener = listener
        self.executor.jobs.outlives_answer = listener is not None
        if self.scheduler is not None:
            self.scheduler.set_listener(self._background_finished if listener is not None else None)

    def _background_finished(self) -> None:
        listener = self._wake_listener
        if listener is not None:
            listener()

    def has_background_news(self) -> bool:
        """Whether a helper or background job ended on its own and the model has not been told."""
        helpers_done = self.helpers is not None and self.helpers.has_finish_notice()
        timer_due = self.scheduler is not None and self.scheduler.has_due()
        return helpers_done or timer_due or self.images.has_notice() or self.executor.jobs.has_unreported_finish()

    def wake_due(self) -> bool:
        """Whether ``wake`` would run a turn now.

        A paused or interrupted task (it has a checkpoint) keeps its news for
        the person's next message, and a closed session takes no turns.
        """
        if self.control.state == "paused" or not self.has_background_news():
            return False
        return self._peek_checkpoint() is None and not self.is_session_closed()

    def stop_left_running(self) -> None:
        """Stop helpers and background jobs that answered turns left running (session switch, new session)."""
        from kernel.agent.agent_runtime import _stop_left_running

        _stop_left_running(self, "task_finished")
        if self.scheduler is not None:
            self.scheduler.clear_waits()

    def wake(self) -> str | None:
        """Give the model a turn about helpers or jobs that ended after its answer; None when nothing did.

        The turn is not the person's message: it keeps the role and language of
        the turn that left the work running, takes no message number, and the
        news itself reaches the model through the loop's usual background note.
        """
        if not self.wake_due():
            return None
        self._journal.begin_turn("", woken=True)
        self._announce_turn_started()
        try:
            goal = SCHEDULE_WAKE_GOAL if self.scheduler is not None and self.scheduler.has_due() else WAKE_GOAL
            return self._run_turn(goal, self._background_role or None, woken=True)
        finally:
            self._journal.end_turn()
            self._announce_turn_finished()

    def shutdown(self, reason: str = "app_closed") -> None:
        """The process is ending: stop helpers, skills, jobs and every owned resource; keep the checkpoint."""
        # Nothing stopped from here on may start a turn.
        self.wake_listener = None
        if self._memory_auto is not None:
            self._memory_auto.close()
        if self.helpers is not None:
            self.helpers.stop_all(reason)
        self.skills.finish_task(reason)
        self.images.stop_all()
        self.executor.jobs.stop_all()
        self.executor.scratchpad.clear()
        report = self.resources.shutdown(reason)
        for outcome in report.failed:
            self._ui.debug_print(f"[resources] {outcome.label}: not confirmed stopped ({outcome.error})")

    def _reset_cancel(self) -> None:
        """Clear a pending interrupt so it cannot leak into the next turn, and mark the turn running."""
        self.control.begin()

    def run(self, user_goal: str, routed_role: str | None = None, *, images: list[dict] | None = None) -> str:
        refusal = self._refuse_closed_session()
        if refusal is not None:
            return refusal
        from kernel.vision.vision import unpack_goal, validate_images
        user_goal, embedded = unpack_goal(user_goal)
        if images is not None and embedded:
            raise ValueError("Pass images either in the goal envelope or as an argument")
        attachments = validate_images(images) if images is not None else embedded
        self._store_attachments(attachments)
        active_task = self._peek_checkpoint() is not None
        if self.helpers is not None and user_goal.strip():
            self.helpers.person_wrote()
        labels = " ".join(f"[Image #{index + 1}]" for index in range(len(attachments)))
        note_pending(self, user_goal)
        self._journal.begin_turn(f"{user_goal} {labels}".strip())
        self._announce_turn_started()
        try:
            if active_task:
                return self._resume_turn(user_goal, attachments)
            return self._run_turn(user_goal, routed_role, attachments)
        finally:
            self._journal.end_turn()
            self._announce_turn_finished()

    def _store_attachments(self, images: list[dict]) -> None:
        """Keep each attached image's original privately and give it an id; drop the bytes from the dict."""
        from kernel.context.attachments import AttachmentError, AttachmentStore

        store = AttachmentStore(self.executor.workspace)
        for image in images:
            original = image.pop("_original", None)
            if original is None:
                continue
            try:
                attachment = store.add_bytes(
                    original, image["name"], session=self.current_session,
                    origin={"kind": "user_message", "session": self.current_session},
                )
            except (AttachmentError, OSError) as error:
                # The model still gets the prepared copy; only re-analysis by id is lost.
                self._ui.debug_print(f"[attachments] {image['name']}: not stored ({error})")
                continue
            image["attachment_id"] = attachment.id

    def is_session_closed(self) -> bool:
        """Whether end_session closed the active session, as its stored document says."""
        end_session.sync_from_disk(self)
        return end_session.is_closed(self)

    def _refuse_closed_session(self) -> str | None:
        """Answer a message to a closed session with CLOSED_REPLY; None when it is open.

        The one lock every transport passes through: it runs before routing,
        the journal and any model call, and writes nothing to the session.
        """
        if not self.is_session_closed():
            return None
        reply = end_session.closed_reply(end_session.closure_language(self))
        self._ui.print_final(reply, [], outcome="closed")
        return reply

    def _run_turn(
        self, user_goal: str, routed_role: str | None, images: list[dict] | None = None, *, woken: bool = False
    ) -> str:
        # Cleared before routing so a cancel requested during a previous turn
        # never aborts this one's setup.
        self._reset_cancel()
        self._reset_run_state()
        if woken:
            # Taken before anything can fail, so the same news never wakes the agent twice.
            from kernel.agent.agent_runtime import (
                _collect_helper_note,
                _collect_image_note,
                _collect_job_note,
                _collect_schedule_note,
            )

            _collect_job_note(self)
            _collect_helper_note(self)
            _collect_image_note(self)
            _collect_schedule_note(self)
        self._state.message_id = end_session.next_message_id(self, from_person=not woken)
        self._state.user_goal = user_goal
        # User-facing response language is resolved by the runtime layer
        # before any model call; the JSON protocol itself stays English. A
        # wake-up's goal is the runtime's English, not the person's language.
        self._state.response_language = (
            self._last_response_language or "en"
            if woken
            else resolve_response_language(user_goal, previous_language=self._last_response_language)
        )
        self._last_response_language = self._state.response_language
        self._set_environment_facts()

        # ── Role routing: either passed from outside or detected locally ─────
        self._state.current_agent_role = self._choose_role(user_goal, routed_role)
        # Shell risk assessment (command_policy) is role-aware; keep it in
        # sync with the routed role for this run.
        self.executor.set_active_role(self._state.current_agent_role)

        if self.config.verbose:
            self._select_action_model_for_run()
            # selected_action_role is model tier (fast/smart), not agent role
            self._ui.debug_print(
                f"Actual role: {self._state.current_agent_role} | "
                f"Selected tier: {self._state.selected_action_role} | "
                f"Selected model: {self._state.selected_action_model}"
            )

        # ── Chat mode: lightweight path, no JSON protocol ────────
        if self._state.current_agent_role == "chat":
            try:
                from kernel.vision.vision import prepare_goal
                user_goal = prepare_goal(self, user_goal, images or [])
                self._state.user_goal = user_goal
                result = self._run_chat(user_goal)
            except (ChatRequestCancelled, InterruptedError):
                self._add_to_session_history(
                    user_goal,
                    "chat",
                    self._stopped_chat_reply(),
                    stopped_by_user=True,
                )
                return self._report_cancelled_turn()
            self._add_to_session_history(user_goal, "chat", result)
            return result

        # ── Standard path (coder / devops): single run_loop ──────
        # Run identity: durably activate this run BEFORE any model/tool call.
        # begin_run atomically replaces any prior resumable checkpoint with a
        # NEW phase="starting" record, so a crash before the first step leaves
        # NEW recoverable (no side effect yet) and OLD unreachable — never a
        # window where neither exists.
        self._run_id = uuid.uuid4().hex
        self._pending_action = None
        pending_goal = user_goal
        if images:
            pending_goal += "\n[Image analysis pending. If resuming without image descriptions, ask the user to reattach the images.]"
        self._begin_run(pending_goal, self._run_id)
        try:
            if images:
                from kernel.vision.vision import prepare_goal
                user_goal = prepare_goal(self, user_goal, images)
                self._state.user_goal = user_goal
                self._begin_run(user_goal, self._run_id)
            if not self._state.selected_action_model:
                self._select_action_model_for_run()
            self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
            self._ui.print_sandbox_notice(self.executor.sandbox_note)
        except (KeyboardInterrupt, InterruptedError):
            return self._report_stop_before_loop(user_goal)
        result = self._run_loop(user_goal, start_step=1)
        self._record_run_turn(user_goal, result)
        return result

    def _record_run_turn(self, user_goal: str, result: str) -> None:
        self._add_to_session_history(
            user_goal,
            self._state.current_agent_role,
            result,
            stopped_by_user=self._state.stopped_by_user,
        )

    def _report_stop_before_loop(self, user_goal: str) -> str:
        """End a turn the user stopped before its first step.

        Nothing ran yet, but the turn is still recorded: the next request may
        well be "carry on", and it has to know this one was cut short.
        """
        self._state.stopped_by_user = True
        message = self._build_cancellation_message()
        self._ui.print_final(message, self._state.user_messages)
        self._record_run_turn(user_goal, message)
        return message

    def select_resume_session(self, session_id: str) -> None:
        """Select an explicit checkpoint without falling back to another session."""
        if not is_valid_session_name(session_id):
            raise ValueError(f"Invalid session id / Неверный id сессии: {session_id!r}")
        document = self._read_session_document(session_id)
        if not document:
            raise ValueError(f"Unknown session / Сессия не найдена: {session_id!r}")
        if document.get("closed") is True:
            raise ValueError(f"Session is closed / Сессия закрыта: {session_id!r}")
        if self._peek_checkpoint(session_id) is None:
            raise ValueError(f"No saved checkpoint / Нет checkpoint: {session_id!r}")
        self.switch_session(session_id)

    def resume(self) -> str | None:
        refusal = self._refuse_closed_session()
        if refusal is not None:
            return refusal
        self._journal.begin_turn("", resumed=True)
        self._announce_turn_started()
        try:
            return self._resume_turn()
        finally:
            self._journal.end_turn()
            self._announce_turn_finished()

    def _announce_turn_started(self) -> None:
        """Tell the tray icon and background memory that a turn runs."""
        if self._tray is not None:
            self._tray.turn_started()
        if self._memory_auto is not None:
            self._memory_auto.turn_started()

    def _announce_turn_finished(self) -> None:
        if self._tray is not None:
            self._tray.turn_finished()
        if self._memory_auto is not None:
            self._memory_auto.turn_finished(self.current_session)

    def _resume_turn(self, instruction: str = "", images: list[dict] | None = None) -> str | None:
        self._reset_cancel()
        self._reset_run_state()
        self._follow_checkpoint_to_its_session()
        result = self._load_checkpoint()
        if result is None:
            return None
        user_goal, last_step = result
        self._journal.describe_turn(user_goal)
        resumed_phase = getattr(self, "_resumed_phase", "executing")

        # Reconcile a mutation that was in-flight when the process died. The
        # checkpoint carries the intended write's pre/intended digests, so we
        # can prove whether it executed instead of blindly replaying it.
        start_step = last_step + 1
        pending = getattr(self, "_pending_action", None)
        if isinstance(pending, dict):
            reconciliation = self._reconcile_pending_action(pending)
            if reconciliation is None:
                return self._report_ambiguous_resume(pending)
            start_step = reconciliation

        # An explicit continuation authorizes another turn without reviving
        # the previous turn's stop limits or losing its collected evidence.
        self._state.consecutive_errors = 0
        self._state.stagnation_count = 0
        self._state.parse_errors = 0
        self._state.step_budget = last_step + self.config.max_steps

        notes = self.skills.restore(self._state.attached_skills)
        if notes:
            self._state.pending_instruction = " ".join([self._state.pending_instruction, *notes]).strip()
        self.skills.resume()
        if instruction or images:
            self._state.message_id = end_session.next_message_id(self, from_person=True)
            if images:
                from kernel.vision.vision import prepare_goal
                instruction = prepare_goal(self, instruction, images)
            if images or not is_bare_continuation(instruction):
                prefix = "After pausing the work, the person wrote: " if self._state.paused_by_user else ""
                self._state.task_updates.append(prefix + instruction)
            self._state.response_language = resolve_response_language(
                instruction, previous_language=self._last_response_language
            )
            self._state.paused_by_user = False
            self._save_checkpoint(user_goal, start_step - 1)
        self._state.paused_by_user = False

        # Environment facts are recomputed for the current machine rather than
        # restored: run() sets them, resume() must too or the prompt shows blanks.
        self._set_environment_facts()
        # Re-detect for the current workspace like run() does; the restored
        # versions may be stale after an interrupted run.
        self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
        self.executor.set_active_role(self._state.current_agent_role)
        notify = getattr(self._ui, "print_route", None)
        if notify is not None:
            notify(self._state.current_agent_role, "resume")
        if self._state.response_language not in ("ru", "en"):
            self._state.response_language = resolve_response_language(
                user_goal, previous_language=self._last_response_language
            )
        self._last_response_language = self._state.response_language
        if not self._state.selected_action_model:
            self._select_action_model_for_run()
        self._ui.debug_print(
            f"Restored from checkpoint: goal='{user_goal}', step={last_step}, progress={self._state.progress}%"
        )
        self._ui.debug_print(
            f"Execution model restored: tier={self._state.selected_action_role}, model={self._state.selected_action_model}"
        )
        # A run interrupted before its first step made no external side
        # effect: start NEW from step 1 rather than reviving the superseded run.
        if resumed_phase == "starting":
            start_step = 1
        result = self._run_loop(user_goal, start_step=start_step)
        self._record_run_turn(instruction or user_goal, result)
        return result

    def _follow_checkpoint_to_its_session(self) -> None:
        """Activate the session holding the interrupted run, if it is elsewhere.

        A launch opens its own session, so the checkpoint the user means by
        ``/resume`` usually lives in the session they were in before. Resuming
        into it also restores that conversation, which is the context the
        interrupted run belongs to.
        """
        try:
            if self._peek_checkpoint() is not None:
                return
            holder = self._find_resumable_session()
        except JsonStorageCorruptError:
            return
        if holder is None or holder == self.current_session:
            return
        self.switch_session(holder)
        if self._last_response_language == "ru":
            message = f"Возобновляю прерванную задачу из сессии «{holder}»."
        else:
            message = f"Resuming the interrupted task from session '{holder}'."
        self.console.print(Text(message, style="dim"))

    def _reconcile_pending_action(self, pending: dict[str, Any]) -> int | None:
        """Decide where to resume given an interrupted action.

        Returns the step to resume at, or ``None`` when the outcome is ambiguous
        and Kvist must stop and ask instead of guessing. Distinguishes intent
        (the agent meant to produce content) from provenance (the agent is
        proven to have produced it): matching the intended content alone never
        grants ``/revert`` ownership.
        """
        pending_step = pending.get("step")
        if not isinstance(pending_step, int):
            return None
        # Action identity: a pending action stamped with a different run must
        # never be reconciled against this run.
        run_id = pending.get("run_id")
        if run_id is not None and self._run_id is not None and run_id != self._run_id:
            return None

        # Non-reconcilable side effects (run_shell, delegate_task): their effect
        # cannot be proven from workspace state after a crash, so they are never
        # replayed — the only safe answer is ambiguous.
        if pending.get("action_kind") == "side_effect":
            return None

        path = pending.get("path")
        intended_digest = pending.get("intended_digest")
        if not isinstance(path, str) or not isinstance(intended_digest, str):
            return None
        try:
            outcome = classify_pending_write(
                self.executor.workspace,
                path,
                pending.get("pre_digest"),
                intended_digest,
                run_id=pending.get("run_id"),
                action_id=pending.get("action_id"),
            )
        except JournalUnavailableError:
            # The recovery journal itself is unreadable: we cannot prove anything.
            return None
        if outcome == "ambiguous":
            return None
        if outcome in ("agent_committed", "desired_present"):
            # The desired file state is on disk, so the write must NOT be
            # replayed. Reconstruct the FULL logical post-action runtime state
            # via the same transition normal success uses, then resume past the
            # step. `agent_committed` already carries journal ownership (promoted
            # in-process); `desired_present` is left unowned — authorship is
            # unproven, so `/revert` will not destroy it.
            self._apply_recovered_file_write(pending)
            return pending_step + 1
        # not_executed: the workspace still holds the pre-mutation content, so
        # the step can safely be retried from scratch.
        return pending_step

    def _apply_recovered_file_write(self, pending: dict[str, Any]) -> None:
        """Replay the logical success transition of a recovered file write.

        Routes a synthetic successful command+feedback through the SAME
        ``_register_feedback`` path as normal execution, so plan progression,
        progress, verification re-arm, recent-result state, and step logs are
        reconstructed identically — without re-executing the write.
        """
        from kernel.protocol.protocol import Action, AgentCommand

        action_type = str(pending.get("action_type") or "write_file")
        path = str(pending.get("path") or "")
        step = int(pending.get("step") or self._current_step or 1)
        command = AgentCommand(
            plan_step=str(pending.get("plan_step") or "edit"),
            action=Action(type=action_type, args={"path": path}),
        )
        feedback = {"ok": True, "action_type": action_type, "data": {"path": path}}
        self._register_feedback(step, command, feedback)

    def _report_ambiguous_resume(self, pending: dict[str, Any]) -> str:
        if pending.get("action_kind") == "side_effect":
            action = str(pending.get("action_type", "command"))
            if self._last_response_language == "ru":
                message = (
                    f"Возобновление остановлено: прерванное действие «{action}» "
                    f"могло уже выполниться, и повторять его небезопасно. Проверьте "
                    f"его эффект вручную; checkpoint сохранён. При необходимости "
                    f"начните заново через /new."
                )
            else:
                message = (
                    f"Resume stopped safely: the interrupted '{action}' may have "
                    f"already run, and replaying it is unsafe. Verify its effect "
                    f"manually; the checkpoint is kept. Start over with /new if "
                    f"needed."
                )
            self.console.print(Text(message, style="yellow"))
            return message

        path = str(pending.get("path", "?"))
        if self._last_response_language == "ru":
            message = (
                f"Возобновление остановлено: невозможно определить, выполнилось ли "
                f"прерванное изменение файла «{path}». Проверьте файл вручную; "
                f"checkpoint сохранён. Исправьте состояние и повторите /resume, "
                f"либо начните заново через /new."
            )
        else:
            message = (
                f"Resume stopped safely: cannot determine whether the interrupted "
                f"write to '{path}' completed. Inspect the file manually; the "
                f"checkpoint is kept. Fix the state and retry /resume, or start "
                f"over with /new."
            )
        self.console.print(Text(message, style="yellow"))
        return message

    _reset_run_state = reset_run_state
    _run_loop = run_loop
    _finalize_run = finalize_run

    _build_runtime_prompt = build_runtime_prompt
    _format_dependency_versions_for_prompt = format_dependency_versions_for_prompt
    _format_web_facts_for_prompt = format_web_facts_for_prompt
    _compact_for_prompt = compact_for_prompt
    _truncate_for_prompt = truncate_for_prompt
    _build_summary = build_summary

    _call_action_model = call_action_model
    _call_model = call_model
    _call_model_streaming = call_model_streaming
    _create_json_completion = _create_json_completion
    _init_tiktoken_encoding = staticmethod(init_tiktoken_encoding)
    _count_tokens = count_tokens
    _trim_prompt_to_token_limit = trim_prompt_to_token_limit
    _trim_chat_messages_to_token_limit = trim_chat_messages_to_token_limit
    _prefer_smart_model = prefer_smart_model
    _select_action_model_for_run = select_action_model_for_run

    _execute_command = execute_command
    _build_error_feedback = build_error_feedback
    _feedback_error_for_model = feedback_error_for_model
    _normalize_error_for_model = normalize_error_for_model
    _register_feedback = register_feedback
    _update_state_from_success = update_state_from_success
    _mark_file_write_applied = mark_file_write_applied
    _update_progress = update_progress
    _register_final_answer = register_final_answer
    _register_safety_refusal = register_safety_refusal
    _seal_run_status = seal_run_status
    _update_recovery_tracking = update_recovery_tracking
    _refresh_completion_requirements = refresh_completion_requirements
    _record_error = record_error
    _feedback_to_line = feedback_to_line
    _feedback_to_detail = feedback_to_detail
    _render_feedback = render_feedback
    _build_early_stop_message = build_early_stop_message
    _build_cancellation_message = build_cancellation_message
    _build_limit_reached_message = build_limit_reached_message
    _build_safety_refusal_message = build_safety_refusal_message
    _action_signature = action_signature
    _record_user_message = record_user_message
    _push_unique = push_unique
    _push_bounded = push_bounded
    _update_verification_evidence = update_verification_evidence
    _is_read_only_violation = _is_read_only_violation

    _prepare_web_search_command = prepare_web_search_command
    _prepare_fetch_url_command = prepare_fetch_url_command
    _append_known_version_to_query = append_known_version_to_query
    _contains_package_token = contains_package_token
    _extract_version_token = extract_version_token
    _extract_fact_from_content = extract_fact_from_content
    _summarize_search_results = summarize_search_results
    _normalize_message = normalize_message

    _sanitize_for_log = sanitize_for_log
    _persist_logs = persist_logs
    _save_checkpoint = save_checkpoint
    _load_checkpoint = load_checkpoint
    _clear_checkpoint = clear_checkpoint
    _create_session = create_session
    _generate_session_name = generate_session_name
    _find_resumable_session = find_resumable_session
    _discard_session_if_empty = discard_session_if_empty
    _begin_run = begin_run
    _peek_checkpoint = peek_checkpoint
    save_session = save_session
    _load_session = load_session
    _read_session_document = read_session_document
    _restore_session_document = restore_session_document
    adopt_session_style = adopt_session_style
    session_style = session_style
    _save_active_session = save_active_session
    _list_sessions = list_sessions
    _delete_session = delete_session
    _store_session_title = store_session_title
    rename_session = rename_session

    # ── Role routing ─────────────────────────────────────────────

    def _choose_role(self, user_goal: str, routed_role: str | None) -> str:
        """Pick the turn's role and tell a UI that listens which way it was chosen."""
        if routed_role:
            decided, source = routed_role, "explicit"
        else:
            decision = self._route_decision(user_goal)
            decided, source = decision.role, decision.source
        role = resume_role(user_goal, decided, self._session_history)
        if role != decided:
            source = "resume"
        notify = getattr(self._ui, "print_route", None)
        if notify is not None:
            notify(role, source)
        return role

    def _route_decision(self, user_goal: str) -> RoleRouteResult:
        """Determine agent role via LLM router (fast_model)."""
        try:
            return route_role_decision_with_llm(
                user_goal=user_goal,
                client=self.client,
                model=self.config.fast_model,
            )
        except Exception:
            # Fallback to keyword-based routing
            return RoleRouteResult(role=route_role(user_goal), source="keyword_fallback")

    # ── Chat mode ────────────────────────────────────────────────

    def _run_chat(self, user_goal: str) -> str:
        """Lightweight chat mode: direct markdown streaming, no JSON protocol."""
        from kernel.agent.agent_model import build_role_system_prompt
        from kernel.context.context_utils import trim_history
        from kernel.llm.llm_clients import fallback_candidates_for_model

        system_prompt = build_role_system_prompt(self, "chat")

        # Initialize chat history if it doesn't exist
        if not hasattr(self, "_chat_history"):
            self._chat_history = [{"role": "system", "content": system_prompt}]
        elif self._chat_history and self._chat_history[0]["role"] == "system":
            # Refreshed every turn so an edit to KVIST-CHAT.md lands on the
            # next message instead of the next session.
            self._chat_history[0] = {"role": "system", "content": system_prompt}

        use_tag = end_session.available(self, "chat")

        # Snapshot before the turn mutates history: a cancelled or failed turn
        # must not leave a user message that no answer ever replies to.
        history_before = list(self._chat_history)
        # Persisted/displayed history keeps the clean user text — the runtime
        # directives are per-turn scaffolding for the model, not part of what
        # the user said, and must not show up as their message in the GUI or
        # get baked permanently into every future turn's context.
        self._chat_history.append({"role": "user", "content": user_goal})

        # Coarse turn cap so a session document cannot grow without bound; the
        # real limit is the token budget applied right after, which is what
        # decides how much of a long conversation actually survives.
        self._chat_history = trim_history(self._chat_history, max_recent=40)
        self._chat_history = self._trim_chat_messages_to_token_limit(self._chat_history)

        # Wire history sent to the model decorates only the current turn with
        # the runtime directives; earlier turns are sent as the clean text
        # that was actually stored. Re-trim afterwards so the decoration
        # itself can't push the request back over the hard input limit.
        wire_history = self._chat_history[:-1] + [
            {"role": "user", "content": self._chat_turn_message(user_goal, offer_tag=use_tag)}
        ]
        wire_history = self._trim_chat_messages_to_token_limit(_defanged_for_model(wire_history))

        content = ""
        last_error: str | None = None

        # Build model list: fast model first, then fallback candidates
        models_to_try: list[str] = [self.config.fast_model]
        try:
            fallbacks = fallback_candidates_for_model(
                self.config.provider,
                self.config.fast_model,
                allow_runtime_fallbacks=True,
            )
            for fb in fallbacks:
                if fb not in models_to_try:
                    models_to_try.append(fb)
        except Exception:
            pass

        self._chat_answer_chunks: list[str] = []
        for attempt, model in enumerate(models_to_try):
            try:
                content = self._chat_reply(wire_history, model, hold_tag=use_tag, hold_thought=use_tag)
                if content:
                    break

                # Empty content (but no exception) — try next model
                last_error = "Model returned empty content"
                continue

            except (ChatRequestCancelled, KeyboardInterrupt) as error:
                # Ordered before ``Exception`` so a stopped turn is never
                # retried against a fallback model. The turn stays in history,
                # answered by what arrived plus a note that the user cut it
                # short, so the next message is read with that in mind.
                self._chat_history = history_before + [
                    {"role": "user", "content": user_goal},
                    {"role": "assistant", "content": self._stopped_chat_reply()},
                ]
                raise ChatRequestCancelled from error

            except Exception as error:
                last_error = redact_text(
                    str(error).strip() or type(error).__name__
                )
                if attempt < len(models_to_try) - 1:
                    self._ui.debug_print(
                        f"Chat attempt {attempt + 1} failed ({last_error}), "
                        "trying fallback..."
                    )
                continue

        if not content:
            # All models exhausted — show friendly error, don't crash REPL
            self._chat_history = history_before
            fallback_msg = _no_response_message(self._state.response_language, last_error)
            self.console.print(Text(fallback_msg, style="yellow"))
            return fallback_msg

        if use_tag and end_session.tag_only(content):
            content = self._chat_end_session(wire_history, model, content, history_before)
        self._chat_history.append({"role": "assistant", "content": content})
        return content

    def _chat_turn_message(self, user_goal: str, *, offer_tag: bool) -> str:
        """The person's message as the model gets it: this turn's runtime facts, the session, the text."""
        # Who the model is and which model serves this turn are two facts: given
        # only the second, the model named itself after its vendor or another product.
        model_note = (
            f"(Runtime fact: {identity_fact(self.config.provider, self.config.fast_model, self.config.reasoning_effort)})\n\n"
        )
        # Without the host fact a chat reply to "restart the editor" invented
        # that the conversation would be lost; it is on disk.
        host_note = (
            f"(Runtime fact: host = {self._state.system_host})\n\n"
            if self._state.system_host
            else ""
        )
        # The number end_session's tag names; only where the tag is offered.
        workspace_note = (
            f"(Runtime fact: workspace = {self._state.system_workspace})\n\n"
            if self._state.system_workspace
            else ""
        )
        message_note = (
            f"(Runtime fact: this is the person's message #{self._state.message_id}.)\n\n"
            if offer_tag
            else ""
        )
        # Built per turn and never stored in the chat history, so a record is not repeated turn after turn.
        memory = memory_prompt_section(self, user_goal)
        memory_note = f"{memory}\n\n" if memory else ""
        # Last before the person's words and never stored: the hour changes every turn, so
        # anywhere earlier it would cut the cached prefix; it lets a greeting at two in the
        # night differ from one at noon.
        time_note = f"(Runtime fact: local time = {current_time_context()['current_time']})\n\n"
        return (
            model_note + host_note + workspace_note + message_note + memory_note + self._chat_session_context()
            + time_note + _REPLY_END_NOTE + user_goal
        )

    def _chat_session_context(self) -> str:
        if not getattr(self, "_session_history", None):
            return ""
        history_lines = [_CHAT_HISTORY_PREAMBLE]
        # Newest first: when the budget cuts this message it keeps the
        # head and the tail, so the oldest turns are what goes.
        for entry in reversed(self._session_history):
            history_lines.append(f"- [{entry['role']}] Asked: \"{entry['goal']}\"")
            if entry.get("stopped_by_user"):
                history_lines.append("    Status: the user stopped this turn before it finished.")
            # A chat reply is already in _chat_history as an assistant message.
            if entry["role"] != "chat" and entry.get("result"):
                history_lines.append(f"    Answered: {entry['result']}")
            for output in entry.get("outputs", []):
                history_lines.append(f"    Output: {output}")
        return "\n".join(history_lines) + "\n\n---\n\n"

    def _chat_reply(self, messages: list, model: str, *, hold_tag: bool, hold_thought: bool = False) -> str:
        """One chat reply, shown as it arrives; a reply that is the end_session tag is never shown."""
        if self.config.stream:
            return self._stream_chat_response(messages, model=model, hold_tag=hold_tag, hold_thought=hold_thought)
        content = self._complete_chat(messages, model)
        if not (hold_tag and end_session.tag_only(content)):
            self._ui.print_user_markdown(content)
        return content

    def _complete_chat(self, messages: list, model: str) -> str:
        safe_history = redact_value(messages)
        assert isinstance(safe_history, list)
        chat_kwargs: dict[str, Any] = {
            "model": model,
            "messages": safe_history,
        }
        merge_reasoning_kwargs(
            chat_kwargs,
            self.config.provider,
            self.config.reasoning_effort,
        )
        response = _create_completion_with_reasoning_fallback(self, chat_kwargs)
        content = safe_extract_completion_content(response, self.config.provider, model)
        self._emit_chat_usage(safe_history, model, content)
        return content

    def _emit_chat_usage(self, messages: list, model: str, content: str, reasoning: str = "") -> None:
        """Report one completed chat call; thinking tokens are billed output too."""
        _emit_usage(
            self,
            model=model,
            input_tokens=sum(
                self._count_tokens(str(message.get("content", "")))
                for message in messages
                if isinstance(message, dict)
            ),
            output_tokens=self._count_tokens(content) + self._count_tokens(reasoning),
            kind="chat",
        )

    def _chat_end_session(self, wire_history: list, model: str, tag_reply: str, history_before: list) -> str:
        """Run a chat reply that is the end_session tag through the one gate.

        Returns the turn's answer for the person and the history: WARNING,
        CLOSED, or the model's words after a declined review or a refused tag.
        The tag exchange lives only in this turn's requests.
        """
        message_id = end_session.flag_tag(tag_reply)
        try:
            if message_id is None:
                return self._chat_words(model)
            flag_args: dict[str, Any] = {"message_id": message_id}
            if end_session.flag_tag_is_request(tag_reply):
                flag_args["request"] = True
            flagged = end_session.handle(self, _end_session_command(flag_args), tag_syntax=True)
            if flagged.log["answer"] == "warning":
                return end_session.warning_text(self._state.response_language)
            if flagged.log["answer"] == "notice":
                return end_session.notice_text(self._state.response_language)
            if flagged.log["answer"] == "review":
                # The review is answered with the tag, so it is never streamed.
                exchange = [*wire_history, {"role": "assistant", "content": tag_reply}, _runtime_message(flagged.answer)]
                decision_reply = self._complete_chat(exchange, model)
                # The reply itself goes into the log: without it a decline and
                # an unreadable answer look the same afterwards.
                unread = f"the review reply was not the decision tag: {json.dumps(decision_reply, ensure_ascii=False)}"
                decision = end_session.decision_tag(decision_reply)
                confirm, reason = decision or (False, unread)
                verdict = end_session.handle(
                    self,
                    _end_session_command({"confirm": confirm, "reason": reason}),
                    unread_reply=None if decision else decision_reply,
                )
                if verdict.final_message:
                    self._state.session_closed = True
                    self._ui.print_final(verdict.final_message, [], outcome="closed")
                    return verdict.final_message
            return self._chat_words(model)
        except (ChatRequestCancelled, KeyboardInterrupt) as error:
            self._chat_history = history_before + [
                {"role": "user", "content": self._state.user_goal},
                {"role": "assistant", "content": self._stopped_chat_reply()},
            ]
            raise ChatRequestCancelled from error
        except Exception as error:
            # The gate has already acted, so the turn is answered, not retried.
            detail = redact_text(str(error).strip() or type(error).__name__)
            message = _no_response_message(self._state.response_language, detail)
            self.console.print(Text(message, style="yellow"))
            return message
        finally:
            self._persist_end_session_log()

    def _chat_words(self, model: str) -> str:
        """The turn's answer after end_session acted: asked as if the tool had never been offered.

        The tool has done its part in this turn, so the request carries neither
        the tag nor the review nor the tool's block. A reply that is still the
        tag is never shown: one retry with a runtime fact, then a fixed line.
        """
        messages = self._chat_words_history()
        reply = self._chat_reply(messages, model, hold_tag=True)
        if not end_session.tag_only(reply):
            return reply
        messages[-1] = {**messages[-1], "content": f"{messages[-1]['content']}\n\n{_WORDS_NOTE}"}
        reply = self._chat_reply(messages, model, hold_tag=True)
        if not end_session.tag_only(reply):
            return reply
        end_session.log_tag_instead_of_words(self)
        carry_on = end_session.carry_on_text(self._state.response_language)
        self._ui.print_user_markdown(carry_on)
        return carry_on

    def _chat_words_history(self) -> list:
        """The first call's conversation without the tool: its system prompt, earlier turns, this message."""
        from kernel.agent.agent_model import build_role_system_prompt

        system_prompt = build_role_system_prompt(self, "chat", offer_end_session=False)
        earlier = [message for message in self._chat_history[:-1] if message["role"] != "system"]
        message = self._chat_turn_message(self._state.user_goal, offer_tag=False)
        turn = {"role": "user", "content": f"{message}\n\n{_AFTER_TOOL_NOTE}"}
        history = [{"role": "system", "content": system_prompt}, *earlier, turn]
        return self._trim_chat_messages_to_token_limit(_defanged_for_model(history))

    def _persist_end_session_log(self) -> None:
        """A chat turn has no run log of its own; one that used end_session gets one."""
        try:
            self._persist_logs(self._state.user_goal)
        except (JsonStorageError, OSError) as error:
            self._ui.debug_print(f"end_session log not written: {redact_text(str(error))}")

    def _stopped_chat_reply(self) -> str:
        note = (
            "[Ответ остановлен пользователем до завершения.]"
            if self._state.response_language == "ru"
            else "[The user stopped this reply before it finished.]"
        )
        partial = "".join(getattr(self, "_chat_answer_chunks", [])).rstrip()
        if end_session.tag_prefix_state(partial) != "text":
            # Never shown, so never kept: the start of an end_session tag.
            partial = ""
        return f"{partial}\n\n{note}" if partial else note

    def _report_cancelled_turn(self) -> str:
        message = (
            "Ответ остановлен вами."
            if self._state.response_language == "ru"
            else "Reply stopped by you."
        )
        self.console.print(Text(message, style="yellow"))
        return message

    def _stream_chat_response(
        self, messages: list, model: str | None = None, *, hold_tag: bool = False, hold_thought: bool = False
    ) -> str:
        """Stream chat response as markdown directly to console."""
        stream_model = model or self.config.fast_model
        safe_messages = redact_value(messages)
        assert isinstance(safe_messages, list)
        stream_kwargs: dict[str, Any] = {
            "model": stream_model,
            "messages": safe_messages,
            "stream": True,
        }
        merge_reasoning_kwargs(
            stream_kwargs, self.config.provider, self.config.reasoning_effort
        )
        begin_call(
            self,
            sum(
                self._count_tokens(str(message.get("content", "")))
                for message in safe_messages
                if isinstance(message, dict)
            ),
        )
        try:
            stream = _create_completion_with_reasoning_fallback(self, stream_kwargs)
        except KeyboardInterrupt as error:
            raise ChatRequestCancelled from error
        try:
            content = self._consume_chat_stream(stream, hold_tag=hold_tag, hold_thought=hold_thought)
            self._emit_chat_usage(
                safe_messages, stream_model, content, "".join(getattr(self, "_last_chat_reasoning", []) or [])
            )
            return content
        except KeyboardInterrupt as error:
            raise ChatRequestCancelled from error
        finally:
            # Applies to cancellation, provider failure, and success alike:
            # an abandoned HTTP stream would otherwise hold its connection.
            self._close_chat_stream(stream)

    def _close_chat_stream(self, stream: Any) -> None:
        close = getattr(stream, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as error:
            # Already unwinding; a close failure must not replace the reason
            # we are here, but it is reported rather than swallowed.
            self._ui.debug_print(
                f"Chat stream close failed: {redact_text(str(error))}"
            )

    def _consume_chat_stream(self, stream: Any, *, hold_tag: bool = False, hold_thought: bool = False) -> str:
        """Show a streamed reply as it arrives and return it.

        ``hold_tag`` holds back the first characters until they show whether
        the reply opens with the end_session tag. One that does is never
        shown; any other is released whole the moment that is clear.
        ``hold_thought`` (with ``hold_tag``) holds the thinking back as well:
        where the tag is offered, a call that ends in it is the tool's own
        move, and the person never sees what led to it.
        """
        chunks: list[str] = []
        holding = hold_tag
        holding_thought = hold_thought
        # Shared, not copied: if the user stops the reply mid-stream, the turn
        # is recorded with whatever had arrived by then.
        self._chat_answer_chunks = chunks
        reasoning_chunks: list[str] = []

        import time
        last_update = time.monotonic()
        last_reasoning_update = last_update

        with self._ui.live_markdown() as update_markdown:
            reasoning_update = getattr(update_markdown, "reasoning", None)
            for chunk in stream:
                # The same flag the action loop watches (see agent_model's
                # stream loop). Without it a cancel requested from another
                # thread — the GUI's only way to stop a turn — was ignored
                # here and a chat answer kept streaming to its end.
                if self._cancel_event.is_set():
                    raise ChatRequestCancelled
                text = ""

                # 1. OpenAI variant (strict objects with attributes)
                delta = safe_extract_stream_delta(chunk)
                if delta is not None:
                    text = getattr(delta, "content", "") if not isinstance(delta, dict) else delta.get("content", "")

                # 2. freeflow-llm variant (raw dicts)
                elif isinstance(chunk, dict):
                    if "choices" in chunk and chunk["choices"]:
                        text = chunk["choices"][0].get("delta", {}).get("content", "")
                    else:
                        text = chunk.get("content", "")

                # 3. freeflow-llm variant (simple object with content field)
                elif hasattr(chunk, "content"):
                    text = chunk.content

                # 4. Fallback for raw string
                elif isinstance(chunk, str):
                    text = chunk

                note_generated(self, lambda: "".join(reasoning_chunks) + "".join(chunks))
                reasoning_text = safe_extract_reasoning_delta(delta)
                if reasoning_text and reasoning_update is not None and not chunks:
                    append_reasoning_delta(reasoning_chunks, reasoning_text)
                    now = time.monotonic()
                    if not holding_thought and now - last_reasoning_update > 0.1:
                        reasoning_update("".join(reasoning_chunks))
                        last_reasoning_update = now

                if text:
                    # The first answer token seals thinking: flush whatever
                    # reasoning was buffered so a fast stream never drops it.
                    if not chunks and reasoning_update is not None and reasoning_chunks and not holding_thought:
                        reasoning_update("".join(reasoning_chunks))
                    chunks.append(text)
                    if holding:
                        holding = end_session.tag_prefix_state("".join(chunks)) != "text"
                        if holding:
                            continue
                        if holding_thought and reasoning_update is not None and reasoning_chunks:
                            reasoning_update("".join(reasoning_chunks))
                        holding_thought = False
                        update_markdown("".join(chunks))
                        last_update = time.monotonic()
                        continue

                    # Throttle rendering to ~10 FPS to prevent CPU choke
                    now = time.monotonic()
                    if now - last_update > 0.1:
                        update_markdown("".join(chunks))
                        last_update = now

            # Final update to guarantee the last parts are rendered. A reply
            # still held is shown now, with any thinking held behind it, unless
            # it is the tag and nothing else.
            reply = "".join(chunks)
            hidden = holding and end_session.tag_only(reply)
            if reasoning_update is not None and reasoning_chunks and not hidden and (holding_thought or not chunks):
                reasoning_update("".join(reasoning_chunks))
            if not hidden:
                update_markdown(reply)

        # Kept for the caller's usage accounting: thinking tokens are billed.
        self._last_chat_reasoning = reasoning_chunks
        return "".join(chunks)
