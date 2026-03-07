"""Data Analyst sub-agent node.

Specialist agent responsible for executing SQL queries with DuckDB, transforming
data with Polars, and creating charts to answer data-driven questions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.data_analyst")

_SYSTEM_PROMPT = """You are a data analysis specialist.

Your responsibilities:
- Execute SQL queries against data sources using DuckDB.
- Transform and aggregate DataFrames using Polars operations.
- Create informative charts (bar, line, scatter, etc.) to visualise findings.
- Explain results in plain language that non-technical stakeholders can understand.

Safety constraints:
- Only execute SELECT queries. Never run DROP, DELETE, UPDATE, INSERT, or DDL \
statements that modify data.
- If the user's request implies a destructive operation, explain why you cannot \
comply and suggest a read-only alternative.

Best practices:
- Always display a sample of the data (first few rows) before drawing conclusions.
- Include row counts and summary statistics where relevant.
- Highlight notable patterns, outliers, or anomalies in the data.
- When creating charts, choose the chart type that best communicates the insight."""


async def data_analyst_node(state: AgentState) -> dict[str, object]:
    """Execute the data_analyst sub-agent node with a ReAct tool loop.

    Runs a full ReAct loop with DuckDB, Polars, and chart tools so the LLM
    can iteratively query, transform and visualise data before returning a
    final answer.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'data_analyst'``
        and new ``messages`` containing the analysis results.
    """
    session_id = state.get("session_id")
    logger.debug("data_analyst_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("data_analyst")
    llm_with_tools = llm.bind_tools(tools)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response = await react_loop(
        llm_with_tools,
        tools,
        messages,
        agent_name="data_analyst",
        session_id=str(session_id) if session_id else None,
    )

    logger.info("data_analyst_complete", session_id=session_id)
    return {"current_agent": "data_analyst", "messages": [response]}


__all__ = ["data_analyst_node"]
