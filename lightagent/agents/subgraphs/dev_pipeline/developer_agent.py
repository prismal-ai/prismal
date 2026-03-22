"""Developer agent node for the dev_pipeline subgraph.

Produces a :class:`~lightagent.agents.subgraphs.artifacts.CodeArtifact` from
a :class:`~lightagent.agents.subgraphs.artifacts.TechnicalSpec`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import CodeArtifact
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.developer")
otel = OTelManager()

_SYSTEM = (
    "You are a Senior Software Developer. Given a technical spec, write clean"
    " Python code.\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "language": "python",\n  "file_path": "module/file.py",\n'
    '  "content": "# full source code here",\n  "dependencies": ["package>=version"]\n}'
)


async def developer_agent_node(state: AgentState) -> dict[str, Any]:
    """Write code implementing the TechnicalSpec.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with CodeArtifact in metadata.
    """
    with otel.start_span("dev_pipeline.developer") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "developer")

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        spec_data = dp.get("technical_spec", {})

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"Technical spec: {json.dumps(spec_data)}"),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            artifact = CodeArtifact.model_validate(data)
        except Exception:
            artifact = CodeArtifact(
                file_path="generated/code.py",
                content=content,
            )

        dp["code_artifact"] = artifact.model_dump()
        logger.info("developer.code_created", file_path=artifact.file_path)
        return {
            "current_agent": "developer",
            "messages": [AIMessage(content=f"Code written: {artifact.file_path}")],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
