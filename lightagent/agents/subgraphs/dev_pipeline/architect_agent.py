"""Architect agent node for the dev_pipeline subgraph.

Converts a :class:`~lightagent.agents.subgraphs.artifacts.UserStory` into a
:class:`~lightagent.agents.subgraphs.artifacts.TechnicalSpec`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import TechnicalSpec
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.architect")
otel = OTelManager()

_SYSTEM = (
    "You are a Software Architect. Given a user story, produce a technical"
    " specification.\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "id": "spec1",\n  "story_id": "...",\n  "architecture": "...",\n'
    '  "design_decisions": ["..."],\n  "technology_stack": ["python", "fastapi"]\n}'
)


async def architect_agent_node(state: AgentState) -> dict[str, Any]:
    """Produce a TechnicalSpec from the user story in metadata.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with TechnicalSpec stored in metadata.
    """
    with otel.start_span("dev_pipeline.architect") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "architect")

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        story_data = dp.get("user_story", {})

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"User story: {json.dumps(story_data)}"),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            spec = TechnicalSpec.model_validate(data)
        except Exception:
            spec = TechnicalSpec(
                id="spec1",
                story_id=story_data.get("id", "s1"),
                architecture=content,
            )

        dp["technical_spec"] = spec.model_dump()
        logger.info("architect.spec_created", spec_id=spec.id)
        return {
            "current_agent": "architect",
            "messages": [AIMessage(content=f"TechnicalSpec ready: {spec.id}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
