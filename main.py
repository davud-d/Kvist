# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local AI agent")
    parser.add_argument(
        "--workspace",
        help=(
            "Path to the project workspace directory. Defaults to "
            "Documents/Kvist Workspace."
        ),
    )
    parser.add_argument(
        "--allow-read",
        action="append",
        default=[],
        metavar="DIR",
        dest="allow_read",
        help=(
            "Extra directory the read-only actions may read outside the "
            "workspace. Repeatable. Writes and shell stay workspace-only."
        ),
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Disable the live banner and use the plain terminal interface.",
    )
    parser.add_argument(
        "--frontend",
        choices=("terminal", "tui", "stdio", "stdio-multi"),
        default=None,
        help=(
            "Frontend (defaults to the saved /tui preference). Use 'tui' for "
            "the Node/Ink terminal UI (src/), 'terminal' for the classic UI, "
            "'stdio' for the single-turn NDJSON event "
            "protocol, 'stdio-multi' for a long-lived process serving one goal "
            "per stdin line."
        ),
    )
    parser.add_argument("--provider", help="Provider override for the stdio frontend.")
    parser.add_argument("--fast-model", help="Fast model override for the stdio frontend.")
    parser.add_argument("--smart-model", help="Smart model override for the stdio frontend.")
    parser.add_argument("--planner-model", help="Planner model override for the stdio frontend.")
    parser.add_argument(
        "--role",
        choices=("auto", "coder", "reader", "devops", "chat"),
        default="auto",
        help="Agent role override for the stdio frontend.",
    )
    parser.add_argument(
        "--approval-mode",
        choices=("ask", "deny"),
        default="ask",
        help="Dangerous-command approval mode for the stdio frontend.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume the active session's checkpoint (stdio frontend, no goal).",
    )
    parser.add_argument(
        "--session-op",
        choices=(
            "status",
            "list",
            "history",
            "create",
            "new",
            "launch",
            "switch",
            "delete",
            "reset",
        ),
        help="Run one headless session operation, print JSON, exit.",
    )
    parser.add_argument(
        "--session-name",
        help="Session name for --session-op create/switch/delete/history.",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        help="Most events --session-op history returns (default 500, max 5000).",
    )
    parser.add_argument(
        "--history-before",
        type=int,
        help="Only events with a smaller seq than this (pages back through history).",
    )
    parser.add_argument(
        "--config-info",
        action="store_true",
        help="Print the saved run configuration as JSON and exit (no LLM call).",
    )
    parser.add_argument(
        "--journal-op",
        choices=("changes", "revert"),
        help="Run one headless change-journal operation, print JSON, exit.",
    )
    parser.add_argument(
        "--commit-message",
        action="store_true",
        help="Generate a commit message from a JSON change payload on stdin, "
        "print JSON, exit.",
    )
    parser.add_argument(
        "--config-op",
        choices=("add-custom-provider", "delete-custom-provider", "set-provider"),
        help="Run one headless config operation, print JSON, exit (no LLM call).",
    )
    parser.add_argument(
        "--config-payload",
        help="JSON payload for --config-op. Omit to read it from stdin (used so "
        "an API key never lands in argv).",
    )
    return parser


# Reject invalid invocations and serve --help before constructing terminal UI
# objects or importing provider SDKs. Imported callers (including tests and the
# credential-free PTY fixture) still parse their current argv inside main().
_DIRECT_CLI_ARGS = (
    _build_cli_parser().parse_args()
    if __name__ == "__main__"
    else None
)


def _run_session_op(args: argparse.Namespace) -> None:
    import json

    from durable_json import JsonStorageError
    from redaction import sanitize_display_text
    from session_store import run_session_op

    try:
        payload = run_session_op(
            args.workspace,
            args.session_op,
            args.session_name,
            limit=args.history_limit,
            before=args.history_before,
        )
    except (ValueError, JsonStorageError, OSError) as error:
        failure = {
            "ok": False,
            "error": sanitize_display_text(str(error).strip() or type(error).__name__),
        }
        print(json.dumps(failure, ensure_ascii=False))
        raise SystemExit(1) from error
    print(json.dumps(payload, ensure_ascii=False))


def _run_commit_message_op() -> None:
    import json
    from contextlib import redirect_stdout

    from commit_message import CommitMessageError, generate_commit_message
    from redaction import sanitize_display_text

    # The IDE writes UTF-8 whatever the console code page is (a Windows build
    # would otherwise decode stdin as cp1252), and the ASCII-escaped reply is
    # immune to the same mismatch on the way back.
    payload_text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        # stdout is the JSON reply the IDE parses; anything a provider SDK
        # prints on the way must not corrupt it.
        with redirect_stdout(sys.stderr):
            message = generate_commit_message(payload_text)
    except (CommitMessageError, RuntimeError, OSError) as error:
        failure = {
            "ok": False,
            "error": sanitize_display_text(str(error).strip() or type(error).__name__),
        }
        print(json.dumps(failure))
        raise SystemExit(1) from error
    print(json.dumps({"ok": True, "message": message}))


def _run_set_provider_op(raw: object) -> dict:
    """Save a provider and its API key non-interactively (a frontend's first-run setup).

    Reuses ``ensure_provider_credentials`` — the same persistence and OpenAI-key
    validation the interactive terminal wizard uses — with the secret supplied
    up front instead of prompted, so an event-driven frontend can offer its own
    setup screen without the core ever needing a TTY or an extra dialog round trip.
    """
    from config_info import build_config_info

    provider = str(raw.get("provider", "")).strip() if isinstance(raw, dict) else ""
    api_key = str(raw.get("api_key", "")).strip() if isinstance(raw, dict) else ""
    # Only asked for a router/gateway provider (OpenRouter, OmniRoute, AnyModel, the
    # manual tier) that has no default model list — the same one id is used for all
    # three roles, matching the simplest choice a person would make by hand anyway.
    model = str(raw.get("model", "")).strip() if isinstance(raw, dict) else ""
    if not provider:
        raise CustomProviderError("A provider id is required.")

    normalized = normalize_provider(provider)
    if is_custom_provider(normalized):
        raise CustomProviderError("Use --config-op add-custom-provider for a custom provider.")

    models, warnings = resolve_models_for_provider(normalized, {})
    missing_roles = [role for role in MODEL_ROLES if not models.get(role)]
    if missing_roles:
        if not model:
            raise CustomProviderError(
                f"{provider_label(normalized)} has no default models; a model id is required."
            )
        models = {role: model for role in MODEL_ROLES}

    required = required_secret_keys_for_provider(normalized, list(models.values()))
    ensure_provider_credentials(
        normalized,
        models.get("fast", ""),
        required_keys=required,
        prompt_secret_fn=lambda _label: api_key,
    )
    if missing_roles:
        save_models_for_provider(normalized, models)
    return {
        "ok": True,
        "provider": normalized,
        "models": models,
        "warnings": warnings,
        "info": build_config_info(),
    }


if _DIRECT_CLI_ARGS is not None and _DIRECT_CLI_ARGS.commit_message:
    _run_commit_message_op()
    raise SystemExit(0)


# The GUI spawns a session op per switch and per sidebar project. Importing the
# provider SDKs and terminal UI below first made each one ~1 s instead of ~50 ms.
if _DIRECT_CLI_ARGS is not None and _DIRECT_CLI_ARGS.session_op:
    _run_session_op(_DIRECT_CLI_ARGS)
    raise SystemExit(0)


from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.output import ColorDepth
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import banner
from agent import AgentConfig, LocalAgent
from event_stdio import CommandContext, CommandHandler, CommandResult
from executor import SafeExecutor
from interactive_screen import InteractiveScreen
from language_utils import choose_localized_text, detect_request_language
from llm_clients import (
    create_llm_client,
    configured_freeflow_backends,
    ANTHROPIC_PROVIDER,
    CUSTOM_PROVIDER_PREFIX,
    CUSTOM_PROVIDER_PLACEHOLDER,
    DEFAULT_BLANK_START_PROVIDER,
    FREEFLOW_PROVIDER,
    get_model_options,
    is_custom_provider,
    extract_custom_provider_name,
    MANUAL_OPENAI_PROVIDERS,
    REGIONAL_BASE_URL_CANDIDATES,
    race_base_urls,
    MODEL_ROLES,
    OPENAI_API_KEY_KEY,
    OPENAI_PROVIDER,
    OMNIROUTE_PROVIDER,
    OPENROUTER_PROVIDER,
    provider_supports_streaming,
    SUPPORTED_LLM_PROVIDERS,
    fetch_available_models,
    normalize_provider,
    provider_label,
    required_secret_keys_for_provider,
    resolve_accessible_models,
    resolve_models_for_provider,
    validate_model_selection,
    validate_omniroute_model_id,
)
from redaction import sanitize_display_text
from custom_provider import (
    CustomProviderError,
    normalize_custom_provider_payload,
    validate_provider_name,
)
from system_context import ensure_workspace_instructions
from ui import AgentUI
from terminal_banner import TerminalBannerUI
from terminal_image import print_exit_logo
from anymodel_onboarding import offer_anymodel_registration
from performance_logger import PerfLogger
from agent_logging import (
    delete_session as delete_agent_session,
    is_valid_session_name,
    list_sessions,
)
from user_config import (
    ensure_provider_credentials,
    get_custom_providers,
    get_custom_provider_by_name,
    save_custom_provider,
    delete_custom_provider,
    clear_saved_provider,
    reset_user_config,
    get_saved_models,
    get_saved_provider,
    get_saved_provider_or_none,
    load_available_credentials,
    prompt_provider_choice,
    prompt_secret,
    save_models_for_provider,
    get_saved_role,
    get_saved_frontend,
    save_frontend,
    save_role_selection,
    get_saved_planning_enabled,
    save_planning_enabled,
    get_saved_reasoning_effort,
    save_reasoning_effort,
    get_saved_style,
    get_style_options,
    save_style_configuration,
    secret_label_for_key,
    set_provider_secret,
)
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.shortcuts import CompleteStyle

from api_settings import (
    API_SETTINGS,
    ApiSetting,
    active_env_override,
    apply_api_settings,
    default_api_settings,
    dynamic_model_loading_enabled,
    format_value,
    load_api_settings,
    parse_number,
    range_hint,
    setting_for,
    store_api_settings,
)
from context_budget import derived_prompt_budget
from reasoning_effort import (
    DEFAULT_EFFORT,
    EFFORT_DESCRIPTIONS,
    EFFORT_LEVELS,
    effort_advisory,
    normalize_effort,
)
from slash_commands import (
    SLASH_COMMAND_KEY_BINDINGS,
    SLASH_COMMAND_LEXER,
    SLASH_COMMAND_MENU_HEIGHT,
    SLASH_COMMAND_PROMPT,
    SLASH_COMMAND_PROMPT_CONTINUATION,
    SLASH_COMMAND_STYLE,
    SlashCommandAutoSuggest,
    SlashCommandCompleter,
    has_slash_command_completion,
    normalize_slash_command,
)

console = Console()
slash_command_auto_suggest = SlashCommandAutoSuggest(
    fallback=AutoSuggestFromHistory()
)

_ROLE_HINTS = {
    "fast": "Used for routing, planning, chat, and simple steps — pick something quick and cheap.",
    "smart": "Used for complex coder/devops steps and error recovery — pick your most capable model.",
    "planner": "Saved alongside fast/smart; usually fine to match the Smart model.",
}


def _display_text(value: object, style: str = "") -> Text:
    return Text(sanitize_display_text(str(value)), style=style)


def _print_model_row(label: str, model: str) -> None:
    line = Text("  ")
    line.append(f"{sanitize_display_text(label):8s}", style="cyan")
    line.append(" ")
    if model:
        line.append(sanitize_display_text(model))
    else:
        line.append("not set", style="red")
    console.print(line)


def slash_completion_is_active() -> bool:
    return has_slash_command_completion(_get_session().default_buffer.text)


_session: PromptSession | None = None


def _get_session() -> PromptSession:
    """Build the interactive PromptSession lazily.

    Constructing it at import time calls prompt_toolkit's console detection,
    which raises on hosts with no attached console (e.g. headless CI runners
    importing this module for its other symbols) — so it's built on first
    real use instead.
    """
    global _session
    if _session is None:
        _session = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCommandCompleter(),
            complete_while_typing=Condition(slash_completion_is_active),
            complete_style=CompleteStyle.COLUMN,
            # One row for the raised input plus one row per command below it.
            reserve_space_for_menu=SLASH_COMMAND_MENU_HEIGHT + 1,
            auto_suggest=slash_command_auto_suggest,
            key_bindings=SLASH_COMMAND_KEY_BINDINGS,
            lexer=SLASH_COMMAND_LEXER,
            style=SLASH_COMMAND_STYLE,
            color_depth=ColorDepth.TRUE_COLOR,
        )
    return _session


def _documents_directory() -> Path:
    """Return the user's standard documents directory on the current OS."""
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"

    # Respect a localized XDG documents directory when it is configured.
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    user_dirs = config_home / "user-dirs.dirs"
    try:
        for line in user_dirs.read_text(encoding="utf-8").splitlines():
            if line.startswith("XDG_DOCUMENTS_DIR="):
                value = line.partition("=")[2].strip().strip('"')
                if value.startswith("$HOME/"):
                    return Path.home() / value[6:]
                if value:
                    return Path(value).expanduser()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise RuntimeError(f"Cannot read user directories configuration: {error}") from error
    return Path.home() / "Documents"


def default_workspace_path() -> Path:
    return _documents_directory() / "Kvist Workspace"


def resolve_workspace_path(raw_path: str | None = None) -> Path:
    candidate = (
        Path(raw_path).expanduser().resolve()
        if raw_path and raw_path.strip()
        else default_workspace_path().expanduser().resolve()
    )
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.exists():
        raise RuntimeError("Workspace path does not exist.")
    if not candidate.is_dir():
        raise RuntimeError("Workspace path must be a directory.")
    return candidate


def multiline_input() -> str:
    """Read user input using prompt_toolkit for a better multiline experience."""
    # Use a condition to enable multiline mode only when there's a newline in the input
    is_multiline = Condition(lambda: "\n" in _get_session().default_buffer.text)
    return _get_session().prompt(
        SLASH_COMMAND_PROMPT,
        multiline=is_multiline,
        prompt_continuation=SLASH_COMMAND_PROMPT_CONTINUATION,
    ).strip()


def restore_input_draft(interactive_screen, text: str) -> None:
    """Put a rejected or cancelled prompt back into the input field.

    The user typed it once; losing it to a length rejection or a cancelled
    turn means retyping. Only the live screen can hold a draft, so the plain
    fallback interface silently skips it.
    """
    if interactive_screen is None or not text or text.startswith("/"):
        return
    try:
        interactive_screen.set_input_text(text)
    except Exception:
        # A draft is a convenience; never let restoring one break the loop.
        pass


def sleep(seconds: int):
    time.sleep(seconds)


# Minimum prompt length before the conversation has real turns behind it, and
# after. The gate answers one question: is there anything for the agent to
# answer at all? On a cold conversation a one-character prompt is a typo or a
# liveness check. Once turns exist, a short reply ("зачем?", "why?") is a
# follow-up on them and its length says nothing about its worth.
_MIN_PROMPT_LENGTH = 8
_MIN_FOLLOWUP_PROMPT_LENGTH = 4
_FOLLOWUP_TURN_THRESHOLD = 2


def minimum_prompt_length(completed_turns: int) -> int:
    if completed_turns >= _FOLLOWUP_TURN_THRESHOLD:
        return _MIN_FOLLOWUP_PROMPT_LENGTH
    return _MIN_PROMPT_LENGTH


def validate_user_goal(
    user_goal: str,
    language_hint: str = "en",
    completed_turns: int = 0,
) -> str | None:
    """Return an error message if the prompt is invalid, or None if acceptable.

    ``completed_turns`` counts agent runs finished in *this process*. It must
    not be derived from the persisted session history: that history is restored
    from disk during ``LocalAgent.__init__``, so any workspace used once would
    disable the gate permanently and a bare "." would reach the model on a
    conversation that has no context to answer it from.
    """
    stripped = user_goal.strip()

    if not stripped:
        return choose_localized_text(
            ru_text='Опиши задачу подробнее. Например: "Исправь ошибку в main.py" или "Создай parser.py для CSV".',
            en_text='Please describe the task more specifically. Example: "Fix the bug in main.py" or "Create parser.py for CSV".',
            language_hint=language_hint,
        )

    # Slash commands are always valid — they are dispatched before the agent runs.
    if stripped.startswith("/"):
        return None

    if len(stripped) < minimum_prompt_length(completed_turns):
        return choose_localized_text(
            ru_text='Опиши задачу подробнее. Например: "Исправь ошибку в main.py" или "Создай parser.py для CSV".',
            en_text='Please describe the task more specifically. Example: "Fix the bug in main.py" or "Create parser.py for CSV".',
            language_hint=language_hint,
        )

    return None


def parse_verbose_value(raw: str, current: bool) -> bool | None:
    """Parse /verbose, accepting true/false with optional angle brackets."""

    parts = raw.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "/verbose":
        return None
    if len(parts) == 1:
        return not current

    value = parts[1].strip().casefold()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parse_plan_value(raw: str, current: bool) -> bool | None:
    """Parse /plan, accepting true/false with optional angle brackets.

    ``current`` and the return value are "planning enabled": ``/plan true``
    keeps the planning stage, ``/plan false`` skips it. A bare ``/plan`` flips.
    """

    parts = raw.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "/plan":
        return None
    if len(parts) == 1:
        return not current

    value = parts[1].strip().casefold()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parse_tui_value(raw: str, current: bool) -> bool | None:
    parts = raw.split()
    if not parts or parts[0].casefold() != "/tui" or len(parts) > 2:
        return None
    if len(parts) == 1:
        return not current
    value = parts[1].strip("<>").casefold()
    if value in {"true", "on", "1"}:
        return True
    if value in {"false", "off", "0"}:
        return False
    return None


