"""File Manager sub-agent node.

Specialist agent responsible for reading and writing files on the local
filesystem, with a preference for the project's workspace directory and
strict restrictions against accessing system-level files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.file_manager")

_SYSTEM_PROMPT = """You are a file management specialist.

Your responsibilities:
- Read files to retrieve their content when asked.
- Write content to files when asked to persist data or results.
- For new files, default to using the ``data/workspace/`` directory unless the
  user specifies a different location.
- List available files when the user needs to know what exists.

Safety constraints:
- NEVER access, read, or write system files (e.g. ``/etc/``, ``/sys/``,
  ``/proc/``, ``~/.ssh/``, ``~/.aws/``, or any path outside the project).
- NEVER overwrite existing files without confirming with the user first.
- NEVER write secrets, API keys, or credentials to any file.
- If a requested path appears to be outside the safe workspace, refuse the
  operation and explain why."""


async def file_manager_node(state: AgentState) -> dict[str, object]:
    """Execute the file_manager sub-agent node with a ReAct tool loop.

    Runs a ReAct loop with read/write file tools so the LLM can perform
    multi-step file operations (read → transform → write) before returning
    a final confirmation.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'file_manager'``
        and new ``messages`` containing the operation result.
    """
    session_id = state.get("session_id")
    logger.debug("file_manager_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    tools = get_tools_for_agent("file_manager")
    llm_with_tools = llm.bind_tools(tools)

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response = await react_loop(
        llm_with_tools,
        tools,
        messages,
        agent_name="file_manager",
        session_id=str(session_id) if session_id else None,
    )

    logger.info("file_manager_complete", session_id=session_id)
    return {"current_agent": "file_manager", "messages": [response]}


__all__ = ["file_manager_node"]
