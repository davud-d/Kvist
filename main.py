# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from rich.console import Console
from rich.panel import Panel

from agent import AgentConfig, LocalAgent
from executor import SafeExecutor
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
from ui import AgentUI
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
)
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.shortcuts import CompleteStyle

from slash_commands import SlashCommandCompleter, normalize_slash_command

console = Console()
session = PromptSession(
    history=InMemoryHistory(),
    completer=SlashCommandCompleter(),
    complete_while_typing=True,
    complete_style=CompleteStyle.MULTI_COLUMN,
    auto_suggest=AutoSuggestFromHistory(),
)


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
    is_multiline = Condition(lambda: "\n" in session.default_buffer.text)
    return session.prompt(
        "\n> ",
        multiline=is_multiline,
        prompt_continuation="... ",
    ).strip()


def sleep(seconds: int):
    time.sleep(seconds)


_MIN_PROMPT_LENGTH = 10


def validate_user_goal(user_goal: str, language_hint: str = "en") -> str | None:
    """Return an error message if the prompt is invalid, or None if it is acceptable."""
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

    if len(stripped) < _MIN_PROMPT_LENGTH:
        return choose_localized_text(
            ru_text='Опиши задачу подробнее. Например: "Исправь ошибку в main.py" или "Создай parser.py для CSV".',
            en_text='Please describe the task more specifically. Example: "Fix the bug in main.py" or "Create parser.py for CSV".',
            language_hint=language_hint,
        )

    return None


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
) -> tuple[LocalAgent, AgentConfig, list[str]]:
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
            ui = AgentUI(console=console, verbose=verbose)
            console.print(f"[yellow]{provider_display} models are not configured.[/yellow]")
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
            console.print(f"[green]{provider_display} models saved.[/green]")

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
    elif not provider_supports_streaming(provider):
        warnings.append(
            "For FreeFlow LLM, streaming is disabled: the library handles stream responses unstably."
        )
        freeflow_backends = configured_freeflow_backends(credentials)
        if len(freeflow_backends) <= 1:
            warnings.append(
                "FreeFlow fallback across providers is unavailable: configure both Gemini and Groq keys."
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
    return LocalAgent(client=client, executor=executor, config=config), config, warnings


def print_runtime_banner(config: AgentConfig, max_steps: int, verbose: bool, warnings: list[str]) -> None:
    console.print(
        f"[magenta]Provider: {provider_label(config.provider)}[/magenta] | "
        f"[green]Fast model: {config.fast_model}[/green] | "
        f"[bold yellow]Smart model: {config.smart_model}[/bold yellow] | "
        f"[blue]Planner model: {config.planner_model}[/blue] | Max steps: {max_steps} | Verbose: {verbose}"
    )
    for warning in warnings:
        console.print(f"[yellow]{warning}[/yellow]")


def choose_provider(current_provider: str, verbose: bool) -> str | None:
    ui = AgentUI(console=console, verbose=verbose)

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


def choose_session(agent, verbose: bool) -> str | None:
    """Select or manage sessions via Rich UI."""
    ui = AgentUI(console=console, verbose=verbose)
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
        agent.new_session(name)
        console.print(f"[green]Session [bold]{name}[/bold] created.[/green]")
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
        console.print(f"[green]Session [bold]{to_delete}[/bold] deleted.[/green]")
        return None

    if selected != current:
        agent.switch_session(selected)
        history_count = len(getattr(agent, "_session_history", []))
        chat_count = len(getattr(agent, "_chat_history", [])) - 1 if hasattr(agent, "_chat_history") else 0
        console.print(
            f"[green]Switched to session [bold]{selected}[/bold] "
            f"([dim]{history_count} tasks, {chat_count} chat msgs[/dim])[/green]"
        )
        # Display session context summary
        history = getattr(agent, "_session_history", [])
        if history:
            console.print("[dim]── Session context ──[/dim]")
            for entry in history[-3:]:
                role_color = {"coder": "green", "devops": "magenta", "chat": "cyan"}.get(entry.get("role", ""), "white")
                console.print(f"  [{role_color}]{entry.get('role', '?')}[/{role_color}] {entry.get('goal', '')[:100]}")
        return selected
    return None


def choose_role(current_role: str | None, verbose: bool) -> str | None:
    """Select agent role via Rich UI. Returns role string or None if cancelled."""
    ui = AgentUI(console=console, verbose=verbose)

    options: list[dict[str, str]] = [
        {
            "value": "auto",
            "label": "Auto (automatic role detection)",
            "description": "Agent automatically detects role from your request (coder/devops/chat)",
        },
        {
            "value": "coder",
            "label": "Coder",
            "description": "Code generation, editing, debugging, refactoring, testing",
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


def choose_models(provider: str, current_models: dict[str, str], verbose: bool) -> dict[str, str] | None:
    ui = AgentUI(console=console, verbose=verbose)
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


# ── Custom Provider Wizard ═══════════════════════════════════════════════════


def configure_custom_provider_interactively(ui: AgentUI, verbose: bool) -> str | None:
    """RichUI wizard to create a new custom OpenAI-compatible provider.

    Walks the user through: name, base URL, API key (optional), display label,
    and models. Returns ``\"custom:<name>\"`` on success or ``None`` if cancelled.
    """
    console.print()
    console.print(Panel(
        "[bold bright_cyan]Create Custom Provider[/bold bright_cyan]\n"
        "[dim]Configure an OpenAI-compatible endpoint as a custom provider.[/dim]",
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
            console.print(f"[red]Provider '{name}' already exists. Choose a different name.[/red]")
            continue
        break

    # ── Step 2: Base URL ──────────────────────────────────────────────
    while True:
        base_url = ui.prompt_text(
            title="Enter base URL:",
            current_value="",
            help_text=(
                "OpenAI-compatible endpoint URL.\n"
                "Example: http://localhost:8080/v1 or https://api.example.com/v1"
            ),
        )
        if base_url is None:
            console.print("[yellow]Provider creation cancelled.[/yellow]")
            return None
        base_url = base_url.strip()
        if not base_url:
            console.print("[red]Base URL cannot be empty.[/red]")
            continue
        if not base_url.startswith(("http://", "https://")):
            console.print("[red]Base URL must start with http:// or https://[/red]")
            continue
        break

    # ── Step 3: Protocol ──────────────────────────────────────────────
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
                console.print(f"[red]Model ID for {role} cannot be empty.[/red]")
                return None
            models[role] = model_id

    # ── Step 7: Confirm ───────────────────────────────────────────────
    console.print()
    proto_display = "Anthropic-compatible" if protocol == "anthropic" else "OpenAI-compatible"
    summary_parts = [
        f"[bold]Provider ID:[/bold] {name}",
        f"[bold]Display name:[/bold] {label}",
        f"[bold]Protocol:[/bold] {proto_display}",
        f"[bold]Base URL:[/bold] {base_url}",
        f"[bold]API key:[/bold] {'[green]configured[/green]' if api_key else '[dim]none[/dim]'}",
        f"[bold]Models:[/bold]",
    ]
    for role in MODEL_ROLES:
        summary_parts.append(f"  {role_labels[role]:8s} {models[role]}")

    console.print(Panel(
        "\n".join(summary_parts),
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
    })
    provider_id = f"{CUSTOM_PROVIDER_PREFIX}{name}"
    console.print(f"[green]Custom provider [bold]{label}[/bold] created successfully![/green]")
    console.print(f"[dim]Provider ID: {provider_id}[/dim]")
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
        console.print(f"  [cyan]{label:8s}[/cyan] {model or '[red]not set[/red]'}")

    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
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

    if action is None or action == "keep":
        return dict(current_models) if action == "keep" else None

    if action == "custom":
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

        console.print(f"[green]Model set to [bold]{model_id}[/bold] for all roles.[/green]")
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
        console.print(f"  [cyan]{label:8s}[/cyan] {model or '[red]not set[/red]'}")

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
            console.print(f"[red]Invalid model id: {validation_error}[/red]")
            return None

        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            selected[role] = model_id

        console.print(f"[green]Model set to [bold]{model_id}[/bold] for all roles.[/green]")
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
        console.print(f"  [cyan]{label:8s}[/cyan] {model or '[red]not set[/red]'}")

    # ── Menu ════════════════════════════════════════════════════════════
    console.print()
    action = ui.choose_option(
        title="Select action:",
        options=[
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

    # ── Custom model id input ═══════════════════════════════════════════
    if action == "custom":
        # Show a single prompt — apply to all roles
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

        # Validate
        validation_error = validate_omniroute_model_id(model_id)
        if validation_error:
            console.print(f"[red]Invalid model id: {validation_error}[/red]")
            return None

        # Apply to all roles
        selected: dict[str, str] = {}
        for role in MODEL_ROLES:
            selected[role] = model_id

        console.print(f"[green]Model set to [bold]{model_id}[/bold] for all roles.[/green]")
        return selected

    # Unknown action (shouldn't happen with choose_option, but defensive)
    return None


def main() -> None:
    cli = argparse.ArgumentParser(description="Local AI agent")
    cli.add_argument(
        "--workspace",
        required=True,
        help="Path to the project workspace directory.",
    )
    args = cli.parse_args()

    max_steps = int(os.getenv("AGENT_MAX_STEPS", "50"))
    persist_logs = os.getenv("AGENT_PERSIST_LOGS", "1") not in {"0", "false", "False"}
    verbose = os.getenv("AGENT_VERBOSE", "0") in {"1", "true", "True", "yes", "on"}
    session_language = detect_request_language(os.getenv("LANG", ""), fallback="en")

    try:
        workspace_path = resolve_workspace_path(args.workspace)
        executor = SafeExecutor(workspace=workspace_path)
        agent, config, model_warnings = build_agent(
            executor=executor,
            max_steps=max_steps,
            persist_logs=persist_logs,
            verbose=verbose,
            language_hint=session_language,
        )
        # Wire HITL callbacks: pause/resume Live TUI during security prompts
        executor.set_hitl_callbacks(
            suspend_fn=agent._ui.stop_live,
            resume_fn=agent._ui.start_live,
        )
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)

    console.print("[dim]Local AI agent [/dim]Kvist [dim]started. [/dim]")
    console.print(f"[dim]Workspace: {executor.workspace}[/dim]")
    print_runtime_banner(config, max_steps, verbose, model_warnings)
    console.print("[red]Type '/bye' to exit. For multiline input, use Alt+Enter or Esc+Enter.[/red]")
    console.print("[dim]Command '/resume' — resume an interrupted task.[/dim]")
    console.print("[dim]Command '/provider' — switch the LLM provider.[/dim]")
    console.print("[dim]Command '/models' — switch models for the current provider.[/dim]")
    console.print("[dim]Command '/role' — select agent role (auto/coder/devops/chat).[/dim]")
    console.print("[dim]Command '/session' — manage and switch sessions.[/dim]")
    console.print("[dim]Command '/new' — reset to a fresh session.[/dim]")
    console.print("[dim]Command /about — to get more info about agent.[/dim]")
    console.print("[dim]Enter your task.[/dim]")

    # Show current session on startup
    session_name = getattr(agent, "current_session", "default")
    sessions = agent._list_sessions()
    console.print(f"[dim]Session: [bold]{session_name}[/bold] ({len(sessions)} saved)[/dim]")
    history = getattr(agent, "_session_history", [])
    if history:
        console.print("[dim]── Previous in this session ──[/dim]")
        for entry in history[-3:]:
            role_color = {"coder": "green", "devops": "magenta", "chat": "cyan"}.get(entry.get("role", ""), "white")
            console.print(f"  [{role_color}]{entry.get('role', '?')}[/{role_color}] {entry.get('goal', '')[:100]}")

    # Show current role on startup
    saved_role = get_saved_role()
    if saved_role and saved_role != "auto":
        role_colors = {"coder": "green", "devops": "magenta", "chat": "cyan"}
        role_color = role_colors.get(saved_role, "white")
        console.print(f"[{role_color}]Current role: {saved_role} (manual — auto-detection disabled)[/{role_color}]")
    else:
        console.print("[dim]Current role: auto (automatic detection enabled)[/dim]")

    try:
        while True:
            user_goal = multiline_input()
            user_goal = normalize_slash_command(user_goal)
            if user_goal and not user_goal.startswith("/"):
                session_language = detect_request_language(user_goal, fallback=session_language)
            if user_goal.lower() == "/bye":
                agent.save_session()
                console.print("[red]Exiting.[/red]")
                break

            if user_goal.lower() == "/resume":
                result = agent.resume()
                if result is None:
                    console.print("[yellow]No saved checkpoint to resume.[/yellow]")
                elif verbose:
                    print(f"\n[Final Answer]\n{result}")
                continue

            if user_goal.lower() == "/provider":
                selected_provider = choose_provider(config.provider, verbose)
                if selected_provider is None:
                    console.print("[yellow]Provider switch cancelled.[/yellow]")
                    continue
                if selected_provider == config.provider:
                    console.print("[dim]Provider unchanged.[/dim]")
                    continue

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
                    console.print(f"[red]{error}[/red]")
                    continue

                executor.set_hitl_callbacks(
                    suspend_fn=agent._ui.stop_live,
                    resume_fn=agent._ui.start_live,
                )

                console.print(
                    f"[green]Provider changed to {provider_label(config.provider)}.[/green]"
                )
                print_runtime_banner(config, max_steps, verbose, model_warnings)
                continue

            if user_goal.lower() == "/models":
                if config.provider == OMNIROUTE_PROVIDER:
                    # OmniRoute uses manual model input
                    ui = AgentUI(console=console, verbose=verbose)
                    selected_models = configure_omniroute_models_interactively(
                        ui,
                        current_models={
                            "fast": config.fast_model,
                            "smart": config.smart_model,
                            "planner": config.planner_model,
                        },
                    )
                    if selected_models is None:
                        console.print("[yellow]Model switch cancelled.[/yellow]")
                        continue
                elif config.provider == OPENROUTER_PROVIDER:
                    # OpenRouter: pick from curated list or enter custom id
                    ui = AgentUI(console=console, verbose=verbose)
                    selected_models = configure_openrouter_models_interactively(
                        ui,
                        current_models={
                            "fast": config.fast_model,
                            "smart": config.smart_model,
                            "planner": config.planner_model,
                        },
                    )
                    if selected_models is None:
                        console.print("[yellow]Model switch cancelled.[/yellow]")
                        continue
                elif is_custom_provider(config.provider):
                    # Custom provider: manual model input
                    ui = AgentUI(console=console, verbose=verbose)
                    selected_models = configure_custom_provider_models_interactively(
                        ui,
                        current_models={
                            "fast": config.fast_model,
                            "smart": config.smart_model,
                            "planner": config.planner_model,
                        },
                    )
                    if selected_models is None:
                        console.print("[yellow]Model switch cancelled.[/yellow]")
                        continue
                else:
                    model_options = get_model_options(config.provider)
                    if not model_options:
                        console.print(
                            "[yellow]No models available for current provider "
                            f"({provider_label(config.provider)}). "
                            "Use /provider to switch.[/yellow]"
                        )
                        continue
                    selected_models = choose_models(
                        provider=config.provider,
                        current_models={
                            "fast": config.fast_model,
                            "smart": config.smart_model,
                            "planner": config.planner_model,
                        },
                        verbose=verbose,
                    )
                    if selected_models is None:
                        console.print("[yellow]Model switch cancelled.[/yellow]")
                        continue

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
                if validation_issues:
                    for issue in validation_issues:
                        console.print(f"[red]{issue}[/red]")
                    continue

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
                    console.print(f"[red]{error}[/red]")
                    continue

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
                print_runtime_banner(config, max_steps, verbose, model_warnings)
                continue

            if user_goal.lower() == "/new":
                agent.new_session()
                console.print("[green]Session reset — fresh start.[/green]")
                continue

            if user_goal.lower() == "/session":
                if not hasattr(agent, "_list_sessions"):
                    console.print("[yellow]/session not available — restart the agent to load the latest code.[/yellow]")
                    continue
                result = choose_session(agent, verbose)
                if result is None:
                    console.print("[yellow]Session switch cancelled.[/yellow]")
                else:
                    console.print(f"[dim]Active session: [bold]{agent.current_session}[/bold][/dim]")
                continue

            if user_goal.lower() == "/role":
                saved_role = get_saved_role()
                selected_role = choose_role(saved_role, verbose)
                if selected_role is None:
                    console.print("[yellow]Role selection cancelled.[/yellow]")
                    continue
                save_role_selection(selected_role)
                display_role = selected_role if selected_role != "auto" else "auto (automatic detection)"
                console.print(f"[green]Role set to: {display_role}[/green]")
                continue

            validation_error = validate_user_goal(user_goal, language_hint=session_language)
            if validation_error:
                console.print(f"[yellow]{validation_error}[/yellow]")
                continue

            if user_goal.lower() == "/about" or user_goal.lower() == "/info" or user_goal.lower() == "/help":
                console.print("[green]This is Kvist: open-source AI agent for Windows/Linux. [/green]")
                console.print('''[red] 
Support: 
  Linux: Yes
  Windows: Yes
  macOS: Untested (or Experimental)
                [/red]''')
                console.print("[dim]\nKvist by Davud, © 2026 [/dim]")
                console.print("[dim] Author: Davud [/dim]")
                console.print("[dim] LICENCE: MIT [/dim]")
                console.print("[dim] Repository: [blue]https://github.com/davud-d/KVist[/blue] [/dim]")
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
                except Exception:
                    detected_role = route_role(user_goal)
                    decision = None
                if decision is not None:
                    detected_role = decision.role
            role_colors = {"coder": "green", "devops": "magenta", "chat": "cyan"}
            role_color = role_colors.get(detected_role, "white")
            source_tag = f" ({decision.source})" if decision else ""
            # Show "(manual)" when user has overridden automatic detection
            if saved_role and saved_role != "auto":
                source_tag = " (manual)"
            console.print(f"[{role_color}]⚡ Role: {detected_role}{source_tag}[/{role_color}]")

            result = agent.run(user_goal, routed_role=detected_role)
            if verbose:
                print(f"\n[Final Answer]\n{result}")
    except KeyboardInterrupt:
        agent.save_session()
        console.print("\n[red]Interrupted by user (Ctrl+C). \nExiting.[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()