# PyInstaller unpacks bundled data under sys._MEIPASS; from source it sits
# next to this file. Either way the Ink UI is the single file `npm run build` makes.
_APP_ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
_TUI_ENTRY = _APP_ROOT / "dist-ui" / "kvist-tui.mjs"
# Exit code the Ink app uses for "/tui": the user chose another interface. Which
# one travels in the file named by KVIST_SWITCH_FILE, so the code stays the same
# however many interfaces there are.
_TUI_SWITCH_CODE = 64


def launch_tui(args: argparse.Namespace) -> int | None:
    """Run the Ink frontend (``src/``, bundled to ``dist-ui/kvist-tui.mjs``) with this program as its core.

    Returns the frontend's exit code, or None when it cannot run here so the
    caller falls back to the classic terminal.
    """
    import json
    import shutil
    import subprocess

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    node = shutil.which("node")
    if node is None:
        print(
            "The Ink UI needs Node.js 22 or newer on PATH; using the classic terminal.",
            file=sys.stderr,
        )
        return None
    if not _TUI_ENTRY.is_file():
        rebuild = (
            "this build of Kvist was made without it (Node.js was missing at build "
            "time); reinstall with Node.js available"
            if getattr(sys, "frozen", False)
            else "build it with `npm install && npm run build`"
        )
        print(f"The Ink UI is not built: {rebuild}. Using the classic terminal.", file=sys.stderr)
        return None
    if not (args.provider or os.getenv("AGENT_LLM_PROVIDER") or get_saved_provider_or_none()):
        print(
            "The Ink UI cannot configure a provider yet; run the classic "
            "terminal once (`--frontend terminal`) to set one up.",
            file=sys.stderr,
        )
        return None

    workspace = resolve_workspace_path(args.workspace)
    core = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    command = [
        node, str(_TUI_ENTRY),
        "--workspace", str(workspace),
        "--role", args.role,
        "--approval-mode", args.approval_mode,
    ]
    if args.provider:
        command += ["--provider", args.provider]
    if args.fast_model and args.smart_model and args.planner_model:
        command += [
            "--fast-model", args.fast_model,
            "--smart-model", args.smart_model,
            "--planner-model", args.planner_model,
        ]
    import tempfile

    with tempfile.TemporaryDirectory(prefix="kvist-switch-") as scratch:
        switch_file = Path(scratch) / "frontend"
        env = {**os.environ, "KVIST_CORE_CMD": json.dumps(core), "KVIST_SWITCH_FILE": str(switch_file)}
        try:
            code = subprocess.call(command, env=env)
        except KeyboardInterrupt:
            return 130
        if code != _TUI_SWITCH_CODE:
            return code
        return _switch_frontend(switch_file)


def _switch_frontend(switch_file: Path) -> int | None:
    """Apply the interface the Ink app asked for; None hands over to the classic terminal."""
    try:
        chosen = switch_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        save_frontend(chosen)
    except ValueError:
        print(f"Unknown interface '{chosen}'; using the classic terminal.", file=sys.stderr)
        return None
    # Only the classic terminal can take over from here; another interface
    # would be launched here once it exists.
    return None


def parse_strime_value(raw: str, current: bool) -> bool | None:
    """Parse /strime, accepting true/false with optional angle brackets."""

    parts = raw.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "/strime":
        return None
    if len(parts) == 1:
        return not current

    value = parts[1].strip().casefold()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def resolve_planning_enabled() -> bool:
    """Startup planning-stage flag: AGENT_SKIP_PLANNING overrides the saved
    preference, which defaults to on."""
    env_skip = os.getenv("AGENT_SKIP_PLANNING")
    if env_skip is not None:
        return env_skip not in {"1", "true", "True", "yes", "on"}
    return get_saved_planning_enabled()


def apply_verbose_mode(agent, enabled: bool) -> None:
    """Apply a verbose-mode change to the active runtime objects."""

    agent.config.verbose = enabled
    agent._ui.verbose = enabled
    if hasattr(agent.client, "_verbose"):
        agent.client._verbose = enabled
    slash_command_auto_suggest.set_verbose(enabled)


def apply_plan_mode(agent, planning_enabled: bool) -> None:
    """Apply a planning-stage change to the active runtime objects."""

    agent.config.skip_planning = not planning_enabled
    slash_command_auto_suggest.set_plan(planning_enabled)


def resolve_reasoning_effort() -> str:
    """Startup reasoning-effort level: AGENT_REASONING_EFFORT overrides the
    persisted preference, which defaults to 'medium'."""
    override = os.getenv("AGENT_REASONING_EFFORT")
    if override and override.strip():
        return normalize_effort(override)
    return normalize_effort(get_saved_reasoning_effort())


def apply_reasoning_effort(agent, config: AgentConfig, level: str) -> None:
    """Apply a reasoning-effort change to the live runtime objects."""
    normalized = normalize_effort(level)
    agent.config.reasoning_effort = normalized
    config.reasoning_effort = normalized


def sync_strime_suggestion(provider: str, enabled: bool) -> None:
    slash_command_auto_suggest.set_streaming(
        enabled,
        available=is_custom_provider(provider),
    )


def apply_strime_mode(agent, enabled: bool) -> bool:
    """Persist and apply streaming mode for the active custom provider."""

    provider = normalize_provider(agent.config.provider)
    if not is_custom_provider(provider):
        return False

    name = extract_custom_provider_name(provider)
    info = get_custom_provider_by_name(name)
    if info is None:
        return False

    info["streaming"] = enabled
    save_custom_provider(name, info)
    agent.config.stream = enabled
    sync_strime_suggestion(provider, enabled)
    return True


@contextmanager
def wizard_screen():
    """Run a whole provider/model wizard inside the alternate screen buffer.

    The alternate buffer captures every line the wizard prints (menus, plain
    input()/getpass() prompts, errors). Leaving it restores the previous
    screen — banner and prompt history — exactly as they were, so the wizard
    leaves no trace. Outside a TTY it is a no-op.
    """
    use_alt = sys.stdin.isatty() and console.is_terminal and not console.is_alt_screen
    if use_alt:
        console.set_alt_screen(True)
        console.clear()
    try:
        yield
    finally:
        if use_alt:
            console.set_alt_screen(False)


def request_cancelled_text(language: str) -> str:
    return "Запрос отменён." if language == "ru" else "Request cancelled."


def run_stopped_text(language: str) -> str:
    return (
        "Работа агента остановлена вами."
        if language == "ru"
        else "Agent run stopped by you."
    )


def release_terminal(
    screen: InteractiveScreen | None,
    use_session_screen: bool,
) -> None:
    """Hand the terminal back, absorbing one late Ctrl+C.

    Teardown is the last chance to leave the alternate screen and restore the
    process streams.  An interrupt arriving here must not abandon that work,
    or the user is returned to a shell drawing on the alternate buffer.
    """
    try:
        _restore_terminal(screen, use_session_screen)
    except KeyboardInterrupt:
        _restore_terminal(screen, use_session_screen)


def _restore_terminal(
    screen: InteractiveScreen | None,
    use_session_screen: bool,
) -> None:
    if screen is not None:
        screen.stop()
    elif use_session_screen:
        console.set_alt_screen(False)


def save_session_for_shutdown(agent: LocalAgent) -> None:
    """Persist the session on the way out without derailing shutdown.

    Both ``/bye`` and Ctrl+C at the prompt end here, so the session is saved
    the same way on either route.  A second Ctrl+C can land inside the durable
    write; that write stays atomic and the previously saved generation remains
    readable, so shutdown reports the interruption and continues rather than
    printing a traceback over a terminal that is already being torn down.
    """
    try:
        agent.save_session()
    except KeyboardInterrupt:
        console.print(
            _display_text(
                "Session save was interrupted; the last saved session is kept.",
                "yellow",
            )
        )
    except Exception as error:
        safe_error = sanitize_display_text(
            str(error).strip() or type(error).__name__
        )
        console.print(
            _display_text(
                f"Session save failed safely: {safe_error}. "
                "The last saved session is kept.",
                "yellow",
            )
        )


@contextmanager
def paused_interactive_screen(screen: InteractiveScreen | None):
    """Temporarily return terminal ownership to an existing Rich/input UI."""
    if screen is not None:
        screen.suspend()
    try:
        yield
    finally:
        if screen is not None:
            screen.resume()


def _anymodel_key_available() -> bool:
    """Is the AnyModel key already reachable (env var or saved config)?"""
    api_key_key = MANUAL_OPENAI_PROVIDERS[DEFAULT_BLANK_START_PROVIDER]["api_key_key"]
    credentials = load_available_credentials((api_key_key,))
    return bool(credentials.get(api_key_key))


def resolve_startup_provider(
    provider_override: str | None = None,
    interactive_ui: AgentUI | None = None,
) -> str:
    if provider_override:
        return normalize_provider(provider_override)

    env_provider = os.getenv("AGENT_LLM_PROVIDER")
    if env_provider:
        return normalize_provider(env_provider)

    saved_provider = get_saved_provider_or_none()
    if saved_provider:
        return saved_provider

    # Blank start: nothing saved yet. AnyModel ships as the default — if its
    # key is already reachable (env var, or a config partially written by a
    # prior run), use it directly instead of asking about the other five
    # built-in providers.
    if _anymodel_key_available():
        return DEFAULT_BLANK_START_PROVIDER

    if interactive_ui is not None:
        # An event frontend has no browser/OAuth registration screen; the
        # picker lists AnyModel like any other provider.
        selected = choose_provider("openai", False, ui=interactive_ui)
        if selected:
            return selected
        raise RuntimeError(
            "No provider was selected. / Провайдер не выбран."
        )

    # First run, no AnyModel key either: use RichUI if available, fall back
    # to plain-text. The AnyModel registration screen is offered first (it
    # is the shipped default); Esc there falls through to the generic
    # picker, with AnyModel excluded since it was just offered separately.
    if sys.stdin.isatty():
        registration_ui = TerminalBannerUI(console=console, verbose=False)
        selected = offer_anymodel_registration(registration_ui)
        if selected:
            return selected
        selected = choose_provider(
            "openai", False, exclude=frozenset({DEFAULT_BLANK_START_PROVIDER})
        )
        if selected:
            return selected
    return prompt_provider_choice()


def build_agent(
    executor: SafeExecutor,
    max_steps: int,
    persist_logs: bool,
    verbose: bool,
    provider_override: str | None = None,
    models_override: dict[str, str] | None = None,
    language_hint: str | None = None,
    ui: AgentUI | None = None,
    skip_planning: bool = False,
    interactive_ui: AgentUI | None = None,
) -> tuple[LocalAgent, AgentConfig, list[str]]:
    # ``interactive_ui`` is how an event frontend answers what the terminal
    # answers through a TTY (provider, models, base URL, secrets). Without a
    # TTY and without it, those questions fail instead of hanging.
    can_ask = interactive_ui is not None or sys.stdin.isatty()
    # Captured before the missing-models wizard may rebind the local ``ui``.
    provided_ui = ui
    provider = resolve_startup_provider(provider_override, interactive_ui)

    # For custom providers, load models from the custom provider config
    if is_custom_provider(provider):
        if models_override:
            saved_models = models_override
        else:
            name = extract_custom_provider_name(provider)
            info = get_custom_provider_by_name(name)
            saved_models = info.get("models", {}) if info else {}
    else:
        saved_models = models_override or get_saved_models(provider)

    models, warnings = resolve_models_for_provider(
        provider,
        saved_models,
        language_hint=language_hint,
    )

    # For OmniRoute, OpenRouter, manual-tier and custom providers, check if models are missing
    if (
        provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER)
        or provider in MANUAL_OPENAI_PROVIDERS
        or is_custom_provider(provider)
    ):
        missing_roles = [role for role in MODEL_ROLES if role not in models or not models[role]]
        if missing_roles:
            provider_display = provider_label(provider)
            if provider == OPENROUTER_PROVIDER:
                example_model = "openai/gpt-5.4-mini"
            elif provider in MANUAL_OPENAI_PROVIDERS:
                example_model = "the exact model id from the provider's documentation"
            else:
                example_model = "kr/claude-sonnet-4.5"
            if not can_ask:
                raise RuntimeError(
                    f"{provider_display} models are not configured. Run Kvist in an interactive "
                    f"terminal and set exact model ids with /models, for example {example_model}."
                )
            # Interactive setup if models are missing
            ui = interactive_ui or TerminalBannerUI(console=console, verbose=verbose)
            console.print(
                _display_text(
                    f"{provider_display} models are not configured.",
                    "yellow",
                )
            )
            if provider == OPENROUTER_PROVIDER:
                selected_models = configure_openrouter_models_interactively(ui, models)
            elif is_custom_provider(provider):
                selected_models = configure_custom_provider_models_interactively(ui, models)
            elif provider in MANUAL_OPENAI_PROVIDERS:
                selected_models = configure_manual_provider_models_interactively(
                    ui, models, provider
                )
            else:
                selected_models = configure_omniroute_models_interactively(ui, models)
            if selected_models is None:
                raise RuntimeError(f"{provider_display} model configuration was cancelled.")
            still_missing = [role for role in MODEL_ROLES if not selected_models.get(role)]
            if still_missing:
                raise RuntimeError(
                    f"{provider_display} has no model configured for: "
                    f"{', '.join(still_missing)}. Set exact model ids with /models, "
                    f"for example {example_model}."
                )
            if is_custom_provider(provider):
                # Save models back to custom provider config
                name = extract_custom_provider_name(provider)
                info = get_custom_provider_by_name(name) or {}
                info["models"] = selected_models
                save_custom_provider(name, info)
            else:
                save_models_for_provider(provider, selected_models)
            models = selected_models
            console.print(
                _display_text(f"{provider_display} models saved.", "green")
            )

    if provider in MANUAL_OPENAI_PROVIDERS and can_ask:
        manual_config = MANUAL_OPENAI_PROVIDERS[provider]
        api_key_key = manual_config["api_key_key"]
        base_url_key = manual_config["base_url_key"]
        existing = load_available_credentials((api_key_key, base_url_key))
        regional_candidates = REGIONAL_BASE_URL_CANDIDATES.get(provider)

        if not existing.get(api_key_key) and regional_candidates and not existing.get(base_url_key):
            # Several of these vendors are region-sharded (Xiaomi Token
            # Plan: cn/sgp/ams; Qwen: intl/mainland; Moonshot/MiniMax/
            # SiliconFlow: .com/.cn) and a key issued for one region gets a
            # plain 401 on another region's endpoint — indistinguishable
            # from a wrong key (a real case reported in Sep 2026). Racing on
            # raw reachability alone doesn't fix that: a fast-but-wrong
            # region is usually still up and answers something. So the key
            # is collected up front and the race is authenticated — a
            # 401/403 excludes that region outright regardless of speed;
            # see race_base_urls in llm_clients.py.
            secret_ui = interactive_ui or TerminalBannerUI(console=console, verbose=verbose)
            api_key_label = secret_label_for_key(api_key_key)
            entered_key = secret_ui.prompt_secret(
                title=api_key_label,
                help_text="Paste or type the value, then press Enter.",
            )
            if not entered_key:
                raise RuntimeError(f"{api_key_label} is required for {provider_label(provider)}.")

            console.print(
                _display_text(
                    f"Testing {len(regional_candidates)} {provider_label(provider)} "
                    "regions with this key...",
                    "dim",
                )
            )
            winner, race_results = race_base_urls(regional_candidates, entered_key)
            if winner is None:
                if all(item["status"] == "unauthorized" for item in race_results.values()):
                    raise RuntimeError(
                        f"{provider_label(provider)} rejected this API key in every known "
                        "region. Double-check it was copied correctly."
                    )
                console.print(
                    _display_text(
                        f"No {provider_label(provider)} endpoint responded within a few "
                        "seconds — check your internet connection, or run /provider "
                        "again to pick a different provider.",
                        "red",
                    )
                )
                raise RuntimeError(
                    f"Could not reach any {provider_label(provider)} regional endpoint "
                    f"(tried: {', '.join(regional_candidates)})."
                )
            console.print(
                _display_text(
                    f"Fastest region that accepted this key: {winner} "
                    f"({race_results[winner]['elapsed'] * 1000:.0f} ms).",
                    "green",
                )
            )
            set_provider_secret(api_key_key, entered_key)
            set_provider_secret(base_url_key, winner)
        elif not existing.get(api_key_key):
            # Single-endpoint provider (or a base URL override is already
            # saved): plain confirm/edit, no race needed. The key itself is
            # still collected afterward by ensure_provider_credentials.
            base_url_ui = interactive_ui or TerminalBannerUI(console=console, verbose=verbose)
            chosen_base_url = base_url_ui.prompt_text(
                title=f"Base URL for {provider_label(provider)}",
                current_value=existing.get(base_url_key) or manual_config["default_base_url"],
                help_text=(
                    "Confirm, or edit if your account/key uses a different "
                    "region or endpoint. Press Enter to accept."
                ),
            )
            if chosen_base_url and chosen_base_url.strip():
                set_provider_secret(base_url_key, chosen_base_url.strip())

    required_keys = required_secret_keys_for_provider(
        provider,
        [models["fast"], models["smart"], models["planner"]],
    )

    def _prompt_secret_via_ui(label: str) -> str:
        # ensure_provider_credentials otherwise falls back to a bare
        # getpass() call with no visible feedback at all (not even
        # asterisks) — this gives first-time key entry the same masked
        # Rich/prompt_toolkit field that /change-api already uses. Without a
        # real TTY, keep the original stdin-pipe fallback (run_task.py /
        # scripted use can still pipe a secret in) rather than the UI, which
        # would just return nothing.
        if interactive_ui is None and not sys.stdin.isatty():
            return prompt_secret(label)
        secret_ui = interactive_ui or TerminalBannerUI(console=console, verbose=verbose)
        return secret_ui.prompt_secret(
            title=label,
            help_text="Paste or type the value, then press Enter.",
        ) or ""

    credentials = ensure_provider_credentials(
        provider=provider,
        validation_model=models["fast"],
        required_keys=required_keys,
        language_hint=language_hint,
        skip_validation=(provider == OPENAI_PROVIDER),
        prompt_secret_fn=_prompt_secret_via_ui,
    )
    if provider == FREEFLOW_PROVIDER:
        credentials.update(
            load_available_credentials(("gemini_api_key", "groq_api_key"))
        )
    # Discover the live model catalog from the provider's /v1/models endpoint
    # on every launch, then reconcile configured models against it. Discovery
    # is best-effort: on failure we keep configured/static models and warn.
    # /settings-api can turn it off entirely, for a gateway that has no
    # catalog endpoint or a user who does not want the extra launch call.
    if not dynamic_model_loading_enabled():
        warnings.append(
            "Dynamic model loading is off: using the configured models without "
            "asking the provider. Re-enable it with /settings-api."
        )
    else:
        live_models, discovery_error = fetch_available_models(provider, credentials)
        if discovery_error:
            warnings.append(discovery_error)
        else:
            models, access_warnings = resolve_accessible_models(
                provider,
                models,
                live_models,
                language_hint=language_hint,
            )
            warnings.extend(access_warnings)
    if provider == FREEFLOW_PROVIDER:
        warnings.append(
            "For FreeFlow LLM, streaming is disabled: the library handles stream responses unstably."
        )
        freeflow_backends = configured_freeflow_backends(credentials)
        if len(freeflow_backends) <= 1:
            warnings.append(
                "FreeFlow fallback across providers is unavailable: configure both Gemini and Groq keys."
            )
    elif is_custom_provider(provider) and not provider_supports_streaming(provider):
        warnings.append(
            "Streaming is disabled for this custom provider. Use /strime to enable it."
        )
    client = create_llm_client(provider=provider, credentials=credentials, verbose=verbose)
    config = AgentConfig(
        provider=provider,
        fast_model=models["fast"],
        smart_model=models["smart"],
        planner_model=models["planner"],
        max_steps=max_steps,
        persist_logs=persist_logs,
        stream=provider_supports_streaming(provider),
        verbose=verbose,
        skip_planning=skip_planning,
        reasoning_effort=resolve_reasoning_effort(),
    )
    apply_api_settings(config)
    agent_console = Console()
    perf_logger = PerfLogger(executor.workspace)
    agent_ui = (
        provided_ui
        if provided_ui is not None
        else TerminalBannerUI(console=agent_console, verbose=verbose, perf_logger=perf_logger)
    )
    agent = LocalAgent(
        client=client,
        executor=executor,
        config=config,
        ui=agent_ui,
        console=agent_console,
        perf_logger=perf_logger,
    )
    warnings.extend(getattr(agent, "_storage_warnings", []))
    effort_note = effort_advisory(config.provider, config.reasoning_effort)
    if effort_note and config.reasoning_effort != DEFAULT_EFFORT:
        warnings.append(f"Reasoning effort '{config.reasoning_effort}': {effort_note}")
    try:
        ensure_workspace_instructions(executor.workspace)
    except OSError as error:
        warnings.append(
            f"Could not prepare KVIST.md / KVIST-CHAT.md in the workspace: {error}"
        )
    return agent, config, warnings


