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
        required=True,
        help="Path to the project workspace directory.",
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
        choices=("terminal", "stdio"),
        default="terminal",
        help="Frontend transport. Use 'stdio' for the NDJSON event protocol.",
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
        choices=("status", "create", "switch", "delete", "reset"),
        help="Run one headless session lifecycle operation, print JSON, exit.",
    )
    parser.add_argument(
        "--session-name",
        help="Session name for --session-op create/switch/delete.",
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
    return parser


# Reject invalid invocations and serve --help before constructing terminal UI
# objects or importing provider SDKs. Imported callers (including tests and the
# credential-free PTY fixture) still parse their current argv inside main().
_DIRECT_CLI_ARGS = (
    _build_cli_parser().parse_args()
    if __name__ == "__main__"
    else None
)


from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.output import ColorDepth
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import banner
from agent import AgentConfig, LocalAgent
from executor import SafeExecutor
from interactive_screen import InteractiveScreen
from language_utils import choose_localized_text, detect_request_language
from llm_clients import (
    create_llm_client,
    configured_freeflow_backends,
    ANTHROPIC_PROVIDER,
    CUSTOM_PROVIDER_PREFIX,
    CUSTOM_PROVIDER_PLACEHOLDER,
    FREEFLOW_PROVIDER,
    get_model_options,
    is_custom_provider,
    extract_custom_provider_name,
    MODEL_ROLES,
    OPENAI_API_KEY_KEY,
    OPENAI_PROVIDER,
    OMNIROUTE_PROVIDER,
    OPENROUTER_PROVIDER,
    provider_supports_streaming,
    resolve_openai_accessible_models,
    normalize_provider,
    provider_label,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
    validate_model_selection,
    validate_omniroute_model_id,
)
from redaction import sanitize_display_text
from ui import AgentUI
from terminal_banner import TerminalBannerUI
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
    get_saved_models,
    get_saved_provider,
    get_saved_provider_or_none,
    load_available_credentials,
    prompt_provider_choice,
    save_models_for_provider,
    get_saved_role,
    save_role_selection,
    secret_label_for_key,
    set_provider_secret,
)
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.shortcuts import CompleteStyle

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


def resolve_workspace_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser().resolve()
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


def apply_verbose_mode(agent, enabled: bool) -> None:
    """Apply a verbose-mode change to the active runtime objects."""

    agent.config.verbose = enabled
    agent._ui.verbose = enabled
    if hasattr(agent.client, "_verbose"):
        agent.client._verbose = enabled
    slash_command_auto_suggest.set_verbose(enabled)


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


def resolve_startup_provider(provider_override: str | None = None) -> str:
    if provider_override:
        return normalize_provider(provider_override)

    env_provider = os.getenv("AGENT_LLM_PROVIDER")
    if env_provider:
        return normalize_provider(env_provider)

    saved_provider = get_saved_provider_or_none()
    if saved_provider:
        return saved_provider

    # First run: use RichUI if available, fall back to plain-text
    if sys.stdin.isatty():
        selected = choose_provider("openai", False)
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
) -> tuple[LocalAgent, AgentConfig, list[str]]:
    # Captured before the missing-models wizard may rebind the local ``ui``.
    provided_ui = ui
    provider = resolve_startup_provider(provider_override)

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

    # For OmniRoute, OpenRouter and custom providers, check if models are missing
    if provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER) or is_custom_provider(provider):
        missing_roles = [role for role in MODEL_ROLES if role not in models or not models[role]]
        if missing_roles:
            provider_display = provider_label(provider)
            if provider == OPENROUTER_PROVIDER:
                example_model = "openai/gpt-5.4-mini"
            else:
                example_model = "kr/claude-sonnet-4.5"
            if not sys.stdin.isatty():
                raise RuntimeError(
                    f"{provider_display} models are not configured. Run Kvist in an interactive "
                    f"terminal and set exact model ids with /models, for example {example_model}."
                )
            # Interactive setup if models are missing
            ui = TerminalBannerUI(console=console, verbose=verbose)
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
            else:
                selected_models = configure_omniroute_models_interactively(ui, models)
            if selected_models is None:
                raise RuntimeError(f"{provider_display} model configuration was cancelled.")
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

    required_keys = required_secret_keys_for_provider(
        provider,
        [models["fast"], models["smart"], models["planner"]],
    )
    credentials = ensure_provider_credentials(
        provider=provider,
        validation_model=models["fast"],
        required_keys=required_keys,
        language_hint=language_hint,
        skip_validation=(provider == OPENAI_PROVIDER),
    )
    if provider == FREEFLOW_PROVIDER:
        credentials.update(
            load_available_credentials(("gemini_api_key", "groq_api_key"))
        )
    if provider == OPENAI_PROVIDER:
        resolved_openai_models, access_warnings = resolve_openai_accessible_models(
            credentials[OPENAI_API_KEY_KEY],
            models,
            language_hint=language_hint,
        )
        models = resolved_openai_models
        warnings.extend(access_warnings)
    elif provider == FREEFLOW_PROVIDER:
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
    )
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
    line.append(f" | Max steps: {max_steps} | Verbose: {verbose}")
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
    console.print("[dim]Kvist agent[/dim] [red]stopped.[/red]")
    message = Text(
        "To continue, start the agent and resume session ",
        style="dim",
    )
    message.append(sanitize_display_text(str(session_name)), style="bold dim")
    message.append(".", style="dim")
    console.print(message)


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
    console.print("[dim]Command '/session' — manage and switch sessions.[/dim]")
    console.print("[dim]Command '/new' — reset to a fresh session.[/dim]")
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


