"""QA agent node for the dev_pipeline subgraph.

Performs integration checks on a
:class:`~lightagent.agents.subgraphs.artifacts.CodeArtifact`
and produces a :class:`~lightagent.agents.subgraphs.artifacts.QAReport`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import QAReport
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.qa_agent")

_SYSTEM = (
    "You are a QA Engineer. Review the code for security issues and integration"
    " quality.\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "integration_tests_run": 3,\n  "integration_tests_passed": 3,\n'
    '  "security_findings": [],\n  "quality_score": 85.0,\n  "approved": true\n}'
)


async def qa_agent_node(state: AgentState) -> dict[str, Any]:
    """Run QA checks on the CodeArtifact and produce a QAReport.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with QAReport in metadata.
    """
    dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
    code_data = dp.get("code_artifact", {})

    llm = ProviderRegistry().get_llm()
    messages = [
        SystemMessage(content=_SYSTEM),
        HumanMessage(content=f"Code to review:\n{json.dumps(code_data, indent=2)}"),
    ]
    response = await llm.ainvoke(messages)
    content = str(response.content)

    try:
        data = json.loads(content)
        report = QAReport.model_validate(data)
    except Exception:
        report = QAReport(quality_score=0.0, approved=False)

    dp["qa_report"] = report.model_dump()
    logger.info(
        "qa_agent.report_created",
        quality_score=report.quality_score,
        approved=report.approved,
    )
    return {
        "current_agent": "qa_agent",
        "messages": [AIMessage(content=f"QA score: {report.quality_score}/100")],
        "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
    }
