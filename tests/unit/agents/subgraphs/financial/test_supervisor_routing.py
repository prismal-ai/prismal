"""Unit tests for supervisor routing to financial_analyst."""
from __future__ import annotations


def test_financial_analyst_in_supervisor_members() -> None:
    """financial_analyst is in the supervisor MEMBERS list."""
    from lightagent.agents.supervisor import MEMBERS

    assert "financial_analyst" in MEMBERS


def test_supervisor_prompt_mentions_financial_analyst() -> None:
    """Supervisor system prompt includes financial_analyst routing rules."""
    from lightagent.agents.supervisor import _SYSTEM_PROMPT

    assert "financial_analyst" in _SYSTEM_PROMPT
    assert "financial" in _SYSTEM_PROMPT.lower()


def test_financial_agents_in_tool_registry_no_crash() -> None:
    """All 5 financial agent names are handled by tool_registry without crashing."""
    from lightagent.agents.tool_registry import get_tools_for_agent

    for agent_name in [
        "market_data_collector",
        "technical_analyst",
        "fundamental_analyst",
        "risk_sentiment_analyst",
        "report_generator",
    ]:
        tools = get_tools_for_agent(agent_name)
        assert isinstance(tools, list)
