# -*- coding: utf-8 -*-
"""The system prompt keeps what every style shares first and the style last."""
from __future__ import annotations

import pytest

from kernel.agent.agent import LocalAgent
from kernel.agent.agent_model import build_role_system_prompt
from kernel.agent.agent_state import AgentConfig
from kernel.context.system_context import instructions_filename
from kernel.llm.prompt_prefix import SELECTED_STYLE_MARKER, split_style_tail
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI

_RULE = "Always run the linter before you say you are done."


def _agent(workspace, *, style="Default", chat_style="Default", session_workspace=None, **config) -> LocalAgent:
    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(workspace),
        config=AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, **config),
        ui=NullUI(),
        session_workspace=session_workspace,
    )
    agent._perf_logger = NullPerfLogger()
    agent._session_styles = {"agents": style, "chat": chat_style}
    return agent


def _write_instructions(folder, role):
    (folder / instructions_filename(role)).write_text(f"# Project\n\n{_RULE}\n", encoding="utf-8")


# ── The boundary ─────────────────────────────────────────────────────────────


def test_a_prompt_without_the_marker_is_all_shared():
    assert split_style_tail("rules\n\nmore rules") == ("rules\n\nmore rules", "")


def test_the_tail_starts_at_the_marker_line_and_runs_to_the_end():
    shared, tail = split_style_tail(f"rules\n\n{SELECTED_STYLE_MARKER}\n\nbe terse\n\n== VOICE ==")

    assert shared == "rules\n\n"
    assert tail == f"{SELECTED_STYLE_MARKER}\n\nbe terse\n\n== VOICE =="


def test_the_marker_inside_a_line_is_not_a_boundary():
    text = f"say {SELECTED_STYLE_MARKER} aloud\n\nrest"

    assert split_style_tail(text) == (text, "")


def test_the_last_marker_line_wins_so_quoted_project_text_cannot_move_the_boundary():
    quoted = f"{SELECTED_STYLE_MARKER}\nquoted by a project file\n\n"
    shared, tail = split_style_tail(f"{quoted}{SELECTED_STYLE_MARKER}\n\nstyle")

    assert shared == quoted
    assert tail == f"{SELECTED_STYLE_MARKER}\n\nstyle"


# ── The order of the sections ────────────────────────────────────────────────


@pytest.mark.parametrize("role", ["coder", "reader", "chat"])
def test_the_style_follows_the_project_instructions_and_voice_follows_the_style(tmp_path, role):
    _write_instructions(tmp_path, role)
    agent = _agent(tmp_path, voice_mode=role != "chat")

    prompt = build_role_system_prompt(agent, role)

    # The role prompts mention the header earlier in passing, so the section is the last occurrence.
    positions = [prompt.index(_RULE), prompt.rindex("## response style"), prompt.index(SELECTED_STYLE_MARKER)]
    assert positions == sorted(positions)
    if role != "chat":
        assert prompt.index("voice mode") > positions[-1]


@pytest.mark.parametrize("role", ["coder", "reader", "chat"])
def test_two_styles_share_everything_before_the_selected_style(tmp_path, role):
    _write_instructions(tmp_path, role)
    plain = build_role_system_prompt(_agent(tmp_path, style="Default", chat_style="Default"), role)
    other = build_role_system_prompt(_agent(tmp_path, style="Paranoid", chat_style="Honest"), role)

    shared_plain, tail_plain = split_style_tail(plain)
    shared_other, tail_other = split_style_tail(other)

    assert shared_plain == shared_other
    assert tail_plain != tail_other
    assert "== something the person can use ==" in shared_plain
    assert _RULE in shared_plain


@pytest.mark.parametrize("role", ["coder", "reader", "chat"])
def test_the_selected_style_stays_inside_the_response_style_section(tmp_path, role):
    # The role prompts' injection guard names the block "quoted under ## response style".
    prompt = build_role_system_prompt(_agent(tmp_path, style="Paranoid", chat_style="Honest"), role)

    section = prompt[prompt.rindex("## response style"):]

    assert SELECTED_STYLE_MARKER in section
    assert "\n## " not in section


def test_the_tail_of_the_working_roles_holds_the_voice_mode_section_after_the_style(tmp_path):
    prompt = build_role_system_prompt(_agent(tmp_path, voice_mode=True), "coder")

    _, tail = split_style_tail(prompt)

    assert tail.index("voice mode") > len(SELECTED_STYLE_MARKER)


def test_the_report_prompt_keeps_the_style_block_as_before(tmp_path):
    from kernel.agent.agent_model import build_report_system_prompt

    prompt = build_report_system_prompt(_agent(tmp_path))

    assert "## response style" in prompt
    assert SELECTED_STYLE_MARKER not in prompt


# ── Best-of executors ────────────────────────────────────────────────────────


def test_executors_in_two_worktrees_share_the_prompt_of_the_session_root(tmp_path):
    root = tmp_path / "root"
    first, second = tmp_path / "wt-a", tmp_path / "wt-b"
    for folder in (root, first, second):
        folder.mkdir()
    _write_instructions(root, "coder")
    a = _agent(first, style="Architect", session_workspace=root)
    b = _agent(second, style="Fast-and-dirty", session_workspace=root)

    prompt_a = build_role_system_prompt(a, "coder", native_tools=True)
    prompt_b = build_role_system_prompt(b, "coder", native_tools=True)
    (shared_a, tail_a), (shared_b, tail_b) = split_style_tail(prompt_a), split_style_tail(prompt_b)

    assert shared_a.encode("utf-8") == shared_b.encode("utf-8")
    assert tail_a != tail_b
    assert _RULE in shared_a


def test_a_plain_agent_still_reads_the_instructions_of_its_workspace(tmp_path):
    _write_instructions(tmp_path, "coder")

    assert _RULE in build_role_system_prompt(_agent(tmp_path), "coder")