def print_runtime_banner(config: AgentConfig, max_steps: int, verbose: bool, warnings: list[str]) -> None:
    line = Text()
    line.append(
        f"Provider: {sanitize_display_text(provider_label(config.provider))}",
        style="magenta",
    )
    line.append(" | ")
    line.append(
        f"Fast model: {sanitize_display_text(config.fast_model)}",
        style="green",
    )
    line.append(" | ")
    line.append(
        f"Smart model: {sanitize_display_text(config.smart_model)}",
        style="bold yellow",
    )
    line.append(" | ")
    line.append(
        f"Planner model: {sanitize_display_text(config.planner_model)}",
        style="blue",
    )
    line.append(
        f" | Max steps: {max_steps} | Effort: {config.reasoning_effort}"
        f" | Verbose: {verbose}"
    )
    console.print(line)
    for warning in warnings:
        console.print(_display_text(warning, "yellow"))


def render_exit_message(agent: LocalAgent) -> None:
    """Print a short stop banner. Called after leaving the session's alt screen.

    The whole interactive session runs on the terminal's alternate screen
    buffer (see `main()`), so leaving it already restores whatever was on the
    real screen before the agent started — this just appends a brief note
    below that restored content instead of leaving the full chat transcript.
    """
    session_name = getattr(agent, "current_session", "default")
    console.file.flush()
    print_exit_logo()
    console.print("[dim]Kvist agent[/dim] [red]stopped.[/red]")
    message = Text(
        "To continue, start the agent and resume session ",
        style="dim",
    )
    message.append(sanitize_display_text(str(session_name)), style="bold dim")
    message.append(".", style="dim")
    console.print(message)


def render_new_session_notice(name: str) -> None:
    """Mark the start of a new session right above the input field."""
    line = Text("── New session: ", style="green")
    line.append(sanitize_display_text(name), style="bold green")
    line.append(" — the previous one is kept in /session ──", style="green")
    console.print(line)


def render_startup_banner(
    agent: LocalAgent,
    config: AgentConfig,
    max_steps: int,
    verbose: bool,
    warnings: list[str],
) -> None:
    """Clear the session's alternate screen and draw the agent banner."""
    console.clear()

    console.print("[dim]Local AI agent [/dim]Kvist [dim]started. [/dim]")
    console.print(
        _display_text(f"Workspace: {agent.executor.workspace}", "dim")
    )
    print_runtime_banner(config, max_steps, verbose, warnings)
    console.print("[red]Type '/bye' to exit. For multiline input, use Alt+Enter or Esc+Enter.[/red]")
    console.print("[dim]Command '/resume' — resume an interrupted task.[/dim]")
    console.print("[dim]Command '/provider' — switch the LLM provider.[/dim]")
    console.print("[dim]Command '/change-api' — update the current provider's API key.[/dim]")
    console.print("[dim]Command '/models' — switch models for the current provider.[/dim]")
    console.print("[dim]Command '/role' — select agent role (auto/coder/reader/devops/chat).[/dim]")
    console.print("[dim]Command '/effort' — set reasoning effort (false/low/medium/high/xhigh/max).[/dim]")
    console.print("[dim]Command '/tui' — choose the Ink terminal UI or the classic one for the next launch.[/dim]")
    console.print("[dim]Command '/session' — manage and switch sessions.[/dim]")
    console.print("[dim]Command '/new' — start a new session (the current one is kept).[/dim]")
    console.print("[dim]Command '/verbose' — enable or disable verbose output.[/dim]")
    console.print("[dim]Command '/strime' — toggle custom-provider streaming.[/dim]")
    console.print("[dim]Command /about — to get more info about agent.[/dim]")
    console.print("[dim]Enter your task.[/dim]")

    session_name = getattr(agent, "current_session", "default")
    sessions = agent._list_sessions()
    session_line = Text("Session: ", style="dim")
    session_line.append(sanitize_display_text(str(session_name)), style="bold dim")
    session_line.append(f" ({len(sessions)} saved)", style="dim")
    console.print(session_line)
    history = getattr(agent, "_session_history", [])
    if history:
        console.print("[dim]── Previous in this session ──[/dim]")
        for entry in history[-3:]:
            role_color = {"coder": "green", "reader": "yellow", "devops": "magenta", "chat": "cyan"}.get(entry.get("role", ""), "white")
            history_line = Text("  ")
            history_line.append(
                sanitize_display_text(str(entry.get("role", "?"))),
                style=role_color,
            )
            history_line.append(
                " " + sanitize_display_text(str(entry.get("goal", ""))[:100])
            )
            console.print(history_line)

    saved_role = get_saved_role()
    if saved_role and saved_role != "auto":
        role_colors = {"coder": "green", "reader": "yellow", "devops": "magenta", "chat": "cyan"}
        role_color = role_colors.get(saved_role, "white")
        console.print(
            _display_text(
                f"Current role: {saved_role} "
                "(manual — auto-detection disabled)",
                role_color,
            )
        )
    else:
        console.print("[dim]Current role: auto (automatic detection enabled)[/dim]")


def choose_provider(
    current_provider: str,
    verbose: bool,
    exclude: frozenset[str] | None = None,
    ui: AgentUI | None = None,
) -> str | None:
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)

    # Built-in providers in order: OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute
    options: list[dict[str, str]] = [
        {
            "value": "openai",
            "label": "OpenAI",
            "description": "Strict JSON mode with GPT models",
        },
        {
            "value": "anthropic",
            "label": "Anthropic",
            "description": "Native Claude models (Opus, Sonnet, Haiku)",
        },
        {
            "value": "deepseek",
            "label": "DeepSeek",
            "description": "Native DeepSeek models (V4 Pro, V4 Flash)",
        },
        {
            "value": "openrouter",
            "label": "OpenRouter",
            "description": "Public multi-provider gateway (OpenAI, Anthropic, Google, etc.)",
        },
        {
            "value": "omniroute",
            "label": "OmniRoute",
            "description": "Local OpenAI-compatible gateway",
        },
    ]

    # FreeFlow (beta)
    options.append({
        "value": "freeflow",
        "label": "FreeFlow LLM",
        "description": "Gemini/Groq via FreeFlow, JSON — best effort",
    })

    # Third-party OpenAI-compatible providers — one API key, manual model entry
    # (same tier as OmniRoute above; see MANUAL_OPENAI_PROVIDERS in llm_clients.py).
    for slug, config in MANUAL_OPENAI_PROVIDERS.items():
        options.append({
            "value": slug,
            "label": config["label"],
            "description": f"OpenAI-compatible — {config['default_base_url']}",
        })

    # Add saved custom providers with protocol tags
    custom_providers = get_custom_providers()
    for name, info in custom_providers.items():
        provider_id = f"{CUSTOM_PROVIDER_PREFIX}{name}"
        label = info.get("label", name)
        protocol = info.get("protocol", "openai")
        proto_label = "Anthropic" if protocol == "anthropic" else "OpenAI"
        url = info.get("base_url", "")
        if len(url) > 40:
            url = url[:37] + "..."
        options.append({
            "value": provider_id,
            "label": f"{label} ({proto_label})",
            "description": f"Custom — {url}",
        })

    # Add "create new" option
    options.append({
        "value": CUSTOM_PROVIDER_PLACEHOLDER,
        "label": "+ Create custom provider...",
        "description": "Add a new OpenAI- or Anthropic-compatible endpoint",
    })

    if exclude:
        options = [option for option in options if option["value"] not in exclude]

    selected = ui.choose_option(
        title="Select LLM Provider",
        current_value=current_provider,
        options=options,
    )

    if selected == CUSTOM_PROVIDER_PLACEHOLDER:
        new_provider = configure_custom_provider_interactively(ui, verbose)
        if new_provider:
            return new_provider
        return None

    return selected


def change_provider_api_key(
    config: AgentConfig, verbose: bool, ui: AgentUI | None = None
) -> bool:
    """Prompt for and save new API key(s) for the currently active provider.

    Returns True if at least one key was updated, False if the user left every
    prompt empty (kept the current key) or cancelled.
    """
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)
    provider = config.provider

    if is_custom_provider(provider):
        name = extract_custom_provider_name(provider)
        info = get_custom_provider_by_name(name) or {}
        new_key = ui.prompt_secret(
            title=f"New API key for {provider_label(provider)}",
            current_value=info.get("api_key", ""),
            help_text="Leave empty and press Enter to keep the current key.",
        )
        if not new_key:
            return False
        info["api_key"] = new_key
        save_custom_provider(name, info)
        return True

    keys_to_update = required_secret_keys_for_provider(
        provider, [config.fast_model, config.smart_model, config.planner_model]
    )
    if not keys_to_update:
        return False

    updated_any = False
    for key_name in keys_to_update:
        label = secret_label_for_key(key_name)
        existing = load_available_credentials((key_name,)).get(key_name, "")
        new_value = ui.prompt_secret(
            title=f"New {label}",
            current_value=existing,
            help_text="Leave empty and press Enter to keep the current key.",
        )
        if not new_value:
            continue
        set_provider_secret(key_name, new_value)
        updated_any = True
    return updated_any


def choose_session(agent, verbose: bool, ui: AgentUI | None = None) -> str | None:
    """Select or manage sessions via Rich UI."""
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)
    sessions = agent._list_sessions()
    current = getattr(agent, "current_session", "default")

    options: list[dict[str, str]] = []
    for name in sessions:
        prefix = "● " if name == current else "  "
        options.append({
            "value": name,
            "label": f"{prefix}{name}",
            "description": "current session" if name == current else "",
        })
    # Always show current session even if no file exists yet
    if current not in sessions and current != "default":
        options.append({
            "value": current,
            "label": f"● {current}",
            "description": "current session",
        })

    options.append({
        "value": "__new__",
        "label": "+ Create new session...",
        "description": "Start a fresh named session",
    })

    if len(sessions) > 1:
        options.append({
            "value": "__delete__",
            "label": "✕ Delete a session...",
            "description": "Remove a saved session",
        })

    selected = ui.choose_option(
        title=f"Manage Sessions (current: {current})",
        current_value=current,
        options=options,
        help_text="↑/↓ — select, Enter — switch, Esc — cancel",
    )

    if selected is None:
        return None

    if selected == "__new__":
        name = ui.prompt_text(
            title="New session name:",
            current_value="",
            help_text="Only lowercase letters, digits, hyphens, underscores.",
        )
        if name is None or not name.strip():
            return None
        name = name.strip().lower()
        if not is_valid_session_name(name):
            console.print(
                "[yellow]Invalid session name. Use 1-64 lowercase letters, "
                "digits, hyphens, or underscores; start with a letter or digit.[/yellow]"
            )
            return None
        try:
            agent.new_session(name)
        except ValueError as error:
            console.print(_display_text(error, "yellow"))
            return None
        console.print(_display_text(f"Session {name} created.", "green"))
        return name

    if selected == "__delete__":
        delete_options = [{"value": n, "label": n} for n in sessions if n != current]
        if not delete_options:
            return None
        to_delete = ui.choose_option(
            title="Delete session:",
            current_value="",
            options=delete_options,
            help_text="Cannot delete the active session",
        )
        if to_delete is None:
            return None
        agent._delete_session(to_delete)
        console.print(
            _display_text(f"Session {to_delete} deleted.", "green")
        )
        return current

    if selected != current:
        agent.switch_session(selected)
        history_count = len(getattr(agent, "_session_history", []))
        chat_count = len(getattr(agent, "_chat_history", [])) - 1 if hasattr(agent, "_chat_history") else 0
        console.print(
            _display_text(
                f"Switched to session {selected} "
                f"({history_count} tasks, {chat_count} chat msgs)",
                "green",
            )
        )
        # Display session context summary
        history = getattr(agent, "_session_history", [])
        if history:
            console.print("[dim]── Session context ──[/dim]")
            for entry in history[-3:]:
                role_color = {"coder": "green", "reader": "yellow", "devops": "magenta", "chat": "cyan"}.get(entry.get("role", ""), "white")
                history_line = Text("  ")
                history_line.append(
                    sanitize_display_text(str(entry.get("role", "?"))),
                    style=role_color,
                )
                history_line.append(
                    " "
                    + sanitize_display_text(
                        str(entry.get("goal", ""))[:100]
                    )
                )
                console.print(history_line)
        return selected
    return None


def choose_role(
    current_role: str | None, verbose: bool, ui: AgentUI | None = None
) -> str | None:
    """Select agent role via Rich UI. Returns role string or None if cancelled."""
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)

    options: list[dict[str, str]] = [
        {
            "value": "auto",
            "label": "Auto (automatic role detection)",
            "description": "Agent automatically detects role from your request (coder/reader/devops/chat)",
        },
        {
            "value": "coder",
            "label": "Coder",
            "description": "Code generation, editing, debugging, refactoring, testing",
        },
        {
            "value": "reader",
            "label": "Reader",
            "description": "Read-only: inspect/analyze files and report; writes are disabled",
        },
        {
            "value": "devops",
            "label": "DevOps",
            "description": "Infrastructure, deployment, server management, CI/CD, Docker",
        },
        {
            "value": "chat",
            "label": "Chat",
            "description": "Questions, explanations, advice, brainstorming, discussions",
        },
    ]

    # Display current as "auto" if not set
    current_display = current_role if current_role else "auto"

    selected = ui.choose_option(
        title="Select Agent Role",
        current_value=current_display,
        options=options,
        help_text="↑/↓ — select, Enter — confirm, Esc — cancel",
    )

    return selected


def choose_effort(
    current_level: str, verbose: bool, ui: AgentUI | None = None
) -> str | None:
    """Select the global reasoning-effort level. Returns the level or None."""
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)

    options = [
        {"value": level, "label": level, "description": EFFORT_DESCRIPTIONS[level]}
        for level in EFFORT_LEVELS
    ]

    return ui.choose_option(
        title="Reasoning effort (applies to every provider)",
        current_value=normalize_effort(current_level),
        options=options,
        help_text="↑/↓ — select, Enter — confirm, Esc — cancel",
    )


def choose_style(
    verbose: bool, ui: AgentUI | None = None
) -> tuple[str, str] | None:
    """Pick a response style for chat or for the action-loop agents.

    Returns (style_type, style_name) or None if the user cancelled at any step.
    """
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)

    style_type = ui.choose_option(
        title="Style for which mode?",
        options=[
            {
                "value": "chat",
                "label": "Chat",
                "description": "Tone of the chat role's answers",
            },
            {
                "value": "agents",
                "label": "Agents",
                "description": "Tone and habits of the coder/reader/devops action loop",
            },
        ],
        current_value="chat",
        help_text="↑/↓ — select, Enter — confirm, Esc — cancel",
    )
    if style_type is None:
        return None

    current_style = get_saved_style(style_type)
    options = [
        {"value": name, "label": name, "description": description}
        for name, description in get_style_options(style_type).items()
    ]

    selected = ui.choose_option(
        title=f"Select {style_type} style",
        current_value=current_style,
        options=options,
        help_text="↑/↓ — select, Enter — confirm, Esc — cancel",
    )
    if selected is None:
        return None

    return style_type, selected