def choose_provider(current_provider: str, verbose: bool) -> str | None:
    ui = TerminalBannerUI(console=console, verbose=verbose)

    # Built-in providers in order: OpenAI, Anthropic, OpenRouter, OmniRoute
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


def change_provider_api_key(config: AgentConfig, verbose: bool) -> bool:
    """Prompt for and save new API key(s) for the currently active provider.

    Returns True if at least one key was updated, False if the user left every
    prompt empty (kept the current key) or cancelled.
    """
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


def choose_session(agent, verbose: bool) -> str | None:
    """Select or manage sessions via Rich UI."""
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


def choose_role(current_role: str | None, verbose: bool) -> str | None:
    """Select agent role via Rich UI. Returns role string or None if cancelled."""
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


def choose_models(provider: str, current_models: dict[str, str], verbose: bool) -> dict[str, str] | None:
    ui = TerminalBannerUI(console=console, verbose=verbose)
    options = get_model_options(provider)
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


def select_models_interactively(config: AgentConfig, verbose: bool) -> dict[str, str] | None:
    """Route the /models wizard to the picker matching the active provider."""
    current_models = {
        "fast": config.fast_model,
        "smart": config.smart_model,
        "planner": config.planner_model,
    }
    ui = TerminalBannerUI(console=console, verbose=verbose)
    if config.provider == OMNIROUTE_PROVIDER:
        return configure_omniroute_models_interactively(ui, current_models=current_models)
    if config.provider == OPENROUTER_PROVIDER:
        return configure_openrouter_models_interactively(ui, current_models=current_models)
    if is_custom_provider(config.provider):
        return configure_custom_provider_models_interactively(ui, current_models=current_models)
    return choose_models(
        provider=config.provider,
        current_models=current_models,
        verbose=verbose,
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
        name = name.strip().lower()
        if not name:
            console.print("[red]Provider name cannot be empty.[/red]")
            continue
        # Validate characters
        if not all(c.islower() or c.isdigit() or c in "-_" for c in name):
            console.print("[red]Name can only contain lowercase letters, digits, hyphens and underscores.[/red]")
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
    save_custom_provider(name, {
        "label": label,
        "protocol": protocol,
        "base_url": base_url,
        "api_key": api_key,
        "models": models,
        "streaming": streaming,
    })
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
    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
            {
                "value": "pick",
                "label": "Choose from popular OpenRouter models",
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
        options = get_model_options(OPENROUTER_PROVIDER)
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
        import json as _json

        from durable_json import JsonStorageError
        from session_store import run_session_op

        try:
            payload = run_session_op(
                args.workspace,
                args.session_op,
                args.session_name,
            )
        except (ValueError, JsonStorageError, OSError) as error:
            failure = {
                "ok": False,
                "error": sanitize_display_text(str(error).strip() or type(error).__name__),
            }
            print(_json.dumps(failure, ensure_ascii=False))
            raise SystemExit(1) from error
        print(_json.dumps(payload, ensure_ascii=False))
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

    max_steps = int(os.getenv("AGENT_MAX_STEPS", "50"))
    persist_logs = os.getenv("AGENT_PERSIST_LOGS", "1") not in {"0", "false", "False"}
    verbose = os.getenv("AGENT_VERBOSE", "0") in {"1", "true", "True", "yes", "on"}
    slash_command_auto_suggest.set_verbose(verbose)
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
            language_hint=session_language,
        )
    except (RuntimeError, ValueError) as error:
        print(sanitize_display_text(str(error)), file=sys.stderr)
        sys.exit(2)

    sync_strime_suggestion(config.provider, config.stream)

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

            if user_goal.lower() == "/new":
                from durable_json import JsonStorageError

                try:
                    agent.new_session()
                except (JsonStorageError, OSError, ValueError) as error:
                    safe_error = sanitize_display_text(
                        str(error).strip() or type(error).__name__
                    )
                    console.print(
                        _display_text(
                            f"Reset failed safely: {safe_error}. "
                            "The current session is unchanged.",
                            "red",
                        )
                    )
                    continue
                console.print("[green]Session reset — fresh start.[/green]")
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

            try:
                result = agent.run(user_goal, routed_role=detected_role)
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
