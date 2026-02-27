"""Data Analyst sub-agent node.

Specialist agent responsible for executing SQL queries with DuckDB, transforming
data with Polars, and creating charts to answer data-driven questions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.tools import DATA_ANALYST_TOOLS
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
    """Execute the data_analyst sub-agent node.

    Invokes the LLM with data-analysis tools (DuckDB query, Polars transform,
    chart creation) and returns insights explained in plain language.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'data_analyst'``
        and new ``messages`` containing the analysis results.
    """
    logger.debug("data_analyst_node_called", session_id=state.get("session_id"))

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    llm_with_tools = llm.bind_tools(DATA_ANALYST_TOOLS)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    logger.info("data_analyst_complete", session_id=state.get("session_id"))
    return {"current_agent": "data_analyst", "messages": [response]}


__all__ = ["data_analyst_node"]
