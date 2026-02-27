"""RAG Agent sub-agent node.

Specialist agent that implements the Corrective RAG (CRAG) pipeline:
retrieve documents, grade relevance, discard low-quality results, fall back
to web search when necessary, and generate a grounded answer with citations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.tools import RAG_AGENT_TOOLS
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.rag_agent")

_SYSTEM_PROMPT = """You are a knowledge base specialist that uses Corrective RAG (CRAG).

Your pipeline:
1. RETRIEVE: Search the internal vector store for documents relevant to the query.
2. GRADE: Evaluate each retrieved document for relevance (score 0.0-1.0).
   - Discard any document with a relevance score below 0.5.
3. FALLBACK: If fewer than 2 relevant documents remain after grading, perform a
   supplementary web search to gather additional information.
4. GENERATE: Produce a comprehensive answer grounded in the retained documents.
   - Always cite the source of every factual claim (document name or URL).
   - Clearly distinguish between information from the knowledge base and from the web.
5. If no relevant information can be found anywhere, say so explicitly rather than
   hallucinating an answer."""


async def rag_agent_node(state: AgentState) -> dict[str, object]:
    """Execute the rag_agent sub-agent node using the CRAG pipeline.

    Retrieves and grades documents, falls back to web search when necessary,
    and generates a grounded answer with citations.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'rag_agent'``,
        new ``messages`` containing the grounded answer, and the existing
        ``retrieved_docs`` and ``doc_grades`` fields preserved.
    """
    logger.debug("rag_agent_node_called", session_id=state.get("session_id"))

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    llm_with_tools = llm.bind_tools(RAG_AGENT_TOOLS)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    retrieved_docs: list[dict[str, Any]] = state.get("retrieved_docs", [])
    doc_grades: list[float] = state.get("doc_grades", [])

    logger.info("rag_agent_complete", session_id=state.get("session_id"))
    return {
        "current_agent": "rag_agent",
        "messages": [response],
        "retrieved_docs": retrieved_docs,
        "doc_grades": doc_grades,
    }


__all__ = ["rag_agent_node"]
