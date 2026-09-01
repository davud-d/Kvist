from __future__ import annotations

from agent_state import RunState
from protocol import Action, AgentCommand


def make_web_command(query: str, thought: str = "Need current SDK docs"):
    return AgentCommand(
        thought=thought,
        plan_step="inspect",
        user_message="Searching docs",
        action=Action(type="web_search", args={"query": query}),
    )


def test_web_topic_detection_and_package_token(agent_factory):
    agent = agent_factory()
    agent._state.dependency_versions = {"openai": ">=1.40.0"}

    assert agent._is_web_topic("fix OpenAI import error", "openai docs") is True
    assert agent._is_web_topic("local refactor", "pure project code") is False
    assert agent._contains_package_token("python_dotenv docs", "python-dotenv") is True


def test_prepare_web_search_sets_defaults_versions_and_blocks_duplicates(agent_factory):
    agent = agent_factory()
    agent._state.dependency_versions = {"openai": ">=1.40.0"}
    command = make_web_command("openai migration")

    assert agent._prepare_web_search_command("external library migration", command) is None
    assert command.action.args["top_k"] == 5
    assert command.action.args["freshness"] == "any"
    assert "1.40.0" in command.action.args["query"]

    duplicate = make_web_command("openai migration 1.40.0")
    assert "Repeating identical" in agent._prepare_web_search_command("external library migration", duplicate)


def test_prepare_web_search_rejects_non_web_topic(agent_factory):
    agent = agent_factory()
    command = make_web_command("local variable names")
    assert "only allowed" in agent._prepare_web_search_command("rename local files", command)


def test_fetch_url_defaults_and_fact_summaries(agent_factory):
    agent = agent_factory()
    command = AgentCommand(
        thought="Read docs",
        plan_step="inspect",
        user_message="Reading URL",
        action=Action(type="fetch_url", args={"url": "https://docs.python.org/3/"}),
    )

    assert agent._prepare_fetch_url_command(command) is None
    assert command.action.args["max_chars"] == 12000
    assert agent._prepare_fetch_url_command(
        AgentCommand("x", "inspect", "x", Action("fetch_url", {"url": "ftp://x"}))
    ).startswith("fetch_url only")
    assert agent._extract_version_token(">=1.2.3,<2") == "1.2.3"
    assert agent._summarize_search_results([{"title": "A"}, {"title": "B"}, {"title": ""}, {"title": "C"}]) == "A; B; C"
    assert agent._extract_fact_from_content("u", "Title", " line one \n\nline two ") == "Title: line one line two"
    assert agent._normalize_message(" A\n B ") == "a b"
