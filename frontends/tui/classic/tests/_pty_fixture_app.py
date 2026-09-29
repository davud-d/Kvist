"""Credential-free child entry point for outer-PTY CLI tests.

Only provider construction/model calls are replaced. The real CLI loop,
terminal screen, UI, session persistence, executor, and shutdown paths run
unchanged in the spawned process.
"""
from __future__ import annotations

import time

from rich.console import Console

from kernel import main
from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from terminal_banner import TerminalBannerUI


class _OfflineClient:
    def chat(self, *args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("offline PTY fixture has no model")


class _OfflineAgent(LocalAgent):
    def _init_tiktoken_encoding(self):
        return None

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        if "trigger safe failure" in user_goal.casefold():
            raise RuntimeError("synthetic PTY task failure")
        if "wait for cancellation" in user_goal.casefold():
            time.sleep(30)
            raise AssertionError("cancelled PTY task unexpectedly completed")

        if "long unicode response" in user_goal.casefold():
            lines = [
                f"строка {index:03d} — café — 🙂 — 界 — " + ("x" * 90)
                for index in range(80)
            ]
            response = "LONG-UNICODE-BEGIN\n" + "\n".join(lines) + "\nLONG-UNICODE-END"
        else:
            response = f"OFFLINE-RESPONSE: {user_goal}"

        role = routed_role or "chat"
        self._ui.print_final(response)
        self._add_to_session_history(user_goal, role, response)
        return response


def _build_offline_agent(
    executor,
    max_steps,
    persist_logs,
    verbose,
    provider_override=None,
    models_override=None,
    language_hint=None,
    ui=None,
):
    _ = provider_override, models_override, language_hint, ui
    config = AgentConfig(
        provider="openai",
        fast_model="offline-fast",
        smart_model="offline-smart",
        max_steps=max_steps,
        persist_logs=persist_logs,
        stream=False,
        verbose=verbose,
    )
    console = Console()
    ui = TerminalBannerUI(console=console, verbose=verbose)
    return (
        _OfflineAgent(_OfflineClient(), executor, config, ui=ui, console=console),
        config,
        [],
    )


main.build_agent = _build_offline_agent
main.main()
