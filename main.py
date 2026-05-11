# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from openai import OpenAI

from rich.console import Console

from agent import AgentConfig, LocalAgent
from executor import SafeExecutor
from user_config import load_api_key_or_bootstrap


console = Console()


def resolve_workspace_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser().resolve()
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.exists():
        raise RuntimeError("Workspace path does not exist.")
    if not candidate.is_dir():
        raise RuntimeError("Workspace path must be a directory.")
    return candidate

def safe_input(prompt: str) -> str:
    print(prompt, end="", flush=True)
    data = sys.stdin.buffer.readline()
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def multiline_input() -> str:
    """Read user input. A trailing backslash enables multiline mode."""
    lines: list[str] = []
    first_line = safe_input("\n> ")
    if not first_line:
        return ""
    if not first_line.endswith("\\"):
        return first_line
    lines.append(first_line[:-1])
    while True:
        line = safe_input("... ")
        if not line or not line.endswith("\\"):
            lines.append(line)
            break
        lines.append(line[:-1])
    return "\n".join(lines).strip()


def main() -> None:
    cli = argparse.ArgumentParser(description="Local AI agent")
    cli.add_argument(
        "--workspace",
        required=True,
        help="Path to the project workspace directory.",
    )
    args = cli.parse_args()

    fast_model = os.getenv("AGENT_FAST_MODEL", "gpt-5.4-nano")
    smart_model = os.getenv("AGENT_SMART_MODEL", "gpt-5.4-mini")
    planner_model = os.getenv("AGENT_PLANNER_MODEL", "gpt-4o")
    max_steps = int(os.getenv("AGENT_MAX_STEPS", "40"))
    persist_logs = os.getenv("AGENT_PERSIST_LOGS", "1") not in {"0", "false", "False"}
    verbose = os.getenv("AGENT_VERBOSE", "0") in {"1", "true", "True", "yes", "on"}

    try:
        workspace_path = resolve_workspace_path(args.workspace)
        api_key = load_api_key_or_bootstrap(validation_model=fast_model)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)

    client = OpenAI(api_key=api_key)
    executor = SafeExecutor(workspace=workspace_path)
    config = AgentConfig(
        fast_model=fast_model,
        smart_model=smart_model,
        planner_model=planner_model,
        max_steps=max_steps,
        persist_logs=persist_logs,
        verbose=verbose,
    )
    agent = LocalAgent(client=client, executor=executor, config=config)

    console.print("Local AI agent DevAgentXD started. Enter your task.")
    console.print(f"[dim]Workspace: {executor.workspace}[/dim]")
    console.print(
        f"[green]Fast model: {fast_model}[/green] | [bold yellow]Smart model: {smart_model}[/bold yellow] | Max steps: {max_steps} | Verbose: {verbose}"
    )
    console.print("[red]Type '/bye' to exit. Multiline input: end a line with '\\'.[/red]")
    console.print("[dim]Command '/resume' — resume an interrupted task.[/dim]")

    try:
        while True:
            user_goal = multiline_input()
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

            if not user_goal:
                continue

            result = agent.run(user_goal)
            if verbose:
                print(f"\n[Final Answer]\n{result}")
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted by user (Ctrl+C). \nExiting.[/red]")
        sys.exit(130)


if __name__ == "__main__":
    main()
