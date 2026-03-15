"""Product Owner agent node for the dev_pipeline subgraph.

Generates a :class:`~lightagent.agents.subgraphs.artifacts.UserStory`
from the user's feature request.  The story is stored under
``state["metadata"]["dev_pipeline"]["user_story"]``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import UserStory
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.po_agent")
otel = OTelManager()

_SYSTEM = (
    "You are a Product Owner. Given a feature request, write a structured user story.\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "id": "s1",\n  "title": "...",\n'
    '  "description": "As a [role], I want [action], so that [benefit]",\n'
    '  "acceptance_criteria": ["Given ..., When ..., Then ..."],\n'
    '  "priority": "MUST"\n}'
)


async def po_agent_node(state: AgentState) -> dict[str, Any]:
    """Generate a UserStory artifact from the latest human message.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ``current_agent`` and updated ``metadata``.
    """
    with otel.start_span("dev_pipeline.po_agent") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "po_agent")

        llm = ProviderRegistry().get_llm()
        messages = [SystemMessage(content=_SYSTEM), *list(state["messages"][-3:])]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            story = UserStory.model_validate(data)
        except Exception:
            story = UserStory(id="s1", title="Feature", description=content)

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        dp["user_story"] = story.model_dump()

        logger.info("po_agent.story_created", story_id=story.id, title=story.title)
        return {
            "current_agent": "po_agent",
            "messages": [AIMessage(content=f"UserStory created: {story.title}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
