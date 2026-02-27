"""Researcher sub-agent node.

Specialist agent responsible for searching the web and querying the RAG
knowledge base to gather information and synthesise findings with citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.tools import RESEARCHER_TOOLS
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.researcher")

_SYSTEM_PROMPT = """You are a research specialist with access to web search and a \
knowledge base.

Your responsibilities:
- Search the web for up-to-date information relevant to the user's query.
- Query the internal knowledge base (RAG) for domain-specific documents.
- Read referenced files when needed for additional context.
- Always cite your sources clearly, including URLs and document names.
- Synthesise findings into a concise, well-structured response.
- If conflicting information is found, present all perspectives and note discrepancies.
- Flag when information may be outdated or uncertain."""


async def researcher_node(state: AgentState) -> dict[str, object]:
    """Execute the researcher sub-agent node.

    Invokes the LLM with research-specific tools (web search, RAG search,
    file read) and returns synthesised findings with source citations.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'researcher'``
        and new ``messages`` containing the research results.
    """
    logger.debug("researcher_node_called", session_id=state.get("session_id"))

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    llm_with_tools = llm.bind_tools(RESEARCHER_TOOLS)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    logger.info("researcher_complete", session_id=state.get("session_id"))
    return {"current_agent": "researcher", "messages": [response]}


__all__ = ["researcher_node"]
