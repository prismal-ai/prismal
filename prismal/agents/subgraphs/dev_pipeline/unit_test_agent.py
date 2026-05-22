# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""Unit test agent node for the dev_pipeline subgraph.

Generates pytest tests for a
:class:`~prismal.agents.subgraphs.artifacts.CodeArtifact`
and produces a :class:`~prismal.agents.subgraphs.artifacts.TestReport`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.subgraphs.artifacts import TestReport
from prismal.monitoring.otel import OTelManager
from prismal.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from prismal.agents.state import AgentState

logger = structlog.get_logger("prismal.subgraphs.dev_pipeline.unit_tester")
otel = OTelManager()

_SYSTEM = """You are a Test Engineer for the dev_pipeline subgraph.

## Purpose
Design a pytest suite for the upstream `CodeArtifact` and return a
`TestReport` describing what was written, what would pass, and the
estimated coverage.

## Input
One HumanMessage containing the JSON dump of the `CodeArtifact` read
from `state.metadata.dev_pipeline.code_artifact`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `TestReport` Pydantic schema:

    {
      "tests_written": 5,                 // int >= 0
      "tests_passed": 5,                  // int >= 0, <= tests_written
      "coverage_percent": 85.0,           // float in [0.0, 100.0]
      "failing_tests": [],                // list[str] of test names
      "recommendations": [                // list[str], actionable items
        "Add a boundary test for empty input"
      ]
    }

## Success Criteria
The `TestReport` is acceptable when ALL of the following hold:
- **Covers behaviour**: `tests_written` >= 1 test per public function
  and per acceptance criterion the code implements.
- **Coverage**: `coverage_percent >= 85.0` for production-ready.
- **Consistency**: `tests_passed <= tests_written`, and
  `len(failing_tests) == tests_written - tests_passed`.
- **Actionable recommendations**: each entry names a concrete test case
  to add (e.g. "empty list", "unicode input"), not generic advice.
- **No fabrication**: failing test names reference actual test function
  names you described in recommendations, not invented labels.

## Instructions
1. Parse the `CodeArtifact` JSON from the HumanMessage.
2. Enumerate the public functions/methods and their edge cases.
3. Plan one test per happy path + one per documented edge case.
4. Compute `coverage_percent` honestly — if you skipped a branch, say so.
5. Populate `failing_tests` with the names of any test you expect to
   fail (ideally empty).
6. Emit JSON only.

## Background
- Artifact schema: `prismal/agents/subgraphs/artifacts.py::TestReport`.
- Parsed via `TestReport.model_validate`; parse failure stores an
  empty report with `failing_tests=["parse_error"]`.
- Downstream: the QA agent uses `coverage_percent` as one input to its
  quality score; reviewer uses `tests_passed/tests_written` ratio.

## Examples

### Positive
Input: a FastAPI PDF export endpoint.

{
  "tests_written": 6,
  "tests_passed": 6,
  "coverage_percent": 92.5,
  "failing_tests": [],
  "recommendations": [
    "Add a load test with 50 concurrent export requests to verify WeasyPrint thread-safety",
    "Add a property-based test that any non-empty report produces a PDF whose first 4 bytes are '%PDF'"
  ]
}

### Negative (what NOT to do)
{
  "tests_written": 1,
  "tests_passed": 5,
  "coverage_percent": 150.0,
  "failing_tests": ["test_all_the_things"],
  "recommendations": ["write more tests"]
}

Problems:
- `tests_passed > tests_written` — impossible and breaks the gate.
- `coverage_percent > 100` — schema violation.
- `failing_tests` has 1 entry while `tests_written - tests_passed = -4`.
- `recommendations` is generic and not actionable.
"""


async def unit_test_agent_node(state: AgentState) -> dict[str, Any]:
    """Generate tests for the CodeArtifact and produce a TestReport.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with TestReport in metadata.
    """
    with otel.start_span("dev_pipeline.unit_tester") as span:
        span.set_attribute("prismal.subgraph", "dev_pipeline")
        span.set_attribute("prismal.agent", "unit_tester")

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
                AIMessage(content=(f"Tests: {report.tests_passed}/{report.tests_written} passed"))
            ],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