# ── /settings-api ════════════════════════════════════════════════════════════
# Menu rows carry the current value only; the long explanation belongs to the
# edit panel, where there is room for it and where the user is about to need it.

_API_SETTINGS_RESET = "__reset__"
_API_SETTINGS_DONE = "__done__"

# Knobs the running agent reads per call, so an edit lands on the next step.
# Everything else here is consumed once, at launch.
_LIVE_API_SETTING_KEYS = frozenset(
    {
        "request_interval",
        "request_timeout",
        "max_retries",
        "retry_base_delay",
        "context_tokens",
        "response_cache",
        "model_fallbacks",
    }
)


def _api_setting_status(
    setting: ApiSetting,
    value: bool | int | float,
    config: AgentConfig,
) -> str:
    """The value column of one settings row, saying what actually applies."""
    rendered = format_value(setting, value)
    if setting.key == "context_tokens" and not value:
        derived = derived_prompt_budget(config.provider, config.fast_model)
        grouped = f"{derived:,}".replace(",", " ")
        rendered = f"{rendered} — {grouped} for {config.fast_model}"
    env_var = active_env_override(setting)
    if env_var:
        rendered = f"{rendered} (ignored: {env_var} is set)"
    return rendered


def _api_settings_menu_options(
    values: dict[str, bool | int | float],
    config: AgentConfig,
) -> list[dict[str, str]]:
    rows = [
        {
            "value": setting.key,
            "label": setting.label,
            "description": _api_setting_status(setting, values[setting.key], config),
        }
        for setting in API_SETTINGS
    ]
    rows.append(
        {
            "value": _API_SETTINGS_RESET,
            "label": "Reset all",
            "description": "Back to Kvist's shipped defaults",
        }
    )
    rows.append({"value": _API_SETTINGS_DONE, "label": "Done", "description": ""})
    return rows


def _edit_api_toggle(ui: AgentUI, setting: ApiSetting, value: bool) -> bool | None:
    selected = ui.choose_option(
        title=setting.label,
        options=[
            {"value": "on", "label": "On"},
            {"value": "off", "label": "Off"},
        ],
        current_value="on" if value else "off",
        help_text=setting.description,
    )
    if selected is None:
        return None
    return selected == "on"


def _edit_api_number(
    ui: AgentUI,
    setting: ApiSetting,
    value: int | float,
) -> int | float | None:
    """Ask for a number until it is one this setting accepts, or cancelled."""
    notice = ""
    while True:
        typed = ui.prompt_text(
            title=setting.label,
            current_value=format_value(setting, value),
            help_text=f"{setting.description}\n{range_hint(setting)}{notice}",
        )
        if typed is None:
            return None
        try:
            return parse_number(setting, typed)
        except ValueError as error:
            notice = f"\n\n{error}"


def _edit_api_setting(
    ui: AgentUI,
    setting: ApiSetting,
    value: bool | int | float,
) -> bool | int | float | None:
    if setting.kind == "bool":
        return _edit_api_toggle(ui, setting, bool(value))
    return _edit_api_number(ui, setting, value)


def _commit_api_settings(
    config: AgentConfig,
    values: dict[str, bool | int | float],
) -> None:
    store_api_settings(values)
    apply_api_settings(config)


def configure_api_settings(
    config: AgentConfig, verbose: bool, ui: AgentUI | None = None
) -> set[str]:
    """Run the API settings dialog. Returns the keys the user changed.

    Every edit is persisted and applied to *config* as it is made, so closing
    the dialog is not a commit step and cancelling one field never discards
    another.
    """
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)
    values = load_api_settings()
    changed: set[str] = set()
    # The menu reopens after every edit, so it reopens on the row just edited
    # instead of sending the user back to the top of the list each time.
    cursor = API_SETTINGS[0].key

    while True:
        chosen = ui.choose_option(
            title=f"API settings — {provider_label(config.provider)}",
            options=_api_settings_menu_options(values, config),
            current_value=cursor,
            help_text="↑/↓ — select, Enter — edit, Esc — close",
        )
        if chosen is None or chosen == _API_SETTINGS_DONE:
            return changed
        cursor = chosen

        if chosen == _API_SETTINGS_RESET:
            if confirm_api_settings_reset(ui):
                defaults = default_api_settings()
                changed.update(
                    key for key, value in values.items() if value != defaults[key]
                )
                values = defaults
                _commit_api_settings(config, values)
            continue

        setting = setting_for(chosen)
        updated = _edit_api_setting(ui, setting, values[setting.key])
        if updated is None or updated == values[setting.key]:
            continue
        values[setting.key] = updated
        _commit_api_settings(config, values)
        changed.add(setting.key)


def confirm_api_settings_reset(ui: AgentUI) -> bool:
    return (
        ui.choose_option(
            title="Reset every API setting to its default?",
            options=[
                {"value": "no", "label": "No, keep them"},
                {"value": "yes", "label": "Yes, reset"},
            ],
            current_value="no",
            help_text="Enter — confirm, Esc — cancel",
        )
        == "yes"
    )


def confirm_revert(file_count: int, verbose: bool) -> bool:
    """Ask the user to confirm a workspace revert. Returns True if confirmed."""
    ui = TerminalBannerUI(console=console, verbose=verbose)
    confirmed = ui.choose_option(
        title=f"Revert {file_count} agent-changed file(s) to their baselines?",
        options=[
            {"value": "no", "label": "No, cancel"},
            {"value": "yes", "label": "Yes, revert"},
        ],
        current_value="no",
        help_text="Enter — confirm, Esc — cancel",
    )
    return confirmed == "yes"


