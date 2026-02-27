"""Planner sub-agent node.

Specialist agent responsible for decomposing complex, multi-step tasks into an
ordered plan that subsequent specialist agents can execute sequentially.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.planner")

_SYSTEM_PROMPT = """You are a task planning specialist.

Your responsibilities:
- Analyse the user's request and identify all the steps required to complete it.
- Decompose the request into a numbered, ordered list of concrete sub-tasks.
- For each sub-task, specify which specialist agent should handle it.
- Format every step EXACTLY as:
    N. [agent: <agent_name>] <Task description>
  where <agent_name> is one of: researcher, coder, rag_agent, critic, \
data_analyst, file_manager.
- Keep each step atomic — one agent, one clear action.
- Order steps logically so that each one builds on the results of the previous.
- If a task is simple enough to be handled in a single step, still produce \
a one-item list.

Available agents:
- researcher: Web search, RAG queries, reading files
- coder: Writing and executing code
- rag_agent: Internal knowledge-base Q&A
- critic: Reviewing and improving outputs
- data_analyst: SQL queries, DataFrame transforms, charts
- file_manager: File read/write operations"""


async def planner_node(state: AgentState) -> dict[str, object]:
    """Execute the planner sub-agent node.

    Calls the LLM directly (no tools) to produce a numbered plan, then
    parses the response into a ``task_plan`` list of step strings.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'planner'``,
        new ``messages`` containing the plan, ``task_plan`` as a list of
        step strings (lines starting with a digit), ``pending_tasks`` set
        to the same list, and ``completed_tasks`` reset to an empty list.
    """
    logger.debug("planner_node_called", session_id=state.get("session_id"))

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: BaseMessage = await llm.ainvoke(messages)

    # Parse numbered steps from the response text
    raw_content: str = str(response.content)
    task_plan: list[str] = [
        line.strip()
        for line in raw_content.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]

    logger.info(
        "planner_complete",
        session_id=state.get("session_id"),
        task_count=len(task_plan),
    )
    return {
        "current_agent": "planner",
        "messages": [response],
        "task_plan": task_plan,
        "pending_tasks": task_plan,
        "completed_tasks": [],
    }


__all__ = ["planner_node"]
