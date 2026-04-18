# Prompt constants contain long JSON example lines.
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
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.qa_agent")
otel = OTelManager()

_SYSTEM = """You are a QA Engineer for the dev_pipeline subgraph.

## Purpose
Perform integration and security review on the upstream `CodeArtifact`
and emit a `QAReport` with a quality score in [0, 100] plus an approval
flag.

## Input
One HumanMessage containing the JSON dump of the `CodeArtifact` read
from `state.metadata.dev_pipeline.code_artifact`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `QAReport` Pydantic schema:

    {
      "integration_tests_run": 3,         // int >= 0
      "integration_tests_passed": 3,      // int >= 0, <= run
      "security_findings": [              // list[str]
        "OWASP A03:2021 - SQL injection risk on line X"
      ],
      "quality_score": 85.0,              // float in [0.0, 100.0]
      "approved": true                    // bool
    }

## Success Criteria
The `QAReport` is acceptable when ALL of the following hold:
- **Integration consistency**: `integration_tests_passed <=
  integration_tests_run`.
- **Security coverage**: every finding references a concrete OWASP
  category or CWE id and cites the file/line or function where it
  was detected.
- **Score semantics**: `quality_score >= 80.0` iff `approved == true`;
  any critical (blocker) security finding forces
  `approved == false` even if `quality_score >= 80`.
- **Evidence-backed**: the score reflects real issues, not gut feel —
  start from 100 and subtract 10 per HIGH finding, 5 per MEDIUM,
  2 per LOW.

## Instructions
1. Parse the `CodeArtifact` JSON.
2. Mentally design 2-5 integration tests and note how many would pass.
3. Scan for common vulnerabilities: SQL/command/LDAP injection, XSS,
   SSRF, insecure deserialization, hardcoded secrets, missing input
   validation.
4. For each issue, add one entry to `security_findings` naming the
   OWASP/CWE id and the file/line or function.
5. Compute `quality_score` from the formula in Success Criteria.
6. Set `approved = (quality_score >= 80 AND no critical findings)`.
7. Emit JSON only.

## Background
- Artifact schema: `lightagent/agents/subgraphs/artifacts.py::QAReport`.
- Parsed via `QAReport.model_validate`; parse failure stores
  `quality_score=0.0, approved=False`.
- Downstream: feeds the reviewer agent as one of the inputs to the
  final `ReviewResult.score`.

## Examples

### Positive
Input: clean FastAPI endpoint using parameterized queries.

{
  "integration_tests_run": 4,
  "integration_tests_passed": 4,
  "security_findings": [
    "CWE-20: input validation on 'report_id' should reject non-UUID strings before the DB lookup"
  ],
  "quality_score": 92.0,
  "approved": true
}

### Negative (what NOT to do)
{
  "integration_tests_run": 2,
  "integration_tests_passed": 5,
  "security_findings": ["security is ok"],
  "quality_score": 120.0,
  "approved": false
}

Problems:
- `integration_tests_passed > integration_tests_run` — impossible.
- `security_findings` entry is not actionable and has no OWASP/CWE id.
- `quality_score > 100` — schema violation.
- `approved == false` but no critical finding listed — inconsistent
  with the score rule.
"""


async def qa_agent_node(state: AgentState) -> dict[str, Any]:
    """Run QA checks on the CodeArtifact and produce a QAReport.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with QAReport in metadata.
    """
    with otel.start_span("dev_pipeline.qa_agent") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "qa_agent")

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
