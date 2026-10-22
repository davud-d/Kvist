from __future__ import annotations

from kernel.agent.agent_state import RunState
from kernel.protocol.protocol import Action, AgentCommand


def make_web_command(query: str, text: str = "Need current SDK docs"):
    return AgentCommand(plan_step="inspect", action=Action(type="web_search", args={"query": query}), text=text)


def test_dependency_package_token(agent_factory):
    agent = agent_factory()
    agent._state.dependency_versions = {"openai": ">=1.40.0"}

    assert agent._contains_package_token("python_dotenv docs", "python-dotenv") is True


def test_prepare_web_search_sets_defaults_versions_and_allows_retries(agent_factory):
    agent = agent_factory()
    agent._state.dependency_versions = {"openai": ">=1.40.0"}
    command = make_web_command("openai migration")

    assert agent._prepare_web_search_command("external library migration", command) is None
    assert command.action.args["top_k"] == 5
    assert command.action.args["freshness"] == "any"
    assert "1.40.0" in command.action.args["query"]

    duplicate = make_web_command("openai migration 1.40.0")
    assert agent._prepare_web_search_command("external library migration", duplicate) is None


def test_prepare_web_search_has_no_topic_keyword_gate(agent_factory):
    agent = agent_factory()
    command = make_web_command("local variable names")
    assert agent._prepare_web_search_command("rename local files", command) is None


def test_fetch_url_defaults_and_fact_summaries(agent_factory):
    agent = agent_factory()
    command = AgentCommand(plan_step="inspect", action=Action(type="fetch_url", args={"url": "https://docs.python.org/3/"}))

    assert agent._prepare_fetch_url_command(command) is None
    assert command.action.args["max_chars"] == 12000
    assert agent._prepare_fetch_url_command(
        AgentCommand("inspect", Action("fetch_url", {"url": "ftp://x"}))
    ).startswith("fetch_url only")
    assert agent._extract_version_token(">=1.2.3,<2") == "1.2.3"
    assert agent._summarize_search_results([{"title": "A"}, {"title": "B"}, {"title": ""}, {"title": "C"}]) == "A; B; C"
    assert agent._extract_fact_from_content("u", "Title", " line one \n\nline two ") == "Title: line one line two"
    assert agent._normalize_message(" A\n B ") == "a b"
