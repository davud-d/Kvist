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

from agent import AgentConfig, LocalAgent
from executor import SafeExecutor
from language_utils import detect_request_language
from llm_clients import (
    create_llm_client,
    configured_freeflow_backends,
    FREEFLOW_PROVIDER,
    get_model_options,
    MODEL_ROLES,
    OPENAI_API_KEY_KEY,
    OPENAI_PROVIDER,
    provider_supports_streaming,
    resolve_openai_accessible_models,
    normalize_provider,
    provider_label,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
    validate_model_selection,
)
from ui import AgentUI
from user_config import (
    ensure_provider_credentials,
    get_saved_models,
    get_saved_provider,
    get_saved_provider_or_none,
    load_available_credentials,
    prompt_provider_choice,
    save_models_for_provider,
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


def sleep(seconnds: int):
    time.sleep(seconnds)


def resolve_startup_provider(provider_override: str | None = None) -> str:
    if provider_override:
        return normalize_provider(provider_override)

    env_provider = os.getenv("AGENT_LLM_PROVIDER")
    if env_provider:
        return normalize_provider(env_provider)

    saved_provider = get_saved_provider_or_none()
    if saved_provider:
        return saved_provider

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
    models, warnings = resolve_models_for_provider(
        provider,
        models_override or get_saved_models(provider),
        language_hint=language_hint,
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
    return ui.choose_option(
        title="Select LLM Provider",
        current_value=current_provider,
        options=[
            {
                "value": "openai",
                "label": "OpenAI",
                "description": "Strict JSON mode with GPT models",
            },
            {
                "value": "freeflow",
                "label": "FreeFlow LLM",
                "description": "Gemini/Groq via FreeFlow, JSON — best effort",
            },
        ],
    )


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

    console.print("[dim]Local AI agent [/dim]DevAgentXD [dim]started. [/dim]")
    console.print(f"[dim]Workspace: {executor.workspace}[/dim]")
    sleep(1)
    print_runtime_banner(config, max_steps, verbose, model_warnings)
    console.print("[red]Type '/bye' to exit. For multiline input, use Alt+Enter or Esc+Enter.[/red]")
    console.print("[dim]Command '/resume' — resume an interrupted task.[/dim]")
    console.print("[dim]Command '/provider' — switch the LLM provider.[/dim]")
    console.print("[dim]Command '/models' — switch models for the current provider.[/dim]")
    console.print("[dim]Command /about — to get more info about agent.[/dim]")
    sleep(2)
    console.print("[dim]Enter your task.[/dim]")

    try:
        while True:
            user_goal = multiline_input()
            user_goal = normalize_slash_command(user_goal)
            if user_goal and not user_goal.startswith("/"):
                session_language = detect_request_language(user_goal, fallback=session_language)
            if user_goal.lower() == "/bye":
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

                save_models_for_provider(config.provider, selected_models)
                console.print("[green]Models updated.[/green]")
                print_runtime_banner(config, max_steps, verbose, model_warnings)
                continue

            if not user_goal:
                continue
            
            if user_goal.lower() == "/about" or user_goal.lower() == "/info" or user_goal.lower() == "/help":
                console.print("[green]This is DevAgentXD: open-source AI agent for Windows/Linux. [/green]")
                console.print('''[red] 
Support: 
  Linux: Yes
  Windows: Yes
  macOS: Untested (or Experimental)
                [/red]''')
                console.print("[dim]\nDevAgentXD by Davud, © 2026 [/dim]")
                console.print("[dim] Author: Davud [/dim]")
                console.print("[dim] LICENCE: MIT [/dim]")
                console.print("[dim] Repository: [blue]https://github.com/davud-d/DevAgentXD[/blue] [/dim]")
                console.print("[dim] Release Version: 1.0 [/dim]")
                console.print("[orange]\nThank you for using DevAgentXD [/orange]")
                continue

            from role_router import route_role_decision_with_llm, route_role
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
            console.print(f"[{role_color}]⚡ Role: {detected_role}{source_tag}[/{role_color}]")

            result = agent.run(user_goal, routed_role=detected_role)
            if verbose:
                print(f"\n[Final Answer]\n{result}")
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted by user (Ctrl+C). \nExiting.[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()