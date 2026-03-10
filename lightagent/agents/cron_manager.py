"""CronManager sub-agent node.

Handles scheduling, listing, pausing, resuming, and removing cron jobs.
The agent interprets natural-language scheduling requests and translates
them into cron expressions + CronManager API calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.cron_manager")

_SYSTEM_PROMPT = """You are a scheduling specialist that manages recurring agent tasks.

You can:
- Schedule one-time reminders using cron_once
- Schedule recurring tasks using cron_add with cron expressions
- List existing scheduled jobs
- Pause, resume, or remove jobs

**CRITICAL — always call get_current_time first:**
Before scheduling anything, call get_current_time to know the host's local
time and timezone. Never ask the user what time it is.

**Choosing the right scheduling tool:**
- "In 5 minutes", "in 2 hours", "tomorrow at 9", "remind me at 15:00" →
  use cron_once with run_at = current_time + offset (YYYY-MM-DD HH:MM:SS)
- "Every day at 9 AM", "every Monday", "every 30 minutes" →
  use cron_add with a cron expression

Common cron expressions (for recurring jobs only):
- Every day at 9 AM: "0 9 * * *"
- Every hour: "0 * * * *"
- Every Monday at 8 AM: "0 8 * * 1"
- Every 30 minutes: "*/30 * * * *"
- First day of month at midnight: "0 0 1 * *"

Scheduling workflow:
1. Call get_current_time to get local time + timezone.
2. Decide: one-time (cron_once) or recurring (cron_add)?
3. Suggest a clear job name (snake_case, e.g. 'email_reminder').
4. Call the appropriate tool.
5. Confirm the scheduled time to the user.

When listing jobs, use cron_list and present the results clearly.
For pause/resume/remove, confirm the action with the job name.
"""


async def cron_manager_node(state: AgentState) -> dict[str, object]:
    """Execute the cron-manager sub-agent node.

    Interprets scheduling requests and manages cron job lifecycle using
    the cron management tools (cron_add, cron_list, cron_pause,
    cron_resume, cron_remove).

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'cron_manager'``
        and new ``messages`` containing the scheduling result.
    """
    session_id = state.get("session_id")
    logger.debug("cron_manager_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    active_tools = get_tools_for_agent("cron_manager")
    llm_with_tools = llm.bind_tools(active_tools)

    system = [SystemMessage(content=_SYSTEM_PROMPT)]
    messages = list(state["messages"])

    response = await react_loop(
        llm_with_tools,
        active_tools,
        system + messages,
        agent_name="cron_manager",
        max_iterations=3,
        session_id=str(session_id) if session_id else None,
    )

    logger.info("cron_manager_complete", session_id=session_id)
    return {"current_agent": "cron_manager", "messages": [response]}


__all__ = ["cron_manager_node"]
