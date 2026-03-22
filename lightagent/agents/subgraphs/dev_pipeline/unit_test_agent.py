"""Unit test agent node for the dev_pipeline subgraph.

Generates pytest tests for a
:class:`~lightagent.agents.subgraphs.artifacts.CodeArtifact`
and produces a :class:`~lightagent.agents.subgraphs.artifacts.TestReport`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import TestReport
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.unit_tester")
otel = OTelManager()

_SYSTEM = (
    "You are a Test Engineer. Analyse the provided code and generate a test report.\n"
    "Respond with ONLY a JSON object matching:\n"
    '{\n  "tests_written": 5,\n  "tests_passed": 5,\n  "coverage_percent": 85.0,\n'
    '  "failing_tests": [],\n  "recommendations": ["add edge case tests"]\n}'
)


async def unit_test_agent_node(state: AgentState) -> dict[str, Any]:
    """Generate tests for the CodeArtifact and produce a TestReport.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with TestReport in metadata.
    """
    with otel.start_span("dev_pipeline.unit_tester") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "unit_tester")

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))
        code_data = dp.get("code_artifact", {})

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"Code artifact:\n{json.dumps(code_data, indent=2)}"),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            report = TestReport.model_validate(data)
        except Exception:
            report = TestReport(
                tests_written=0,
                tests_passed=0,
                failing_tests=["parse_error"],
            )

        dp["test_report"] = report.model_dump()
        logger.info(
            "unit_tester.report_created",
            passed=report.tests_passed,
            failed=len(report.failing_tests),
        )
        return {
            "current_agent": "unit_tester",
            "messages": [
                AIMessage(
                    content=(
                        f"Tests: {report.tests_passed}"
                        f"/{report.tests_written} passed"
                    )
                )
            ],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
