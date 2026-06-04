from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import LocalAgent
from agent_state import AgentConfig, PlanInfo
from executor import SafeExecutor
from protocol import Action, AgentCommand


class NullUI:
    def start_live(self):
        pass

    def stop_live(self):
        pass

    def update_live(self, *args, **kwargs):
        pass

    def print_plan(self, *args, **kwargs):
        pass

    def print_step_header(self, *args, **kwargs):
        pass

    def print_final(self, *args, **kwargs):
        pass

    def debug_print(self, *args, **kwargs):
        pass


class NullPerfLogger:
    def log_api_request(self, **kwargs):
        pass

    def log_render(self, **kwargs):
        pass

    def log_event_loop_lag(self, **kwargs):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class FakeLLMClient:
    def __init__(self, content: str = '{"ok": true}', stream_chunks: list[str] | None = None):
        self.requests: list[dict] = []
        self.content = content
        self.stream_chunks = stream_chunks or []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if kwargs.get("stream"):
            return [
                SimpleNamespace(
                    choices=[
                        SimpleNamespace(delta=SimpleNamespace(content=chunk))
                    ]
                )
                for chunk in self.stream_chunks
            ]
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content))
            ]
        )


@pytest.fixture
def agent_factory(tmp_path):
    def build(**config_overrides):
        config_values = {
            "persist_logs": False,
            "cache_enabled": True,
            "api_request_interval": 0,
            "retry_base_delay": 0,
        }
        config_values.update(config_overrides)
        config = AgentConfig(**config_values)
        agent = LocalAgent(
            client=FakeLLMClient(),
            executor=SafeExecutor(tmp_path),
            config=config,
        )
        agent._ui = NullUI()
        agent._perf_logger = NullPerfLogger()
        return agent

    return build


def make_command(action_type: str, args: dict, progress: int = 10, plan_step: str = "inspect") -> AgentCommand:
    return AgentCommand(
        thought="Inspecting current state",
        plan_step=plan_step,
        progress=progress,
        user_message="Working on the task",
        action=Action(type=action_type, args=args),
    )


def make_plan() -> PlanInfo:
    return PlanInfo(
        goal="Test goal",
        complexity="simple",
        plan=["inspect files", "edit implementation", "verify result", "final summary"],
        success_criteria=["changes are applied", "verification is recorded"],
        risks=["test risk"],
    )
