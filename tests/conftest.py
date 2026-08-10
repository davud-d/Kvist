from __future__ import annotations

import contextlib
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.text import Text

from agent import LocalAgent
from agent_state import AgentConfig, PlanInfo
from executor import SafeExecutor
from protocol import Action, AgentCommand
from terminal import (
    _controlled_child_path,
    _is_trusted_system_executable,
)

requires_posix = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-specific behavior (permission bits, Unix-only commands, "
    "process groups, symlinks) not exercised on Windows",
)


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

    @contextlib.contextmanager
    def live_markdown(self):
        def update(message):
            _ = message

        update.reasoning = lambda message: None
        yield update

    def print_final(self, *args, **kwargs):
        pass

    def render_user_markdown(self, message, fallback_style=""):
        _ = fallback_style
        return Text(message)

    def print_user_markdown(self, *args, **kwargs):
        pass

    def print_sandbox_notice(self, *args, **kwargs):
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


@pytest.fixture(autouse=True)
def _disable_write_approval_gate(monkeypatch):
    """Tests run without a TTY; the overwrite gate would deny every edit.

    Tests that exercise the gate itself re-enable it explicitly via
    AGENT_WRITE_APPROVAL=1.
    """
    monkeypatch.setenv("AGENT_WRITE_APPROVAL", "0")
    # General unit tests exercise policy/terminal behavior without depending
    # on the host's bwrap installation. Sandbox tests opt into auto/bwrap
    # explicitly when that behavior is under test.
    monkeypatch.setenv("AGENT_SANDBOX_MODE", "workspace")
    monkeypatch.setenv("AGENT_SANDBOX_NET", "deny")


def _find_trusted_system_python(tmp_path) -> tuple[str, Path] | None:
    controlled_path = _controlled_child_path(tmp_path)
    for name in ("python3", "python", "py"):
        found = shutil.which(name, path=controlled_path)
        if found is None:
            continue
        lookup = Path(found)
        try:
            resolved = lookup.resolve(strict=True)
        except OSError:
            continue
        if _is_trusted_system_executable(lookup, resolved):
            return name, resolved
    return None


@pytest.fixture
def trusted_python_name(tmp_path):
    """Return a Python command whose resolved binary passes terminal trust checks."""
    found = _find_trusted_system_python(tmp_path)
    if found is None:
        pytest.skip("no trusted system Python executable is available")
    name, _resolved = found
    return name


@pytest.fixture
def trusted_python_path(tmp_path):
    """Return the resolved path of a trusted-root system Python executable.

    Unlike ``trusted_python_name`` (a bare command for PATH lookup), this is
    the real target a venv launcher symlink must point at — CI runners that
    install Python outside the trusted system roots (e.g. hostedtoolcache)
    have no such binary, so tests depending on this fixture skip there.
    """
    found = _find_trusted_system_python(tmp_path)
    if found is None:
        pytest.skip("no trusted system Python executable is available")
    _name, resolved = found
    return resolved


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
            ui=NullUI(),
        )
        agent._perf_logger = NullPerfLogger()
        return agent

    return build


def make_command(action_type: str, args: dict, progress: int = 10, plan_step: str = "inspect", step_summary: str = "") -> AgentCommand:
    return AgentCommand(
        thought="Inspecting current state",
        plan_step=plan_step,
        progress=progress,
        user_message="Working on the task",
        action=Action(type=action_type, args=args),
        step_summary=step_summary,
    )


def make_plan() -> PlanInfo:
    return PlanInfo(
        goal="Test goal",
        complexity="simple",
        plan=["inspect files", "edit implementation", "verify result", "final summary"],
        success_criteria=["changes are applied", "verification is recorded"],
        risks=["test risk"],
    )
