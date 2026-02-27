"""Critic sub-agent node.

Specialist agent responsible for critically reviewing outputs produced by other
agents, scoring quality on a 0.0-1.0 scale, and listing specific improvements
when the score falls below the acceptance threshold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.tools import CRITIC_TOOLS
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.critic")

_SYSTEM_PROMPT = """You are a critical reviewer responsible for evaluating outputs.

Your evaluation criteria:
- ACCURACY: Is every factual claim correct and well-supported?
- COMPLETENESS: Does the response fully address all parts of the original request?
- CLARITY: Is the response clear, concise, and easy to understand?
- SAFETY: Does the response avoid harmful, biased, or inappropriate content?

Your output format:
1. Provide a score between 0.0 (completely unacceptable) and 1.0 (perfect).
2. If the score is below 0.8, list specific, actionable improvements.
3. Structure your review as:
   SCORE: <float>
   STRENGTHS: <brief bullet list>
   IMPROVEMENTS: <numbered list of required changes, empty if score >= 0.8>

Be constructive and specific. Vague feedback like "improve clarity" is not helpful."""


async def critic_node(state: AgentState) -> dict[str, object]:
    """Execute the critic sub-agent node.

    Evaluates the most recent agent output for accuracy, completeness,
    clarity, and safety, then returns a scored review.  Increments
    ``iteration_count`` to track the number of self-critique cycles.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'critic'``,
        new ``messages`` containing the review, and ``iteration_count``
        incremented by 1.
    """
    logger.debug("critic_node_called", session_id=state.get("session_id"))

    current_iteration: int = state.get("iteration_count", 0)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    llm_with_tools = llm.bind_tools(CRITIC_TOOLS)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    logger.info(
        "critic_complete",
        session_id=state.get("session_id"),
        iteration=current_iteration + 1,
    )
    return {
        "current_agent": "critic",
        "messages": [response],
        "iteration_count": current_iteration + 1,
    }


def critic_router(state: AgentState) -> str:
    """Route unconditionally back to the supervisor after critique.

    The critic never terminates the graph itself — it always returns
    control to the supervisor so that the supervisor can decide whether
    to accept the output or request further refinement.

    Args:
        state: Current agent state from LangGraph (not used for routing logic).

    Returns:
        Always ``"supervisor"``.
    """
    _ = state  # routing is unconditional; state is accepted to satisfy LangGraph API
    return "supervisor"


__all__ = ["critic_node", "critic_router"]
