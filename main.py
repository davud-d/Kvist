# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from rich.console import Console

from agent import AgentConfig, LocalAgent
from executor import SafeExecutor


# Console for rich output (currently not used, but can be integrated later)
console = Console()

def safe_input(prompt: str) -> str:
    print(prompt, end="", flush=True)
    data = sys.stdin.buffer.readline()
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def main() -> None:
    cli = argparse.ArgumentParser(description="Локальный AI-агент")
    cli.add_argument(
        "--workspace", "-w",
        default=None,
        help="Путь к рабочей директории (по умолчанию: AGENT_WORKSPACE или ./workspace)",
    )
    args = cli.parse_args()

    # .env — ищем рядом со скриптом, не зависим от cwd
    env_path = Path(__file__).resolve().parent / "API_KEYS.env"
    load_dotenv(env_path, override=True)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Не найден OPENAI_API_KEY")

    if not api_key.startswith("sk-"):
        raise RuntimeError("OPENAI_API_KEY выглядит некорректно")

    fast_model = os.getenv("AGENT_FAST_MODEL", "gpt-4.1-mini")
    smart_model = os.getenv("AGENT_SMART_MODEL", "gpt-4.1")
    planner_model = os.getenv("AGENT_PLANNER_MODEL", smart_model)
    max_steps = int(os.getenv("AGENT_MAX_STEPS", "20"))
    persist_logs = os.getenv("AGENT_PERSIST_LOGS", "1") not in {"0", "false", "False"}
    verbose = os.getenv("AGENT_VERBOSE", "0") in {"1", "true", "True", "yes", "on"}

    client = OpenAI(api_key=api_key)
    workspace_path = args.workspace or os.getenv("AGENT_WORKSPACE", "workspace")
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

    console.print("Локальный AI-агент DevAgent запущен. Напиши задачу.")
    console.print(
        f"[green]Fast model: {fast_model}[/green] | [bold yellow]Smart model: {smart_model}[/bold yellow] | Max steps: {max_steps} | Verbose: {verbose}"
    )
    console.print("[red]Для выхода введи '/bye'.[/red]")    

    while True:
        user_goal = safe_input("\n> ")
        if user_goal.lower() == "/bye":
            console.print("[red]Выход.[/red]")
            break

        if not user_goal:
            continue

        result = agent.run(user_goal)
        if verbose:
            print(f"\n[Финальный ответ]\n{result}")


if __name__ == "__main__":
    main()