def _terminal_supports_mouse_clicks() -> bool:
    """Best-effort check for xterm-style mouse click reporting.

    The Linux virtual console (``TERM=linux``) and non-terminal streams never
    forward mouse events to the process; anything else that identifies a real
    terminal type is assumed capable — prompt_toolkit's own mouse protocol
    degrades to an inert click rather than corrupting input if this guess is
    wrong, so the cost of a false positive is just "the button did nothing."
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    if os.name == "nt":
        return True
    return os.environ.get("TERM", "").strip().lower() not in ("", "dumb", "linux")


def confirm_with_mouse_checkbox(title: str, checkbox_text: str) -> bool | None:
    """Mouse-click checkbox + OK/Cancel confirmation for a destructive action.

    Returns ``True`` if the box was checked and OK was activated, ``False`` if
    cancelled, or ``None`` if this terminal cannot be trusted to report mouse
    clicks — the caller falls back to a keyboard-only confirmation in that case.
    """
    if not _terminal_supports_mouse_clicks():
        return None

    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.widgets import Button, Checkbox, Frame, Label

    checkbox = Checkbox(text=checkbox_text)
    result = {"value": False}

    def submit() -> None:
        result["value"] = checkbox.checked
        get_app().exit()

    def cancel() -> None:
        result["value"] = False
        get_app().exit()

    ok_button = Button(text="OK", handler=submit)
    cancel_button = Button(text="Cancel", handler=cancel)

    root = Frame(
        HSplit(
            [
                Label(text=title),
                Window(height=1),
                checkbox,
                Window(height=1),
                VSplit([ok_button, Window(width=2), cancel_button]),
            ]
        ),
        title="Confirm",
    )

    nav_bindings = KeyBindings()
    nav_bindings.add("tab")(focus_next)
    nav_bindings.add("s-tab")(focus_previous)

    escape_bindings = KeyBindings()

    @escape_bindings.add("escape")
    @escape_bindings.add("c-c")
    def _cancel(event) -> None:
        result["value"] = False
        event.app.exit()

    app = Application(
        layout=Layout(root, focused_element=checkbox),
        key_bindings=merge_key_bindings([nav_bindings, escape_bindings]),
        mouse_support=True,
        full_screen=True,
    )
    app.run()
    return result["value"]


def _model_picker_options(provider: str) -> tuple[list[dict[str, str]], bool]:
    """Return model picker options for a provider, preferring the live catalog.

    Queries the provider's /v1/models endpoint using stored credentials and
    falls back to the static curated hints when discovery fails, returns
    nothing, or when /settings-api has turned dynamic model loading off. The
    second element marks whether the options are live.
    """
    if not dynamic_model_loading_enabled():
        return get_model_options(provider), False
    try:
        required_keys = required_secret_keys_for_provider(provider, [])
        credentials = load_available_credentials(required_keys)
        available_models, _error = fetch_available_models(provider, credentials)
        options = get_model_options(provider, available_models)
        if options:
            return options, True
    except Exception:
        pass
    return get_model_options(provider), False


def choose_models(
    provider: str,
    current_models: dict[str, str],
    verbose: bool,
    ui: AgentUI | None = None,
) -> dict[str, str] | None:
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)
    options, _is_live = _model_picker_options(provider)
    if not options:
        return None

    role_labels = {
        "fast": "Fast model",
        "smart": "Smart model",
        "planner": "Planner model",
    }
    selected = dict(current_models)
    original = dict(current_models)

    for role in MODEL_ROLES:
        chosen = ui.choose_option(
            title=f"Select model for {role_labels[role]}",
            current_value=selected.get(role, options[0]["value"]),
            options=options,
            help_text="↑/↓ — select model, Enter — confirm, Esc — cancel all",
        )
        if chosen is None:
            return None
        selected[role] = chosen

    if selected == original:
        return selected
    return selected


def select_models_interactively(
    config: AgentConfig, verbose: bool, ui: AgentUI | None = None
) -> dict[str, str] | None:
    """Route the /models wizard to the picker matching the active provider."""
    current_models = {
        "fast": config.fast_model,
        "smart": config.smart_model,
        "planner": config.planner_model,
    }
    if ui is None:
        ui = TerminalBannerUI(console=console, verbose=verbose)
    if config.provider == OMNIROUTE_PROVIDER:
        return configure_omniroute_models_interactively(ui, current_models=current_models)
    if config.provider == OPENROUTER_PROVIDER:
        return configure_openrouter_models_interactively(ui, current_models=current_models)
    if config.provider in MANUAL_OPENAI_PROVIDERS:
        return configure_manual_provider_models_interactively(
            ui, current_models=current_models, provider=config.provider
        )
    if is_custom_provider(config.provider):
        return configure_custom_provider_models_interactively(ui, current_models=current_models)
    return choose_models(
        provider=config.provider,
        current_models=current_models,
        verbose=verbose,
        ui=ui,
    )


# ── Custom Provider Wizard ═══════════════════════════════════════════════════


def configure_custom_provider_interactively(ui: AgentUI, verbose: bool) -> str | None:
    """RichUI wizard to create a new custom OpenAI- or Anthropic-compatible provider.

    Walks the user through: name, protocol, base URL, API key (optional), display
    label, and models. Returns ``\"custom:<name>\"`` on success or ``None`` if cancelled.
    """
    console.print()
    console.print(Panel(
        "[bold bright_cyan]Create Custom Provider[/bold bright_cyan]\n"
        "[dim]Configure an OpenAI- or Anthropic-compatible endpoint as a custom provider.[/dim]",
        border_style="bright_cyan",
    ))

    # ── Step 1: Provider name ──────────────────────────────────────────
    existing_names = set(get_custom_providers().keys())
    while True:
        name = ui.prompt_text(
            title="Enter a short provider ID:",
            current_value="",
            help_text=(
                "Only lowercase letters, digits, hyphens and underscores.\n"
                "Example: my-llama, local-vllm, company-gateway"
            ),
        )
        if name is None:
            console.print("[yellow]Provider creation cancelled.[/yellow]")
            return None
        try:
            name = validate_provider_name(name)
        except CustomProviderError as error:
            console.print(_display_text(str(error), "red"))
            continue
        if name in existing_names:
            console.print(
                _display_text(
                    f"Provider {name!r} already exists. "
                    "Choose a different name.",
                    "red",
                )
            )
            continue
        break

    # ── Step 2: Protocol ──────────────────────────────────────────────
    protocol = ui.choose_option(
        title="Select protocol:",
        options=[
            {
                "value": "openai",
                "label": "OpenAI-compatible",
                "description": "Uses OpenAI API format (most common)",
            },
            {
                "value": "anthropic",
                "label": "Anthropic-compatible",
                "description": "Uses Anthropic Messages API format",
            },
        ],
        current_value="openai",
        help_text="↑/↓ — select, Enter — confirm",
    )
    if protocol is None:
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None

    # ── Step 3: Base URL ──────────────────────────────────────────────
    if protocol == "anthropic":
        url_help = (
            "Anthropic-compatible endpoint URL.\n"
            "Example: https://api.anthropic.com or https://gateway.example.com"
        )
    else:
        url_help = (
            "OpenAI-compatible endpoint URL.\n"
            "Example: http://localhost:8080/v1 or https://api.example.com/v1"
        )

    while True:
        base_url = ui.prompt_text(
            title="Enter base URL:",
            current_value="",
            help_text=url_help,
        )
        if base_url is None:
            console.print("[yellow]Provider creation cancelled.[/yellow]")
            return None
        base_url = base_url.strip()
        if not base_url:
            console.print("[red]Base URL cannot be empty.[/red]")
            continue
        if not base_url.startswith(("http://", "https://")):
            choice = ui.choose_option(
                title="base URL must start with https://.",
                options=[
                    {"value": "add_https", "label": "Add https:// automatically"},
                    {"value": "retry", "label": "Enter again"},
                ],
                current_value="add_https",
                help_text="Enter — confirm, Esc — cancel",
            )
            if choice is None:
                console.print("[yellow]Provider creation cancelled.[/yellow]")
                return None
            if choice == "add_https":
                base_url = "https://" + base_url
                break
            else:
                continue
        break

    if protocol == "openai":
        url_stripped = base_url.strip()
        while url_stripped.endswith("/"):
            url_stripped = url_stripped[:-1]

        from urllib.parse import urlparse
        parsed = urlparse(url_stripped)
        path = parsed.path.strip("/")

        if not path:
            choice = ui.choose_option(
                title=(
                    "base URL has no path suffix; most OpenAI-compatible endpoints "
                    "require /v1, and requests will 404 without it."
                ),
                options=[
                    {"value": "add", "label": "Add /v1 (recommended)"},
                    {"value": "keep", "label": "Keep as is"},
                ],
                current_value="add",
                help_text="Enter — confirm, Esc — cancel",
            )
            if choice is None:
                console.print("[yellow]Provider creation cancelled.[/yellow]")
                return None
            if choice == "add":
                base_url = url_stripped + "/v1"
            else:
                base_url = url_stripped
        else:
            base_url = url_stripped

    # ── Step 3b: Streaming support ──────────────────────────────────────
    streaming_choice = ui.choose_option(
        title="Does this endpoint support streaming responses?",
        options=[
            {
                "value": "no",
                "label": "No / not sure (recommended)",
                "description": (
                    "Many third-party OpenAI-compatible gateways handle plain "
                    "requests correctly but break on stream=true. If in doubt, "
                    "keep this off."
                ),
            },
            {
                "value": "yes",
                "label": "Yes, streaming is verified to work",
                "description": "Enable stream=true requests for this provider.",
            },
        ],
        current_value="no",
        help_text="↑/↓ — select, Enter — confirm",
    )
    if streaming_choice is None:
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None
    streaming = streaming_choice == "yes"

    # ── Step 4: API key (optional, masked) ─────────────────────────────
    api_key = ui.prompt_secret(
        title="Enter API key (optional, press Enter to skip):",
        current_value="",
        help_text="Leave empty if the endpoint does not require authentication.",
    )
    if api_key is None:
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None
    api_key = api_key.strip()

    # ── Step 5: Display label ─────────────────────────────────────────
    default_label = name.replace("-", " ").replace("_", " ").title()
    label = ui.prompt_text(
        title="Enter display name:",
        current_value=default_label,
        help_text="Human-readable name shown in the provider list.",
    )
    if label is None:
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None
    label = label.strip() or default_label

    # ── Step 6: Models ────────────────────────────────────────────────
    role_labels = {"fast": "Fast", "smart": "Smart", "planner": "Planner"}
    console.print()
    action = ui.choose_option(
        title="Model configuration:",
        options=[
            {
                "value": "one",
                "label": "Enter one model ID for all roles",
            },
            {
                "value": "each",
                "label": "Enter separate model IDs for each role",
            },
        ],
        current_value="one",
        help_text="↑/↓ — select, Enter — confirm, Esc — cancel",
    )
    if action is None:
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None

    models: dict[str, str] = {}
    if action == "one":
        model_id = ui.prompt_text(
            title="Enter model ID for all roles:",
            current_value="",
            help_text="The model identifier used by the endpoint (e.g. gpt-4, llama-4, my-model).",
        )
        if model_id is None:
            console.print("[yellow]Provider creation cancelled.[/yellow]")
            return None
        model_id = model_id.strip()
        if not model_id:
            console.print("[red]Model ID cannot be empty.[/red]")
            return None
        for role in MODEL_ROLES:
            models[role] = model_id
    else:
        for role in MODEL_ROLES:
            model_id = ui.prompt_text(
                title=f"Enter model ID for {role_labels[role]} role:",
                current_value="",
                help_text=f"Model identifier for the {role} role.",
            )
            if model_id is None:
                console.print("[yellow]Provider creation cancelled.[/yellow]")
                return None
            model_id = model_id.strip()
            if not model_id:
                console.print(
                    _display_text(
                        f"Model ID for {role} cannot be empty.",
                        "red",
                    )
                )
                return None
            models[role] = model_id

    # ── Step 7: Confirm ───────────────────────────────────────────────
    console.print()
    proto_display = "Anthropic-compatible" if protocol == "anthropic" else "OpenAI-compatible"
    summary = Text()

    def append_summary(label_text: str, value: str, style: str = "") -> None:
        summary.append(f"{label_text}: ", style="bold")
        summary.append(sanitize_display_text(value), style=style)
        summary.append("\n")

    append_summary("Provider ID", name)
    append_summary("Display name", label)
    append_summary("Protocol", proto_display)
    append_summary("Base URL", base_url)
    append_summary(
        "API key",
        "configured" if api_key else "none",
        "green" if api_key else "dim",
    )
    append_summary("Streaming", "enabled" if streaming else "disabled")
    summary.append("Models:\n", style="bold")
    for role in MODEL_ROLES:
        summary.append(f"  {role_labels[role]:8s} ")
        summary.append(sanitize_display_text(models[role]))
        summary.append("\n")

    console.print(Panel(
        summary,
        title="Provider Summary",
        border_style="bright_cyan",
    ))

    confirmed = ui.choose_option(
        title="Create this provider?",
        options=[
            {"value": "yes", "label": "Yes, create provider"},
            {"value": "no", "label": "No, cancel"},
        ],
        current_value="yes",
        help_text="Enter — confirm, Esc — cancel",
    )
    if confirmed != "yes":
        console.print("[yellow]Provider creation cancelled.[/yellow]")
        return None

    # ── Save ──────────────────────────────────────────────────────────
    # Route through the same normaliser the GUI uses so the persisted shape is
    # defined in exactly one place. The wizard already applied these rules
    # interactively, so this pass is idempotent.
    name, provider_config = normalize_custom_provider_payload({
        "name": name,
        "label": label,
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
        "streaming": streaming,
    })
    save_custom_provider(name, provider_config)
    provider_id = f"{CUSTOM_PROVIDER_PREFIX}{name}"
    console.print(
        _display_text(
            f"Custom provider {label} created successfully!",
            "green",
        )
    )
    console.print(_display_text(f"Provider ID: {provider_id}", "dim"))
    return provider_id


def configure_custom_provider_models_interactively(
    ui: AgentUI,
    current_models: dict[str, str],
) -> dict[str, str] | None:
    """Interactive model configuration for an existing custom provider."""
    role_labels = {"fast": "Fast", "smart": "Smart", "planner": "Planner"}

    console.print()
    console.print("[bold cyan]Custom Provider — Model Configuration[/bold cyan]")
    console.print("[dim]Enter model IDs for each role.[/dim]")

    console.print()
    console.print("[bold]Current models:[/bold]")
    for role in MODEL_ROLES:
        model = current_models.get(role, "")
        label = role_labels.get(role, role)
        _print_model_row(label, model)

    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
            {
                "value": "one",
                "label": "Enter one model id for all roles",
            },
            {
                "value": "each",
                "label": "Enter a separate model id per role",
            },
            {
                "value": "keep",
                "label": "Keep current models (unchanged)",
            },
        ],
        current_value="",
        help_text="Enter — cancel and keep current models",
    )

    if action is None or action == "keep":
        return dict(current_models) if action == "keep" else None

    if action == "one":
        first_model = current_models.get("fast", "")
        model_id = ui.prompt_text(
            title="Enter model ID for all roles:",
            current_value=first_model,
            help_text="Model identifier for this provider's endpoint.",
        )
        if model_id is None or not model_id.strip():
            return None

        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            selected[role] = model_id.strip()

        console.print(
            _display_text(
                f"Model set to {model_id} for all roles.",
                "green",
            )
        )
        return selected

    if action == "each":
        selected = {}
        for role in MODEL_ROLES:
            model_id = ui.prompt_text(
                title=f"Enter model ID for {role_labels[role]} role:",
                current_value=current_models.get(role, ""),
                help_text=f"{_ROLE_HINTS[role]} Model identifier for this provider's endpoint.",
            )
            if model_id is None or not model_id.strip():
                return None
            selected[role] = model_id.strip()

        console.print(_display_text("Models set per role.", "green"))
        return selected

    return None


def configure_openrouter_models_interactively(
    ui: AgentUI,
    current_models: dict[str, str],
) -> dict[str, str] | None:
    """Interactively configure OpenRouter models via a menu.

    OpenRouter has a curated model list but also supports custom model ids
    in provider/model format (e.g. openai/gpt-5.4-mini, anthropic/claude-sonnet-4-6).
    """
    # ── Header: explain OpenRouter ═══════════════════════════════════════
    console.print()
    console.print(
        "[bold cyan]OpenRouter Model Configuration[/bold cyan]"
    )
    console.print(
        "[dim]OpenRouter provides access to models from OpenAI, Anthropic, Google, Meta, and more.[/dim]"
    )
    console.print(
        "[dim]Select from popular models below or choose custom input for other models.[/dim]"
    )

    # ── Show current models ════════════════════════════════════════════
    role_labels = {
        "fast": "Fast",
        "smart": "Smart",
        "planner": "Planner",
    }
    console.print()
    console.print("[bold]Current models:[/bold]")
    for role in MODEL_ROLES:
        model = current_models.get(role, "")
        label = role_labels.get(role, role)
        _print_model_row(label, model)

    # ── Menu ════════════════════════════════════════════════════════════
    picker_options, picker_is_live = _model_picker_options(OPENROUTER_PROVIDER)
    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
            {
                "value": "pick",
                "label": (
                    "Choose from the live OpenRouter model catalog"
                    if picker_is_live
                    else "Choose from popular OpenRouter models"
                ),
            },
            {
                "value": "custom",
                "label": "Enter custom model id for all roles",
            },
            {
                "value": "keep",
                "label": "Keep current models (unchanged)",
            },
        ],
        current_value="",
        help_text="Enter — cancel and keep current models",
    )

    if action is None:
        return None

    if action == "keep":
        console.print("[green]Current models kept.[/green]")
        return dict(current_models)

    # ── Pick from predefined options ═════════════════════════════════════
    if action == "pick":
        options = picker_options
        selected = dict(current_models)
        original = dict(current_models)

        for role in MODEL_ROLES:
            chosen = ui.choose_option(
                title=f"Select model for {role_labels[role]}",
                current_value=selected.get(role, options[0]["value"]),
                options=options,
                help_text="↑/↓ — select model, Enter — confirm, Esc — cancel all",
            )
            if chosen is None:
                return None
            selected[role] = chosen

        console.print("[green]Models selected from OpenRouter catalog.[/green]")
        return selected

    # ── Custom model id input ═══════════════════════════════════════════
    if action == "custom":
        first_model = current_models.get("fast", "")
        model_id = ui.prompt_text(
            title="Enter OpenRouter model id for all roles:",
            current_value=first_model,
            help_text=(
                "Format: provider/model (e.g. openai/gpt-5.4-mini)\n"
                "See https://openrouter.ai/models for full list."
            ),
        )

        if model_id is None:
            return None

        if not model_id:
            console.print("[yellow]Model id cannot be empty.[/yellow]")
            return None

        # Validate using the same OmniRoute validator (provider/model format)
        validation_error = validate_omniroute_model_id(model_id)
        if validation_error:
            console.print(
                _display_text(
                    f"Invalid model id: {validation_error}",
                    "red",
                )
            )
            return None

        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            selected[role] = model_id

        console.print(
            _display_text(
                f"Model set to {model_id} for all roles.",
                "green",
            )
        )
        return selected

    return None


def configure_omniroute_models_interactively(
    ui: AgentUI,
    current_models: dict[str, str],
) -> dict[str, str] | None:
    """Interactively configure OmniRoute models via a menu.

    OmniRoute has no static model list — models are dynamic and depend on
    connected providers. Users enter model ids manually in provider/model
    format (e.g. kr/claude-sonnet-4.5, ds-web/deepseek-v4-pro).
    """
    # ── Header: explain OmniRoute ══════════════════════════════════════
    console.print()
    console.print(
        "[bold cyan]OmniRoute Model Configuration[/bold cyan]"
    )
    console.print(
        "[dim]OmniRoute uses dynamic model ids — no static model list.[/dim]"
    )
    console.print(
        "[dim]Enter model ids manually in provider/model format "
        "(e.g. [bold]kr/claude-sonnet-4.5[/bold], [bold]ds-web/deepseek-v4-pro[/bold]).[/dim]"
    )

    # ── Show current models ════════════════════════════════════════════
    role_labels = {
        "fast": "Fast",
        "smart": "Smart",
        "planner": "Planner",
    }
    console.print()
    console.print("[bold]Current models:[/bold]")
    for role in MODEL_ROLES:
        model = current_models.get(role, "")
        label = role_labels.get(role, role)
        _print_model_row(label, model)

    # ── Menu ════════════════════════════════════════════════════════════
    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
            {
                "value": "one",
                "label": "Enter one model id for all roles",
            },
            {
                "value": "each",
                "label": "Enter a separate model id per role",
            },
            {
                "value": "keep",
                "label": "Keep current models (unchanged)",
            },
        ],
        current_value="",
        help_text="Enter — cancel and keep current models",
    )

    if action is None:
        return None

    if action == "keep":
        console.print("[green]Current models kept.[/green]")
        return dict(current_models)

    # ── One model id for all roles ═══════════════════════════════════════
    if action == "one":
        first_model = current_models.get("fast", "")
        model_id = ui.prompt_text(
            title="Enter OmniRoute model id for all roles:",
            current_value=first_model,
            help_text=(
                "Format: provider/model (e.g. ds-web/deepseek-v4-pro)\n"
                "Type 'auto' for OmniRoute auto-routing."
            ),
        )

        if model_id is None:
            # User cancelled text input
            return None

        if not model_id:
            console.print("[yellow]Model id cannot be empty.[/yellow]")
            return None

        validation_error = validate_omniroute_model_id(model_id)
        if validation_error:
            console.print(
                _display_text(
                    f"Invalid model id: {validation_error}",
                    "red",
                )
            )
            return None

        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            selected[role] = model_id

        console.print(
            _display_text(
                f"Model set to {model_id} for all roles.",
                "green",
            )
        )
        return selected

    # ── Separate model id per role ═══════════════════════════════════════
    if action == "each":
        selected = {}
        for role in MODEL_ROLES:
            model_id = ui.prompt_text(
                title=f"Enter OmniRoute model id for {role_labels[role]} role:",
                current_value=current_models.get(role, ""),
                help_text=(
                    f"{_ROLE_HINTS[role]}\n"
                    "Format: provider/model (e.g. ds-web/deepseek-v4-pro). "
                    "Type 'auto' for OmniRoute auto-routing."
                ),
            )
            if model_id is None:
                return None
            if not model_id:
                console.print(
                    _display_text(f"Model id for {role} cannot be empty.", "yellow")
                )
                return None

            validation_error = validate_omniroute_model_id(model_id)
            if validation_error:
                console.print(
                    _display_text(
                        f"Invalid model id for {role}: {validation_error}",
                        "red",
                    )
                )
                return None
            selected[role] = model_id

        console.print(_display_text("Models set per role.", "green"))
        return selected

    # Unknown action (shouldn't happen with choose_option, but defensive)
    return None


def configure_manual_provider_models_interactively(
    ui: AgentUI,
    current_models: dict[str, str],
    provider: str,
) -> dict[str, str] | None:
    """Interactively configure models for a manual-tier provider (see
    ``MANUAL_OPENAI_PROVIDERS`` in llm_clients.py). Kvist ships no static
    model list for these vendors, but they are plain OpenAI-compatible
    endpoints, so ``_model_picker_options`` can usually populate a live
    ``/v1/models`` catalog once an API key is configured — offered as a
    picker the same way ``configure_openrouter_models_interactively`` does.
    Text entry (single id, or one per role) remains the fallback for a first
    run with no key yet, or when the endpoint has no ``/v1/models``."""
    provider_display = provider_label(provider)
    console.print()
    console.print(f"[bold cyan]{provider_display} Model Configuration[/bold cyan]")
    console.print(
        f"[dim]Enter the exact model id from {provider_display}'s documentation.[/dim]"
    )

    role_labels = {"fast": "Fast", "smart": "Smart", "planner": "Planner"}
    console.print()
    console.print("[bold]Current models:[/bold]")
    for role in MODEL_ROLES:
        _print_model_row(role_labels.get(role, role), current_models.get(role, ""))

    picker_options, picker_is_live = _model_picker_options(provider)

    console.print()
    menu_options = []
    if picker_is_live:
        menu_options.append({
            "value": "pick",
            "label": f"Choose from the live {provider_display} model catalog",
        })
    menu_options.append({"value": "one", "label": "Enter one model id for all roles"})
    menu_options.append({"value": "each", "label": "Enter a separate model id per role"})
    menu_options.append({"value": "keep", "label": "Keep current models (unchanged)"})

    action = ui.choose_option(
        title="Select action:",
        options=menu_options,
        current_value="",
        help_text="Enter — cancel and keep current models",
    )
    if action is None:
        return None
    if action == "keep":
        console.print("[green]Current models kept.[/green]")
        return dict(current_models)

    if action == "pick":
        selected = dict(current_models)
        for role in MODEL_ROLES:
            chosen = ui.choose_option(
                title=f"Select model for {role_labels[role]}",
                current_value=selected.get(role, picker_options[0]["value"]),
                options=picker_options,
                help_text="↑/↓ — select model, Enter — confirm, Esc — cancel all",
            )
            if chosen is None:
                return None
            selected[role] = chosen
        console.print(
            _display_text(f"Models selected from the {provider_display} catalog.", "green")
        )
        return selected

    if action == "one":
        model_id = ui.prompt_text(
            title=f"Enter {provider_display} model id for all roles:",
            current_value=current_models.get("fast", ""),
            help_text="The exact model id, e.g. as shown in the provider's docs.",
        )
        if model_id is None:
            return None
        model_id = model_id.strip()
        if not model_id:
            console.print("[yellow]Model id cannot be empty.[/yellow]")
            return None
        console.print(_display_text(f"Model set to {model_id} for all roles.", "green"))
        return {role: model_id for role in MODEL_ROLES}

    if action == "each":
        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            model_id = ui.prompt_text(
                title=f"Enter {provider_display} model id for {role_labels[role]} role:",
                current_value=current_models.get(role, ""),
                help_text=_ROLE_HINTS[role],
            )
            if model_id is None:
                return None
            model_id = model_id.strip()
            if not model_id:
                console.print(_display_text(f"Model id for {role} cannot be empty.", "yellow"))
                return None
            selected[role] = model_id
        console.print(_display_text("Models set per role.", "green"))
        return selected

    return None


# ── Event-frontend commands (--frontend stdio-multi) ═════════════════════════
# The same wizards as the classic REPL, run inside the stdio process with the
# EventStreamUI so every dialog becomes an input_request. A handler reports what
# changed through CommandResult.effects; it never prints to the frontend.

_ROLE_CHOICES = ("auto", "coder", "reader", "devops", "chat")


def _bi(en: str, ru: str) -> str:
    return f"{en} / {ru}"


def _command_cancelled() -> CommandResult:
    return CommandResult(True, _bi("Cancelled.", "Отменено."), {"cancelled": True})


def _save_failure_note(error: RuntimeError) -> str:
    detail = sanitize_display_text(str(error))
    return " (" + _bi(f"not saved: {detail}", f"не сохранено: {detail}") + ")"


def _stdio_role(ctx: CommandContext) -> CommandResult:
    if ctx.args:
        selected = ctx.args[0].casefold()
        if selected not in _ROLE_CHOICES:
            return CommandResult(
                False,
                _bi(
                    "Role must be one of: " + ", ".join(_ROLE_CHOICES),
                    "Роль должна быть одной из: " + ", ".join(_ROLE_CHOICES),
                ),
            )
    else:
        selected = choose_role(
            get_saved_role(), ctx.session.agent.config.verbose, ui=ctx.ui
        )
    if selected is None:
        return _command_cancelled()
    try:
        save_role_selection(selected)
    except RuntimeError as error:
        return CommandResult(
            False, _bi("Role not saved", "Роль не сохранена") + _save_failure_note(error)
        )
    ctx.routed_role = None if selected == "auto" else selected
    return CommandResult(
        True,
        _bi(f"Role set to: {selected}", f"Роль: {selected}"),
        {"role": selected},
    )


def _stdio_effort(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    if ctx.args:
        selected = ctx.args[0].casefold()
        if selected not in EFFORT_LEVELS:
            return CommandResult(
                False,
                _bi(
                    "Effort must be one of: " + ", ".join(EFFORT_LEVELS),
                    "Уровень должен быть одним из: " + ", ".join(EFFORT_LEVELS),
                ),
            )
    else:
        selected = choose_effort(
            agent.config.reasoning_effort, agent.config.verbose, ui=ctx.ui
        )
    if selected is None:
        return _command_cancelled()
    selected = normalize_effort(selected)
    apply_reasoning_effort(agent, agent.config, selected)
    note = ""
    try:
        save_reasoning_effort(selected)
    except RuntimeError as error:
        note = _save_failure_note(error)
    advisory = effort_advisory(agent.config.provider, selected)
    return CommandResult(
        True,
        _bi(f"Reasoning effort set to: {selected}", f"Уровень рассуждений: {selected}")
        + note,
        {"effort": selected, "advisory": advisory or ""},
    )


def _stdio_plan(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    enabled = parse_plan_value(
        "/plan " + " ".join(ctx.args), not agent.config.skip_planning
    )
    if enabled is None:
        return CommandResult(False, _bi("Usage: plan [true|false]", "Использование: plan [true|false]"))
    apply_plan_mode(agent, enabled)
    note = ""
    try:
        save_planning_enabled(enabled)
    except RuntimeError as error:
        note = _save_failure_note(error)
    return CommandResult(
        True,
        (
            _bi("Planning stage enabled.", "Этап планирования включён.")
            if enabled
            else _bi("Planning stage disabled.", "Этап планирования выключен.")
        )
        + note,
        {"plan": enabled},
    )


def _stdio_verbose(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    enabled = parse_verbose_value("/verbose " + " ".join(ctx.args), agent.config.verbose)
    if enabled is None:
        return CommandResult(False, _bi("Usage: verbose [true|false]", "Использование: verbose [true|false]"))
    apply_verbose_mode(agent, enabled)
    return CommandResult(
        True,
        (
            _bi("Verbose mode enabled.", "Подробный режим включён.")
            if enabled
            else _bi("Verbose mode disabled.", "Подробный режим выключен.")
        ),
        {"verbose": enabled},
    )


def _stdio_style(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    if len(ctx.args) == 2:
        chosen: tuple[str, str] | None = (ctx.args[0].casefold(), ctx.args[1])
    elif ctx.args:
        return CommandResult(False, _bi("Usage: style [chat|agents NAME]", "Использование: style [chat|agents ИМЯ]"))
    else:
        chosen = choose_style(agent.config.verbose, ui=ctx.ui)
    if chosen is None:
        return _command_cancelled()
    style_type, style_name = chosen
    if style_type not in ("chat", "agents") or not save_style_configuration(style_type, style_name):
        return CommandResult(False, _bi(f"Unknown style: {style_type} {style_name}", f"Неизвестный стиль: {style_type} {style_name}"))
    adopted = agent.adopt_session_style(style_type, style_name)
    message = _bi(f"{style_type.capitalize()} style set to: {style_name}", f"Стиль {style_type}: {style_name}")
    if not adopted:
        message += _bi(
            f". This session keeps {agent.session_style(style_type)}; the new style starts with the next session.",
            f". Эта сессия сохраняет {agent.session_style(style_type)}; новый стиль начнётся со следующей сессии.",
        )
    return CommandResult(
        True,
        message,
        {"style": {"type": style_type, "name": style_name, "applied_to_session": adopted}},
    )


def _session_effects(agent) -> dict:
    return {
        "session": agent.current_session,
        "sessions": agent._list_sessions(),
        "has_checkpoint": agent._peek_checkpoint() is not None,
    }


def _stdio_session_op(agent, args: list[str]) -> str:
    """Apply ``switch|new|delete [NAME]`` and return the confirmation text."""
    op, name = args[0].casefold(), (args[1].strip().lower() if len(args) > 1 else None)
    if op == "new":
        created = agent.new_session(name)
        return _bi(f"Session {created} created.", f"Сессия {created} создана.")
    if name is None or op not in ("switch", "delete"):
        raise ValueError(_bi("Usage: session [switch|new|delete] [NAME]", "Использование: session [switch|new|delete] [ИМЯ]"))
    if op == "switch":
        agent.switch_session(name)
        return _bi(f"Switched to session {name}.", f"Переключено на сессию {name}.")
    if not agent._delete_session(name):
        raise ValueError(_bi(f"Unknown session: {name!r}", f"Неизвестная сессия: {name!r}"))
    return _bi(f"Session {name} deleted.", f"Сессия {name} удалена.")


def _stdio_session(ctx: CommandContext) -> CommandResult:
    from durable_json import JsonStorageError

    agent = ctx.session.agent
    before = agent.current_session
    try:
        if ctx.args:
            message = _stdio_session_op(agent, ctx.args)
        elif choose_session(agent, agent.config.verbose, ui=ctx.ui) is None:
            return _command_cancelled()
        else:
            message = _bi(f"Active session: {agent.current_session}", f"Активная сессия: {agent.current_session}")
    except (JsonStorageError, OSError, ValueError) as error:
        return CommandResult(False, sanitize_display_text(str(error).strip() or type(error).__name__))
    effects = _session_effects(agent)
    effects["changed"] = agent.current_session != before
    return CommandResult(True, message, effects)


def _stdio_new(ctx: CommandContext) -> CommandResult:
    ctx.args = ["new", *ctx.args[:1]]
    return _stdio_session(ctx)


def _provider_offers_model_list(provider: str) -> bool:
    return bool(
        provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER)
        or provider in MANUAL_OPENAI_PROVIDERS
        or is_custom_provider(provider)
        or get_model_options(provider)
    )


def _stdio_models(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    config = agent.config
    if not _provider_offers_model_list(config.provider):
        return CommandResult(
            False,
            _bi(
                f"No models available for {provider_label(config.provider)}.",
                f"Для {provider_label(config.provider)} нет доступных моделей.",
            ),
        )
    if len(ctx.args) == len(MODEL_ROLES):
        selected = dict(zip(MODEL_ROLES, ctx.args))
    elif ctx.args:
        return CommandResult(False, _bi("Usage: models [FAST SMART PLANNER]", "Использование: models [FAST SMART PLANNER]"))
    else:
        selected = select_models_interactively(config, config.verbose, ui=ctx.ui)
    if selected is None:
        return _command_cancelled()

    credentials = load_available_credentials(
        (OPENAI_API_KEY_KEY, "gemini_api_key", "groq_api_key", "omniroute_api_key", "openrouter_api_key")
    )
    issues = validate_model_selection(config.provider, selected, credentials)
    if issues:
        return CommandResult(False, "\n".join(sanitize_display_text(issue) for issue in issues))
    try:
        new_agent, new_config, warnings = ctx.rebuild(
            provider_override=config.provider, models_override=selected
        )
    except RuntimeError as error:
        return CommandResult(False, sanitize_display_text(str(error)))
    ctx.session.replace_agent(new_agent)

    note = ""
    try:
        if is_custom_provider(new_config.provider):
            name = extract_custom_provider_name(new_config.provider)
            info = get_custom_provider_by_name(name) or {}
            info["models"] = selected
            save_custom_provider(name, info)
        else:
            save_models_for_provider(new_config.provider, selected)
    except RuntimeError as error:
        note = _save_failure_note(error)
    return CommandResult(
        True,
        _bi("Models updated.", "Модели обновлены.") + note,
        {
            "provider": new_config.provider,
            "models": {
                "fast": new_config.fast_model,
                "smart": new_config.smart_model,
                "planner": new_config.planner_model,
            },
            "warnings": warnings,
        },
    )


def _stdio_changes(ctx: CommandContext) -> CommandResult:
    from change_journal import JournalUnavailableError, build_diff_report

    try:
        report = build_diff_report(ctx.session.agent.executor.workspace)
    except JournalUnavailableError as error:
        return CommandResult(False, sanitize_display_text(str(error)))
    return CommandResult(True, report)


def _adopt_rebuilt_agent(ctx: CommandContext, **rebuild_kwargs) -> tuple[AgentConfig, list[str]]:
    """Rebuild through the event stream, so a missing secret becomes a dialog."""
    new_agent, new_config, warnings = ctx.rebuild(interactive=True, **rebuild_kwargs)
    ctx.session.replace_agent(new_agent)
    sync_strime_suggestion(new_config.provider, new_config.stream)
    return new_config, warnings


def _provider_effects(config: AgentConfig, warnings: list[str]) -> dict:
    return {
        "provider": config.provider,
        "models": {
            "fast": config.fast_model,
            "smart": config.smart_model,
            "planner": config.planner_model,
        },
        "warnings": warnings,
    }


def _failure_detail(error: Exception) -> str:
    return sanitize_display_text(str(error).strip() or type(error).__name__)


def _known_provider(raw: str) -> str | None:
    candidate = raw.strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    if is_custom_provider(candidate) and get_custom_provider_by_name(
        extract_custom_provider_name(candidate)
    ):
        return candidate
    return None


def _stdio_provider(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    previous = agent.config.provider
    if ctx.args:
        selected = _known_provider(ctx.args[0])
        if selected is None:
            return CommandResult(
                False,
                _bi(f"Unknown provider: {ctx.args[0]}", f"Неизвестный провайдер: {ctx.args[0]}"),
            )
    else:
        selected = choose_provider(previous, agent.config.verbose, ui=ctx.ui)
    if selected is None:
        return _command_cancelled()
    if selected == previous:
        return CommandResult(
            True,
            _bi("Provider unchanged.", "Провайдер не изменён."),
            {"provider": previous, "changed": False},
        )
    try:
        config, warnings = _adopt_rebuilt_agent(ctx, provider_override=selected)
    except (RuntimeError, ValueError, OSError) as error:
        return CommandResult(False, _failure_detail(error))
    return CommandResult(
        True,
        _bi(
            f"Provider changed to {provider_label(config.provider)}.",
            f"Провайдер: {provider_label(config.provider)}.",
        ),
        {**_provider_effects(config, warnings), "changed": True},
    )


def _stdio_change_api(ctx: CommandContext) -> CommandResult:
    config = ctx.session.agent.config
    try:
        changed = change_provider_api_key(config, config.verbose, ui=ctx.ui)
    except RuntimeError as error:
        return CommandResult(
            False, _bi("API key not saved", "Ключ не сохранён") + _save_failure_note(error)
        )
    if not changed:
        return _command_cancelled()
    try:
        new_config, warnings = _adopt_rebuilt_agent(ctx, provider_override=config.provider)
    except (RuntimeError, ValueError, OSError) as error:
        detail = _failure_detail(error)
        return CommandResult(
            False,
            _bi(
                f"The key was saved, but applying it failed: {detail}",
                f"Ключ сохранён, но применить его не удалось: {detail}",
            ),
            {"key_saved": True, "restart_required": True},
        )
    label = provider_label(new_config.provider)
    return CommandResult(
        True,
        _bi(f"API key updated for {label}.", f"API-ключ для {label} обновлён."),
        {**_provider_effects(new_config, warnings), "key_saved": True},
    )


def _stdio_del_provider(ctx: CommandContext) -> CommandResult:
    config = ctx.session.agent.config
    if not is_custom_provider(config.provider):
        return CommandResult(
            False,
            _bi(
                "del-provider works only for a custom provider.",
                "del-provider работает только для кастомного провайдера.",
            ),
        )
    name = extract_custom_provider_name(config.provider)
    label = provider_label(config.provider)
    confirmed = ctx.ui.confirm(
        f"Delete custom provider '{label}'? / Удалить кастомный провайдер '{label}'?",
        _bi("This cannot be undone.", "Отменить нельзя."),
    )
    if not confirmed:
        return _command_cancelled()
    if not delete_custom_provider(name):
        return CommandResult(
            False,
            _bi(f"Custom provider '{name}' was already gone.", f"Кастомный провайдер '{name}' уже удалён."),
        )
    clear_saved_provider()
    try:
        new_config, warnings = _adopt_rebuilt_agent(ctx)
    except (RuntimeError, ValueError, OSError) as error:
        detail = _failure_detail(error)
        return CommandResult(
            False,
            _bi(
                f"Provider deleted, but selecting a replacement failed: {detail}. "
                "Restart Kvist or run provider.",
                f"Провайдер удалён, но выбрать замену не удалось: {detail}. "
                "Перезапустите Kvist или выполните provider.",
            ),
            {"deleted": name, "restart_required": True},
        )
    new_label = provider_label(new_config.provider)
    return CommandResult(
        True,
        _bi(
            f"Custom provider '{name}' deleted. Now using {new_label}.",
            f"Кастомный провайдер '{name}' удалён. Сейчас: {new_label}.",
        ),
        {**_provider_effects(new_config, warnings), "deleted": name},
    )


def _stdio_full_reset(ctx: CommandContext) -> CommandResult:
    # Two explicit yeses instead of the classic mouse checkbox, which needs a
    # fullscreen terminal. Any other answer, including EOF, leaves the config alone.
    warning = _bi(
        "This wipes ~/.kvist/config.json entirely: every saved API key, custom "
        "provider, model selection and preference. It cannot be undone.",
        "Это полностью очистит ~/.kvist/config.json: все сохранённые API-ключи, "
        "кастомные провайдеры, выбор моделей и настройки. Отменить нельзя.",
    )
    if not ctx.ui.confirm(
        "Continue toward a full provider/config reset? / Продолжить полный сброс?",
        warning,
    ):
        return _command_cancelled()
    if not ctx.ui.confirm(
        "Final confirmation: wipe all provider settings now? / "
        "Последнее подтверждение: стереть все настройки провайдеров сейчас?",
        warning,
    ):
        return _command_cancelled()
    try:
        reset_user_config()
    except RuntimeError as error:
        return CommandResult(
            False, _bi("Config was not wiped", "Конфиг не очищен") + _save_failure_note(error)
        )
    ctx.routed_role = None
    try:
        config, warnings = _adopt_rebuilt_agent(ctx)
    except (RuntimeError, ValueError, OSError) as error:
        detail = _failure_detail(error)
        return CommandResult(
            False,
            _bi(
                f"Config wiped, but no provider was configured: {detail}. "
                "Restart Kvist or run provider.",
                f"Конфиг очищен, но провайдер не настроен: {detail}. "
                "Перезапустите Kvist или выполните provider.",
            ),
            {"reset": True, "restart_required": True},
        )
    label = provider_label(config.provider)
    return CommandResult(
        True,
        _bi(
            f"Full reset complete. Now using {label}.",
            f"Полный сброс выполнен. Сейчас: {label}.",
        ),
        {**_provider_effects(config, warnings), "reset": True},
    )


def _stdio_settings_api(ctx: CommandContext) -> CommandResult:
    config = ctx.session.agent.config
    try:
        changed = configure_api_settings(config, config.verbose, ui=ctx.ui)
    except RuntimeError as error:
        return CommandResult(
            False,
            _bi("API settings not saved", "Настройки API не сохранены") + _save_failure_note(error),
        )
    if not changed:
        return CommandResult(
            True,
            _bi("API settings unchanged.", "Настройки API не изменены."),
            {"changed_keys": []},
        )
    saved = load_api_settings()
    lines = [
        f"{setting.label}: {format_value(setting, saved[setting.key])}"
        for setting in API_SETTINGS
        if setting.key in changed
    ]
    restart_required = not changed <= _LIVE_API_SETTING_KEYS
    if restart_required:
        lines.append(
            _bi(
                "Dynamic model loading applies at the next launch.",
                "Динамическая загрузка моделей применится при следующем запуске.",
            )
        )
    return CommandResult(
        True,
        "\n".join(lines),
        {
            "changed_keys": sorted(changed),
            "values": {key: saved[key] for key in sorted(changed)},
            "restart_required": restart_required,
        },
    )


def _stdio_strime(ctx: CommandContext) -> CommandResult:
    agent = ctx.session.agent
    if not is_custom_provider(agent.config.provider):
        return CommandResult(
            False,
            _bi(
                "strime is available only for custom providers.",
                "strime доступна только для кастомных провайдеров.",
            ),
        )
    enabled = parse_strime_value("/strime " + " ".join(ctx.args), agent.config.stream)
    if enabled is None:
        return CommandResult(False, _bi("Usage: strime [true|false]", "Использование: strime [true|false]"))
    try:
        applied = apply_strime_mode(agent, enabled)
    except RuntimeError as error:
        return CommandResult(
            False, _bi("Streaming mode not saved", "Режим стриминга не сохранён") + _save_failure_note(error)
        )
    if not applied:
        return CommandResult(
            False,
            _bi(
                "The active custom provider could not be updated.",
                "Не удалось обновить активный кастомный провайдер.",
            ),
        )
    return CommandResult(
        True,
        (
            _bi("Streaming mode enabled.", "Стриминг включён.")
            if enabled
            else _bi("Streaming mode disabled.", "Стриминг выключен.")
        ),
        {"stream": enabled},
    )


_REVERT_LISTED_FILES = 50


def _revert_confirmation_body(baseline: dict) -> str:
    paths = sorted(baseline)
    lines = [sanitize_display_text(path) for path in paths[:_REVERT_LISTED_FILES]]
    if len(paths) > _REVERT_LISTED_FILES:
        lines.append(f"... +{len(paths) - _REVERT_LISTED_FILES}")
    summary = _bi(
        f"This restores {len(paths)} file(s) tracked by the workspace change journal "
        "(all sessions) to their state before the agent's first change and cannot be "
        "undone. Files changed outside the agent's writes are kept.",
        f"Будет восстановлено файлов: {len(paths)} (журнал изменений рабочей папки, все "
        "сессии) — до первого изменения агентом; отменить нельзя. Файлы, изменённые "
        "вне записей агента, сохраняются.",
    )
    return summary + "\n\n" + "\n".join(lines)


def _stdio_revert(ctx: CommandContext) -> CommandResult:
    from change_journal import JournalUnavailableError, load_baseline, revert_workspace

    workspace = ctx.session.agent.executor.workspace
    try:
        baseline = load_baseline(workspace)
    except JournalUnavailableError:
        return CommandResult(
            False,
            _bi(
                "The change journal is corrupt; revert is unavailable until it is reset "
                "(remove .kvist/change_journal.json). No files were touched.",
                "Журнал изменений повреждён; revert недоступен, пока его не сбросить "
                "(удалите .kvist/change_journal.json). Файлы не тронуты.",
            ),
        )
    if not baseline:
        return CommandResult(
            True,
            _bi("Nothing to revert: no tracked changes.", "Откатывать нечего: нет отслеживаемых изменений."),
            {"reverted": []},
        )
    confirmed = ctx.ui.confirm(
        f"Revert {len(baseline)} agent-changed file(s) to their baselines? / "
        f"Откатить файлов, изменённых агентом: {len(baseline)}?",
        _revert_confirmation_body(baseline),
    )
    if not confirmed:
        return _command_cancelled()
    try:
        reverted = revert_workspace(workspace)
    except (JournalUnavailableError, OSError) as error:
        return CommandResult(False, _failure_detail(error))
    applied = [item for item in reverted if item.action != "skipped"]
    skipped = [item for item in reverted if item.action == "skipped"]
    message = _bi(f"Reverted {len(applied)} file(s).", f"Откачено файлов: {len(applied)}.")
    if skipped:
        message += "\n" + _bi(
            f"Kept {len(skipped)} file(s) that changed outside the agent's tracked writes.",
            f"Оставлено файлов, изменённых вне записей агента: {len(skipped)}.",
        )
    return CommandResult(
        True,
        message,
        {
            "reverted": [
                {"path": item.path, "action": item.action, "reason": item.reason}
                for item in reverted
            ]
        },
    )


STDIO_COMMANDS: dict[str, CommandHandler] = {
    "role": _stdio_role,
    "effort": _stdio_effort,
    "plan": _stdio_plan,
    "verbose": _stdio_verbose,
    "style": _stdio_style,
    "session": _stdio_session,
    "new": _stdio_new,
    "models": _stdio_models,
    "changes": _stdio_changes,
    "provider": _stdio_provider,
    "change-api": _stdio_change_api,
    "del-provider": _stdio_del_provider,
    "full-reset": _stdio_full_reset,
    "settings-api": _stdio_settings_api,
    "strime": _stdio_strime,
    "revert": _stdio_revert,
}


def main() -> None:
    args = (
        _DIRECT_CLI_ARGS
        if _DIRECT_CLI_ARGS is not None
        else _build_cli_parser().parse_args()
    )

    if args.config_info:
        import json as _json

        from config_info import build_config_info

        try:
            payload = build_config_info()
        except (OSError, ValueError) as error:
            failure = {
                "ok": False,
                "error": sanitize_display_text(str(error).strip() or type(error).__name__),
            }
            print(_json.dumps(failure, ensure_ascii=False))
            raise SystemExit(1) from error
        print(_json.dumps(payload, ensure_ascii=False))
        return

    if args.commit_message:
        _run_commit_message_op()
        return

    if args.config_op:
        import json as _json

        from config_info import build_config_info

        try:
            # The payload can carry an API key, so the frontend pipes it on
            # stdin rather than argv (argv is visible in `ps` / `/proc`).
            payload_text = args.config_payload
            if payload_text is None:
                payload_text = sys.stdin.read()
            raw = _json.loads(payload_text or "null")
            if args.config_op == "set-provider":
                payload = _run_set_provider_op(raw)
            else:
                if args.config_op == "add-custom-provider":
                    name, provider_config = normalize_custom_provider_payload(raw)
                    save_custom_provider(name, provider_config)
                else:
                    name = str(raw.get("name", "")).strip() if isinstance(raw, dict) else ""
                    if not name or not delete_custom_provider(name):
                        raise CustomProviderError(f"Custom provider {name!r} not found.")
                payload = {
                    "ok": True,
                    "provider_id": f"{CUSTOM_PROVIDER_PREFIX}{name}",
                    "info": build_config_info(),
                }
        except (CustomProviderError, ValueError, OSError, RuntimeError) as error:
            failure = {
                "ok": False,
                "error": sanitize_display_text(str(error).strip() or type(error).__name__),
            }
            print(_json.dumps(failure, ensure_ascii=False))
            raise SystemExit(1) from error
        print(_json.dumps(payload, ensure_ascii=False))
        return

    # All execution frontends share the same safe default workspace.
    try:
        args.workspace = str(resolve_workspace_path(args.workspace))
    except (RuntimeError, OSError, ValueError) as error:
        print(sanitize_display_text(str(error)), file=sys.stderr)
        raise SystemExit(2) from error

    if args.journal_op:
        import json as _json

        from change_journal import (
            JournalUnavailableError,
            build_diff_report,
            revert_workspace,
        )

        try:
            workspace = resolve_workspace_path(args.workspace)
            if args.journal_op == "changes":
                payload = {"ok": True, "report": build_diff_report(workspace)}
            else:
                reverted = revert_workspace(workspace)
                payload = {
                    "ok": True,
                    "reverted": [
                        {"path": item.path, "action": item.action, "reason": item.reason}
                        for item in reverted
                    ],
                }
        except (JournalUnavailableError, RuntimeError, OSError, ValueError) as error:
            failure = {
                "ok": False,
                "error": sanitize_display_text(str(error).strip() or type(error).__name__),
            }
            print(_json.dumps(failure, ensure_ascii=False))
            raise SystemExit(1) from error
        print(_json.dumps(payload, ensure_ascii=False))
        return

    if args.session_op:
        _run_session_op(args)
        return

    if args.frontend == "stdio-multi":
        from event_stdio import launch_multi

        model_values = (args.fast_model, args.smart_model, args.planner_model)
        if any(model_values) and not all(model_values):
            print(
                "Model overrides require --fast-model, --smart-model, and --planner-model together.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        models_override = (
            {
                "fast": args.fast_model,
                "smart": args.smart_model,
                "planner": args.planner_model,
            }
            if all(model_values)
            else None
        )
        try:
            launch_multi(
                args.workspace,
                build_agent,
                provider_override=args.provider,
                models_override=models_override,
                routed_role=None if args.role == "auto" else args.role,
                approval_mode=args.approval_mode,
                commands=STDIO_COMMANDS,
                skip_planning=not resolve_planning_enabled(),
            )
        except RuntimeError as error:
            print(sanitize_display_text(str(error)), file=sys.stderr)
            raise SystemExit(2) from error
        return

    if args.frontend == "stdio":
        from event_stdio import launch

        goal = "" if args.resume else sys.stdin.readline().strip()
        model_values = (args.fast_model, args.smart_model, args.planner_model)
        if any(model_values) and not all(model_values):
            print(
                "Model overrides require --fast-model, --smart-model, and --planner-model together.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        models_override = (
            {
                "fast": args.fast_model,
                "smart": args.smart_model,
                "planner": args.planner_model,
            }
            if all(model_values)
            else None
        )
        try:
            launch(
                args.workspace,
                goal,
                build_agent,
                provider_override=args.provider,
                models_override=models_override,
                routed_role=None if args.role == "auto" else args.role,
                approval_mode=args.approval_mode,
                resume=args.resume,
            )
        except RuntimeError as error:
            print(sanitize_display_text(str(error)), file=sys.stderr)
            raise SystemExit(2) from error
        return

    if (args.frontend or get_saved_frontend()) == "tui" and not args.no_banner:
        exit_code = launch_tui(args)
        if exit_code is not None:
            raise SystemExit(exit_code)

    max_steps = int(os.getenv("AGENT_MAX_STEPS", "50"))
    persist_logs = os.getenv("AGENT_PERSIST_LOGS", "1") not in {"0", "false", "False"}
    verbose = os.getenv("AGENT_VERBOSE", "0") in {"1", "true", "True", "yes", "on"}
    slash_command_auto_suggest.set_verbose(verbose)
    # Persisted preference is the default; AGENT_SKIP_PLANNING overrides if set.
    planning_enabled = resolve_planning_enabled()
    skip_planning = not planning_enabled
    slash_command_auto_suggest.set_plan(planning_enabled)
    session_language = detect_request_language(os.getenv("LANG", ""), fallback="en")

    try:
        workspace_path = resolve_workspace_path(args.workspace)
        executor = SafeExecutor(
            workspace=workspace_path,
            read_roots=tuple(args.allow_read),
        )
        agent, config, model_warnings = build_agent(
            executor=executor,
            max_steps=max_steps,
            persist_logs=persist_logs,
            verbose=verbose,
            skip_planning=skip_planning,
            language_hint=session_language,
        )
    except (RuntimeError, ValueError) as error:
        print(sanitize_display_text(str(error)), file=sys.stderr)
        sys.exit(2)

    sync_strime_suggestion(config.provider, config.stream)

    # A launch never continues the previous one's conversation. An interrupted
    # run stays reachable: /resume walks to whichever session holds it.
    from durable_json import JsonStorageError

    try:
        _, resumable_session = agent.start_fresh_session()
    except (JsonStorageError, OSError, ValueError) as error:
        resumable_session = None
        model_warnings.append(
            "Could not start a fresh session "
            f"({sanitize_display_text(str(error).strip() or type(error).__name__)}); "
            f"continuing in {agent.current_session}."
        )

    interactive_screen: InteractiveScreen | None = None
    interactive_screen_error: str | None = None
    if (
        not args.no_banner
        and banner.banner_available(console.width, console.height)
    ):
        candidate_screen = InteractiveScreen(_get_session(), config)
        try:
            candidate_screen.start()
        except Exception as error:
            candidate_screen.stop()
            interactive_screen_error = sanitize_display_text(str(error))
        else:
            interactive_screen = candidate_screen
            agent._ui.attach_screen(interactive_screen)

    if interactive_screen is not None:
        executor.set_hitl_callbacks(
            suspend_fn=interactive_screen.suspend,
            resume_fn=interactive_screen.resume,
        )
    else:
        executor.set_hitl_callbacks(
            suspend_fn=agent._ui.stop_live,
            resume_fn=agent._ui.start_live,
        )

    # The whole interactive session runs on the alternate screen buffer, so
    # whatever was on the real terminal before startup (shell prompt, prior
    # scrollback) is left untouched and reappears exactly as it was once the
    # session ends — no destructive clearing of the user's own terminal.
    use_session_screen = (
        interactive_screen is None
        and sys.stdin.isatty()
        and console.is_terminal
    )
    if use_session_screen:
        console.set_alt_screen(True)

    if interactive_screen is None:
        render_startup_banner(agent, config, max_steps, verbose, model_warnings)
        if interactive_screen_error is not None:
            console.print(
                _display_text(
                    "Live banner unavailable; using the plain interface: "
                    f"{interactive_screen_error}",
                    "yellow",
                )
            )
    else:
        for warning in model_warnings:
            console.print(_display_text(warning, "yellow"))

    if resumable_session is not None:
        console.print(
            _display_text(
                f"An interrupted task is waiting in session {resumable_session} — "
                "'/resume' continues it there.",
                "yellow",
            )
        )

    interrupted = False
    # Turns finished in this process. Drives the prompt-length gate; the
    # persisted session history must not be used for it.
    completed_turns = 0
    try:
        while True:
            if interactive_screen is None:
                user_goal = multiline_input()
            else:
                user_goal = interactive_screen.read_input()
            user_goal = normalize_slash_command(user_goal)
            # A bare Enter is a no-op in every shell. Re-prompt silently instead
            # of answering it with a "describe the task" lecture.
            if not user_goal:
                continue
            if not user_goal.startswith("/"):
                session_language = detect_request_language(user_goal, fallback=session_language)
            if user_goal.lower() == "/bye":
                save_session_for_shutdown(agent)
                break

            if user_goal.split(maxsplit=1)[0].casefold() == "/tui":
                selected_tui = parse_tui_value(user_goal, get_saved_frontend() == "tui")
                if selected_tui is None:
                    console.print("[yellow]Usage: /tui [true|false|on|off][/yellow]")
                    continue
                try:
                    save_frontend("tui" if selected_tui else "terminal")
                except RuntimeError as error:
                    console.print(_display_text(f"Could not save the UI preference: {error}", "yellow"))
                    continue
                console.print(_display_text(
                    "The Ink terminal UI will open on the next launch."
                    if selected_tui
                    else "The classic terminal will open on the next launch.",
                    "green",
                ))
                continue

            if user_goal.lower() == "/resume":
                try:
                    result = agent.resume()
                except Exception as error:
                    safe_error = sanitize_display_text(
                        str(error).strip() or type(error).__name__
                    )
                    console.print(
                        Text(
                            "Resume failed safely: "
                            f"{safe_error}. The current session remains available.",
                            style="red",
                        )
                    )
                    continue
                if result is None:
                    console.print("[yellow]No saved checkpoint to resume.[/yellow]")
                elif verbose:
                    print(f"\n[Final Answer]\n{sanitize_display_text(result)}")
                continue

            if (
                user_goal
                and user_goal.split(maxsplit=1)[0].casefold() == "/verbose"
            ):
                selected_verbose = parse_verbose_value(user_goal, verbose)
                if selected_verbose is None:
                    console.print(
                        "[yellow]Usage: /verbose <true> or /verbose <false>[/yellow]"
                    )
                    continue

                verbose = selected_verbose
                apply_verbose_mode(agent, verbose)
                state = "enabled" if verbose else "disabled"
                console.print(
                    _display_text(f"Verbose mode {state}.", "green")
                )
                continue

            if (
                user_goal
                and user_goal.split(maxsplit=1)[0].casefold() == "/plan"
            ):
                planning_enabled = parse_plan_value(
                    user_goal, not agent.config.skip_planning
                )
                if planning_enabled is None:
                    console.print(
                        "[yellow]Usage: /plan <true> or /plan <false>[/yellow]"
                    )
                    continue

                skip_planning = not planning_enabled
                apply_plan_mode(agent, planning_enabled)
                try:
                    save_planning_enabled(planning_enabled)
                except RuntimeError as error:
                    console.print(
                        _display_text(
                            "Planning preference not saved: "
                            f"{sanitize_display_text(str(error))}",
                            "yellow",
                        )
                    )
                if planning_enabled:
                    console.print(
                        _display_text("Planning stage enabled.", "green")
                    )
                else:
                    console.print(
                        _display_text(
                            "Planning stage disabled — the agent starts acting "
                            "immediately, without an upfront plan.",
                            "green",
                        )
                    )
                continue

            if (
                user_goal
                and user_goal.split(maxsplit=1)[0].casefold() == "/strime"
            ):
                if not is_custom_provider(config.provider):
                    console.print(
                        "[yellow]/strime is available only for custom providers.[/yellow]"
                    )
                    continue

                selected_streaming = parse_strime_value(
                    user_goal,
                    agent.config.stream,
                )
                if selected_streaming is None:
                    console.print(
                        "[yellow]Usage: /strime <true> or /strime <false>[/yellow]"
                    )
                    continue

                if not apply_strime_mode(agent, selected_streaming):
                    console.print(
                        "[yellow]The active custom provider could not be updated.[/yellow]"
                    )
                    continue

                state = "enabled" if selected_streaming else "disabled"
                console.print(
                    _display_text(f"Streaming mode {state}.", "green")
                )
                continue

            if user_goal.lower() == "/del-provider":
                if not is_custom_provider(config.provider):
                    console.print(
                        _display_text(
                            "/del-provider works only for a custom provider. / "
                            "/del-provider работает только для кастомного провайдера.",
                            "yellow",
                        )
                    )
                    continue

                provider_name = extract_custom_provider_name(config.provider)
                with paused_interactive_screen(interactive_screen):
                    confirmed = (
                        TerminalBannerUI(console=console, verbose=verbose).choose_option(
                            title=(
                                f"Delete custom provider '{provider_label(config.provider)}'? "
                                "This cannot be undone."
                            ),
                            options=[
                                {"value": "no", "label": "No, cancel"},
                                {"value": "yes", "label": "Yes, delete"},
                            ],
                            current_value="no",
                            help_text="Enter — confirm, Esc — cancel",
                        )
                        == "yes"
                    )
                if not confirmed:
                    console.print("[yellow]Provider deletion cancelled.[/yellow]")
                    continue

                if not delete_custom_provider(provider_name):
                    console.print(
                        _display_text(
                            f"Custom provider '{provider_name}' was already gone.",
                            "yellow",
                        )
                    )
                    continue
                clear_saved_provider()

                build_error: str | None = None
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    try:
                        agent, config, model_warnings = build_agent(
                            executor=executor,
                            max_steps=max_steps,
                            persist_logs=persist_logs,
                            verbose=verbose,
                            skip_planning=skip_planning,
                            language_hint=session_language,
                        )
                    except Exception as error:
                        build_error = sanitize_display_text(
                            str(error).strip() or type(error).__name__
                        )

                if build_error is not None:
                    console.print(
                        _display_text(
                            "Provider deleted, but selecting a replacement failed: "
                            f"{build_error}. Run /provider to choose one.",
                            "red",
                        )
                    )
                    continue

                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                    agent._ui.attach_screen(interactive_screen)
                    executor.set_hitl_callbacks(
                        suspend_fn=interactive_screen.suspend,
                        resume_fn=interactive_screen.resume,
                    )
                else:
                    executor.set_hitl_callbacks(
                        suspend_fn=agent._ui.stop_live,
                        resume_fn=agent._ui.start_live,
                    )
                sync_strime_suggestion(config.provider, config.stream)

                console.print(
                    _display_text(
                        f"Custom provider '{provider_name}' deleted. Now using "
                        f"{provider_label(config.provider)}.",
                        "green",
                    )
                )
                if interactive_screen is None:
                    print_runtime_banner(config, max_steps, verbose, model_warnings)
                else:
                    for warning in model_warnings:
                        console.print(_display_text(warning, "yellow"))
                continue

            if user_goal.lower() == "/provider":
                previous_provider = config.provider
                build_error: str | None = None
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    selected_provider = choose_provider(previous_provider, verbose)
                    if selected_provider is not None and selected_provider != previous_provider:
                        try:
                            agent, config, model_warnings = build_agent(
                                executor=executor,
                                max_steps=max_steps,
                                persist_logs=persist_logs,
                                verbose=verbose,
                                skip_planning=skip_planning,
                                provider_override=selected_provider,
                                language_hint=session_language,
                            )
                        except RuntimeError as error:
                            build_error = sanitize_display_text(str(error))

                if selected_provider is None:
                    console.print("[yellow]Provider switch cancelled.[/yellow]")
                    continue
                if selected_provider == previous_provider:
                    console.print("[dim]Provider unchanged.[/dim]")
                    continue
                if build_error is not None:
                    console.print(_display_text(build_error, "red"))
                    continue

                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                    agent._ui.attach_screen(interactive_screen)
                    executor.set_hitl_callbacks(
                        suspend_fn=interactive_screen.suspend,
                        resume_fn=interactive_screen.resume,
                    )
                else:
                    executor.set_hitl_callbacks(
                        suspend_fn=agent._ui.stop_live,
                        resume_fn=agent._ui.start_live,
                    )
                sync_strime_suggestion(config.provider, config.stream)

                console.print(
                    _display_text(
                        "Provider changed to "
                        f"{provider_label(config.provider)}.",
                        "green",
                    )
                )
                if interactive_screen is None:
                    print_runtime_banner(config, max_steps, verbose, model_warnings)
                else:
                    for warning in model_warnings:
                        console.print(_display_text(warning, "yellow"))
                continue

            if user_goal.lower() == "/change-api":
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    changed = change_provider_api_key(config, verbose)

                if not changed:
                    console.print("[yellow]API key change cancelled.[/yellow]")
                    continue

                build_error: str | None = None
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    try:
                        agent, config, model_warnings = build_agent(
                            executor=executor,
                            max_steps=max_steps,
                            persist_logs=persist_logs,
                            verbose=verbose,
                            skip_planning=skip_planning,
                            provider_override=config.provider,
                            language_hint=session_language,
                        )
                    except Exception as error:
                        build_error = sanitize_display_text(
                            str(error).strip() or type(error).__name__
                        )

                if build_error is not None:
                    console.print(
                        _display_text(
                            f"Failed to apply new API key: {build_error}",
                            "red",
                        )
                    )
                    continue

                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                    agent._ui.attach_screen(interactive_screen)
                    executor.set_hitl_callbacks(
                        suspend_fn=interactive_screen.suspend,
                        resume_fn=interactive_screen.resume,
                    )
                else:
                    executor.set_hitl_callbacks(
                        suspend_fn=agent._ui.stop_live,
                        resume_fn=agent._ui.start_live,
                    )

                console.print(
                    _display_text(
                        f"API key updated for {provider_label(config.provider)}.",
                        "green",
                    )
                )
                if interactive_screen is None:
                    print_runtime_banner(config, max_steps, verbose, model_warnings)
                else:
                    for warning in model_warnings:
                        console.print(_display_text(warning, "yellow"))
                continue

            if user_goal.lower() == "/models":
                uses_manual_model_ids = (
                    config.provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER)
                    or config.provider in MANUAL_OPENAI_PROVIDERS
                    or is_custom_provider(config.provider)
                )
                if not uses_manual_model_ids and not get_model_options(config.provider):
                    console.print(
                        _display_text(
                            "No models available for current provider "
                            f"({provider_label(config.provider)}). "
                            "Use /provider to switch.",
                            "yellow",
                        )
                    )
                    continue

                build_error: str | None = None
                validation_issues: list[str] = []
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    selected_models = select_models_interactively(config, verbose)
                    if selected_models is not None:
                        available_credentials = load_available_credentials(
                            (
                                OPENAI_API_KEY_KEY,
                                "gemini_api_key",
                                "groq_api_key",
                                "omniroute_api_key",
                                "openrouter_api_key",
                            )
                        )
                        validation_issues = validate_model_selection(
                            config.provider,
                            selected_models,
                            available_credentials,
                            language_hint=session_language,
                        )
                        if not validation_issues:
                            try:
                                agent, config, model_warnings = build_agent(
                                    executor=executor,
                                    max_steps=max_steps,
                                    persist_logs=persist_logs,
                                    verbose=verbose,
                                    skip_planning=skip_planning,
                                    provider_override=config.provider,
                                    models_override=selected_models,
                                    language_hint=session_language,
                                )
                            except RuntimeError as error:
                                build_error = sanitize_display_text(str(error))

                if selected_models is None:
                    console.print("[yellow]Model switch cancelled.[/yellow]")
                    continue
                if validation_issues:
                    for issue in validation_issues:
                        console.print(_display_text(issue, "red"))
                    continue
                if build_error is not None:
                    console.print(_display_text(build_error, "red"))
                    continue

                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                    agent._ui.attach_screen(interactive_screen)
                    executor.set_hitl_callbacks(
                        suspend_fn=interactive_screen.suspend,
                        resume_fn=interactive_screen.resume,
                    )
                else:
                    executor.set_hitl_callbacks(
                        suspend_fn=agent._ui.stop_live,
                        resume_fn=agent._ui.start_live,
                    )

                if is_custom_provider(config.provider):
                    name = extract_custom_provider_name(config.provider)
                    info = get_custom_provider_by_name(name) or {}
                    info["models"] = selected_models
                    save_custom_provider(name, info)
                else:
                    save_models_for_provider(config.provider, selected_models)
                console.print("[green]Models updated.[/green]")
                if interactive_screen is None:
                    print_runtime_banner(config, max_steps, verbose, model_warnings)
                else:
                    for warning in model_warnings:
                        console.print(_display_text(warning, "yellow"))
                continue

            if user_goal.lower() == "/style":
                with paused_interactive_screen(interactive_screen):
                    result = choose_style(verbose)
                if result is None:
                    console.print("[yellow]Style selection cancelled.[/yellow]")
                    continue
                style_type, style_name = result
                save_style_configuration(style_type, style_name)
                # One session, one style: this session takes the new choice only
                # while nothing has been produced under the old one.
                adopted = agent.adopt_session_style(style_type, style_name)
                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                console.print(
                    _display_text(
                        f"{style_type.capitalize()} style set to: {style_name}",
                        "green",
                    )
                )
                if not adopted:
                    console.print(
                        _display_text(
                            f"This session keeps {agent.session_style(style_type)}. "
                            "The new style starts with the next session (/new).",
                            "yellow",
                        )
                    )
                continue

            if user_goal.lower() == "/new":
                try:
                    started = agent.new_session()
                except (JsonStorageError, OSError, ValueError) as error:
                    safe_error = sanitize_display_text(
                        str(error).strip() or type(error).__name__
                    )
                    console.print(
                        _display_text(
                            f"New session failed safely: {safe_error}. "
                            "The current session is unchanged.",
                            "red",
                        )
                    )
                    continue
                if interactive_screen is not None:
                    interactive_screen.clear_transcript()
                else:
                    console.clear()
                render_new_session_notice(started)
                continue

            if user_goal.lower() == "/changes":
                from change_journal import build_diff_report

                report = build_diff_report(agent.executor.workspace)
                console.print(Panel(
                    Text(report),
                    title="Agent changes in this workspace (all sessions)",
                    border_style="bright_cyan",
                ))
                continue

            if user_goal.lower() == "/revert":
                from change_journal import (
                    JournalUnavailableError,
                    load_baseline,
                    revert_workspace,
                )

                try:
                    baseline = load_baseline(agent.executor.workspace)
                except JournalUnavailableError:
                    console.print(
                        _display_text(
                            "The change journal is corrupt; revert is unavailable "
                            "until it is reset (remove .kvist/change_journal.json). "
                            "No files were touched.",
                            "red",
                        )
                    )
                    continue
                if not baseline:
                    console.print("[yellow]Nothing to revert — no tracked changes.[/yellow]")
                    continue

                console.print(
                    _display_text(
                        f"This will restore {len(baseline)} file(s) tracked by the "
                        "workspace change journal (all sessions) to their state "
                        "before the agent's first change and cannot be undone. "
                        "Files changed outside the agent's writes are kept.",
                        "yellow",
                    )
                )
                with paused_interactive_screen(interactive_screen):
                    confirmed = confirm_revert(len(baseline), verbose)
                if not confirmed:
                    console.print("[yellow]Revert cancelled.[/yellow]")
                    continue

                reverted = revert_workspace(agent.executor.workspace)
                applied = [item for item in reverted if item.action != "skipped"]
                skipped = [item for item in reverted if item.action == "skipped"]
                for item in applied:
                    console.print(f"  [dim]{item.action}:[/dim] {item.path}")
                for item in skipped:
                    console.print(
                        f"  [yellow]skipped:[/yellow] {item.path} [dim]({item.reason})[/dim]"
                    )
                console.print(_display_text(f"Reverted {len(applied)} file(s).", "green"))
                if skipped:
                    console.print(
                        _display_text(
                            f"Kept {len(skipped)} file(s) that changed outside the "
                            "agent's tracked writes; their baselines stay in the journal.",
                            "yellow",
                        )
                    )
                continue

            if user_goal.lower() == "/full-reset":
                console.print(
                    Panel(
                        "[bold red]This wipes ~/.kvist/config.json entirely: every saved "
                        "API key, custom provider, model selection, and role/style/effort/"
                        "planning preference. It cannot be undone.[/bold red]\n"
                        "[bold red]Это полностью очистит ~/.kvist/config.json: все "
                        "сохранённые API-ключи, кастомные провайдеры, выбор моделей и "
                        "настройки роли/стиля/effort/планирования. Отменить нельзя.[/bold red]",
                        title="/full-reset",
                        border_style="red",
                    )
                )

                with paused_interactive_screen(interactive_screen):
                    first_confirm = (
                        TerminalBannerUI(console=console, verbose=verbose).choose_option(
                            title="Continue toward a full provider/config reset?",
                            options=[
                                {"value": "no", "label": "No, cancel / Нет, отмена"},
                                {"value": "yes", "label": "Yes, continue / Да, продолжить"},
                            ],
                            current_value="no",
                            help_text="Enter — confirm, Esc — cancel",
                        )
                        == "yes"
                    )
                if not first_confirm:
                    console.print("[yellow]Full reset cancelled.[/yellow]")
                    continue

                with paused_interactive_screen(interactive_screen):
                    mouse_result = confirm_with_mouse_checkbox(
                        title=(
                            "Check the box to confirm the full reset, then click OK.\n"
                            "Поставьте галочку, чтобы подтвердить полный сброс, затем "
                            "нажмите OK."
                        ),
                        checkbox_text=(
                            "I understand this is irreversible / "
                            "Я понимаю, что это необратимо"
                        ),
                    )
                    if mouse_result is None:
                        # Terminal cannot report mouse clicks: skip the mouse-only
                        # gate and fall back to one more keyboard confirmation.
                        final_confirm = (
                            TerminalBannerUI(console=console, verbose=verbose).choose_option(
                                title="Final confirmation: wipe all provider settings now?",
                                options=[
                                    {"value": "no", "label": "No, cancel / Нет, отмена"},
                                    {"value": "yes", "label": "Yes, wipe / Да, стереть"},
                                ],
                                current_value="no",
                                help_text="Enter — confirm, Esc — cancel",
                            )
                            == "yes"
                        )
                    else:
                        final_confirm = mouse_result
                if not final_confirm:
                    console.print("[yellow]Full reset cancelled.[/yellow]")
                    continue

                reset_user_config()

                build_error: str | None = None
                with paused_interactive_screen(interactive_screen), wizard_screen():
                    try:
                        agent, config, model_warnings = build_agent(
                            executor=executor,
                            max_steps=max_steps,
                            persist_logs=persist_logs,
                            verbose=verbose,
                            skip_planning=skip_planning,
                            language_hint=session_language,
                        )
                    except Exception as error:
                        build_error = sanitize_display_text(
                            str(error).strip() or type(error).__name__
                        )

                if build_error is not None:
                    console.print(
                        _display_text(
                            "Config wiped, but no provider was configured: "
                            f"{build_error}. Run /provider when ready.",
                            "red",
                        )
                    )
                    continue

                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                    agent._ui.attach_screen(interactive_screen)
                    executor.set_hitl_callbacks(
                        suspend_fn=interactive_screen.suspend,
                        resume_fn=interactive_screen.resume,
                    )
                else:
                    executor.set_hitl_callbacks(
                        suspend_fn=agent._ui.stop_live,
                        resume_fn=agent._ui.start_live,
                    )
                sync_strime_suggestion(config.provider, config.stream)

                console.print(
                    _display_text(
                        "Full reset complete. Now using "
                        f"{provider_label(config.provider)}. / Полный сброс выполнен.",
                        "green",
                    )
                )
                if interactive_screen is None:
                    print_runtime_banner(config, max_steps, verbose, model_warnings)
                else:
                    for warning in model_warnings:
                        console.print(_display_text(warning, "yellow"))
                continue

            if user_goal.lower() == "/session":
                if not hasattr(agent, "_list_sessions"):
                    console.print("[yellow]/session not available — restart the agent to load the latest code.[/yellow]")
                    continue
                with paused_interactive_screen(interactive_screen):
                    result = choose_session(agent, verbose)
                if result is None:
                    console.print("[yellow]Session switch cancelled.[/yellow]")
                else:
                    console.print(
                        _display_text(
                            f"Active session: {agent.current_session}",
                            "dim",
                        )
                    )
                continue

            if user_goal.lower() == "/role":
                saved_role = get_saved_role()
                with paused_interactive_screen(interactive_screen):
                    selected_role = choose_role(saved_role, verbose)
                if selected_role is None:
                    console.print("[yellow]Role selection cancelled.[/yellow]")
                    continue
                save_role_selection(selected_role)
                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                display_role = selected_role if selected_role != "auto" else "auto (automatic detection)"
                console.print(
                    _display_text(f"Role set to: {display_role}", "green")
                )
                continue

            if user_goal.lower() == "/settings-api":
                try:
                    with paused_interactive_screen(interactive_screen):
                        changed_settings = configure_api_settings(config, verbose)
                except RuntimeError as error:
                    console.print(
                        _display_text(
                            "API settings not saved: "
                            f"{sanitize_display_text(str(error))}",
                            "yellow",
                        )
                    )
                    continue
                if not changed_settings:
                    console.print("[yellow]API settings unchanged.[/yellow]")
                    continue
                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                saved_settings = load_api_settings()
                for setting in API_SETTINGS:
                    if setting.key not in changed_settings:
                        continue
                    console.print(
                        _display_text(
                            f"{setting.label}: "
                            f"{format_value(setting, saved_settings[setting.key])}",
                            "green",
                        )
                    )
                if not changed_settings <= _LIVE_API_SETTING_KEYS:
                    console.print(
                        _display_text(
                            "Dynamic model loading applies at the next launch.",
                            "yellow",
                        )
                    )
                continue

            if user_goal.lower() == "/effort":
                with paused_interactive_screen(interactive_screen):
                    selected_effort = choose_effort(
                        agent.config.reasoning_effort, verbose
                    )
                if selected_effort is None:
                    console.print(
                        "[yellow]Reasoning effort unchanged.[/yellow]"
                    )
                    continue
                selected_effort = normalize_effort(selected_effort)
                apply_reasoning_effort(agent, config, selected_effort)
                try:
                    save_reasoning_effort(selected_effort)
                except RuntimeError as error:
                    console.print(
                        _display_text(
                            "Reasoning-effort preference not saved: "
                            f"{sanitize_display_text(str(error))}",
                            "yellow",
                        )
                    )
                if interactive_screen is not None:
                    interactive_screen.set_config(config)
                console.print(
                    _display_text(
                        f"Reasoning effort set to: {selected_effort}", "green"
                    )
                )
                advisory = effort_advisory(config.provider, selected_effort)
                if advisory:
                    console.print(_display_text(advisory, "yellow"))
                continue

            validation_error = validate_user_goal(
                user_goal,
                language_hint=session_language,
                completed_turns=completed_turns,
            )
            if validation_error:
                console.print(_display_text(validation_error, "yellow"))
                restore_input_draft(interactive_screen, user_goal)
                continue

            if user_goal.lower() == "/commands":
                from slash_commands import SLASH_COMMANDS

                console.print("[bold]Available commands:[/bold]")
                for command in SLASH_COMMANDS:
                    aliases = (
                        f" [dim](aliases: {', '.join('/' + a for a in command.aliases)})[/dim]"
                        if command.aliases
                        else ""
                    )
                    console.print(
                        f"  [cyan]/{command.name}[/cyan] — {command.description}{aliases}"
                    )
                continue

            if user_goal.lower() == "/about" or user_goal.lower() == "/info" or user_goal.lower() == "/help":
                console.print("[green]This is Kvist: open-source AI agent for Windows/Linux. [/green]")
                console.print('''[red] 
Support: 
  [green]Linux: Yes
  Windows: Yes [/green]
  [dim]macOS: Untested (or Experimental)[/dim]
                [/red]''')
                console.print("[dim]\nKvist by Davud, © 2026 [/dim]")
                console.print("[dim] Author: Davud [/dim]")
                console.print("[dim] LICENCE: MIT [/dim]")
                console.print("[dim] Repository: [blue]https://github.com/davud-d/Kvist[/blue] [/dim]")
                console.print("[dim] Release Version: 1.0 [/dim]")
                console.print("[orange]\nThank you for using Kvist [/orange]")
                continue

            from role_router import route_role_decision_with_llm, route_role
            saved_role = get_saved_role()
            # If user selected a specific role, skip role detection and use it directly
            if saved_role and saved_role != "auto":
                detected_role = saved_role
                decision = None
            else:
                try:
                    decision = route_role_decision_with_llm(
                        user_goal=user_goal,
                        client=agent.client,
                        model=config.fast_model,
                    )
                except KeyboardInterrupt:
                    # Routing is a provider request made on the user's behalf
                    # before the turn starts; interrupting it cancels that
                    # turn, not the session.
                    console.print(
                        _display_text(
                            request_cancelled_text(session_language),
                            "yellow",
                        )
                    )
                    # Cancelled before the action model saw the goal: nothing
                    # ran, so handing the text back is honest. Later stages
                    # have already spent tokens or touched files and must not
                    # pretend the turn never happened.
                    restore_input_draft(interactive_screen, user_goal)
                    continue
                except Exception:
                    detected_role = route_role(user_goal)
                    decision = None
                if decision is not None:
                    detected_role = decision.role
            role_colors = {"coder": "green", "reader": "yellow", "devops": "magenta", "chat": "cyan"}
            role_color = role_colors.get(detected_role, "white")
            source_tag = f" ({decision.source})" if decision else ""
            # Show "(manual)" when user has overridden automatic detection
            if saved_role and saved_role != "auto":
                source_tag = " (manual)"
            console.print(
                _display_text(
                    f"⚡ Role: {detected_role}{source_tag}",
                    role_color,
                )
            )

            turn_language = detect_request_language(user_goal, fallback=session_language)
            if interactive_screen is not None:
                interactive_screen.set_language(turn_language)
            try:
                result = agent.run(user_goal, routed_role=detected_role)
            except KeyboardInterrupt:
                # A stop that lands before run() owns the turn: it ends, the
                # session stays open. Stops during planning, the loop or a chat
                # reply never reach here — run() reports and records them.
                console.print(_display_text(run_stopped_text(turn_language), "yellow"))
                continue
            except Exception as error:
                safe_error = sanitize_display_text(
                    str(error).strip() or type(error).__name__
                )
                console.print(
                    Text(
                        "Task failed safely: "
                        f"{safe_error}. You can retry or switch provider.",
                        style="red",
                    )
                )
                continue
            completed_turns += 1
            if verbose:
                print(f"\n[Final Answer]\n{sanitize_display_text(result)}")
    except KeyboardInterrupt:
        save_session_for_shutdown(agent)
        interrupted = True
    finally:
        release_terminal(interactive_screen, use_session_screen)

    render_exit_message(agent)
    if interrupted:
        sys.exit(130)


if __name__ == "__main__":
    main()
