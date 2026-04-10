# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
"""Reviewer agent node for the dev_pipeline subgraph.

Produces a :class:`~lightagent.agents.subgraphs.artifacts.ReviewResult` with
a score in [0.0, 1.0].  When score < 0.8 the approval gate routes back to
the developer node.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lightagent.agents.subgraphs.artifacts import ReviewResult
from lightagent.monitoring.otel import OTelManager
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = structlog.get_logger("lightagent.subgraphs.dev_pipeline.reviewer")
otel = OTelManager()

_SYSTEM = """You are a Senior Code Reviewer for the dev_pipeline subgraph.

## Purpose
Provide the FINAL quality verdict on all dev_pipeline artifacts
(UserStory, TechnicalSpec, CodeArtifact, TestReport, QAReport) and emit
a `ReviewResult` whose score drives the approval gate.

## Input
One HumanMessage containing the full JSON dump of
`state.metadata.dev_pipeline` (all prior artifacts).

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching
exactly the `ReviewResult` Pydantic schema:

    {
      "score": 0.85,                      // float in [0.0, 1.0]
      "approved": true,                   // bool
      "strengths": ["clean code"],        // list[str]
      "improvements": ["add more docs"],  // list[str]
      "blocking_issues": []               // list[str]
    }

## Success Criteria
The `ReviewResult` itself is well-formed when ALL of the following hold:
- **Score rubric**: `score >= 0.8` means production-ready and aligns
  with the downstream `score_gate` threshold (0.8). Changing this
  here requires changing it in `subgraphs/gates.py`.
- **Deterministic scoring**: start from 1.0 and apply:
    - `-0.3` per HIGH severity issue
    - `-0.1` per MEDIUM severity issue
    - `-0.05` per LOW severity issue
    Clip the result to [0.0, 1.0].
- **Consistency**: `approved == true` iff `score >= 0.8` AND
  `blocking_issues` is empty.
- **Specificity**: every entry in `improvements` and `blocking_issues`
  references a specific file/function/line or artifact field.
- **Strengths non-empty** when `score >= 0.8`: reviewers must also
  document what the code got right.

## Instructions
1. Parse the dev_pipeline metadata JSON.
2. For each artifact (story, spec, code, tests, QA), note issues by
   severity.
3. Compute the score using the deterministic rubric above.
4. Populate `blocking_issues` ONLY with items that prevent production
   (security criticals, data corruption, legal/compliance failures).
5. Set `approved = (score >= 0.8 AND blocking_issues is empty)`.
6. Write at least one `strengths` entry when approved.
7. Emit JSON only.

## Background
- Artifact schema: `lightagent/agents/subgraphs/artifacts.py::ReviewResult`.
- Parsed via `ReviewResult.model_validate`; parse failure stores
  `score=0.0, approved=False`.
- The 0.8 threshold is the canonical gate value; it MUST stay in sync
  with `subgraphs/gates.py::score_gate(threshold=0.8)` and the critic
  base agent's rubric.

## Examples

### Positive (score >= 0.8)
{
  "score": 0.87,
  "approved": true,
  "strengths": [
    "CodeArtifact maps every TechnicalSpec decision to an implementation line",
    "TestReport coverage 92.5% with both happy-path and edge-case tests",
    "QAReport shows 0 HIGH findings and 1 LOW input-validation suggestion"
  ],
  "improvements": [
    "Add a MEDIUM-priority rate-limit middleware on POST /reports/{id}/export (suggested 10 req/min/user)",
    "Extend docstring on `export_report` with an example invocation"
  ],
  "blocking_issues": []
}

Scoring trace: start 1.0, subtract 0.1 (one MEDIUM) and 0.05 (one LOW) -> 0.85; round to 0.87 based on strengths weighting.

### Negative (what NOT to do)
{
  "score": 0.95,
  "approved": true,
  "strengths": [],
  "improvements": [],
  "blocking_issues": [
    "Hardcoded AWS access key in settings.py"
  ]
}

Problems:
- Blocking issue present AND `approved == true` — inconsistent.
- `strengths` empty while score is 0.95 — no evidence for the high score.
- Score 0.95 with a hardcoded secret (should be clipped to 0.0 due to
  HIGH security issue -> 1.0 - 0.3 = 0.7, further reduced by blocking).
"""


async def reviewer_agent_node(state: AgentState) -> dict[str, Any]:
    """Review all pipeline artifacts and produce a ReviewResult.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with ReviewResult in metadata.
    """
    with otel.start_span("dev_pipeline.reviewer") as span:
        span.set_attribute("lightagent.subgraph", "dev_pipeline")
        span.set_attribute("lightagent.agent", "reviewer")

        dp: dict[str, Any] = dict(state.get("metadata", {}).get("dev_pipeline", {}))

        llm = ProviderRegistry().get_llm()
        messages = [
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=f"Pipeline artifacts:\n{json.dumps(dp, indent=2)}"),
        ]
        response = await llm.ainvoke(messages)
        content = str(response.content)

        try:
            data = json.loads(content)
            result = ReviewResult.model_validate(data)
        except Exception:
            result = ReviewResult(
                score=0.0, approved=False, strengths=[], improvements=[]
            )

        dp["review_result"] = result.model_dump()
        logger.info("reviewer.result", score=result.score, approved=result.approved)

        verdict = "APPROVED" if result.approved else "NEEDS REVISION"
        return {
            "current_agent": "reviewer",
            "messages": [
                AIMessage(content=f"Review score: {result.score:.2f} | {verdict}")
            ],
            "metadata": {**state.get("metadata", {}), "dev_pipeline": dp},
        }
