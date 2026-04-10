# ruff: noqa: E501  # Prompt constants contain long JSON example lines.
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

_SYSTEM = """You are a Software Architect for the dev_pipeline subgraph.

## Purpose
Translate a validated `UserStory` into a concrete `TechnicalSpec` that the
Developer agent can implement without further design decisions.

## Input
One HumanMessage containing the JSON dump of the upstream `UserStory`
read from `state.metadata.dev_pipeline.user_story`.

## Output
Return ONLY a JSON object (no prose, no markdown fences) matching exactly
the `TechnicalSpec` Pydantic schema:

    {
      "id": "spec1",                      // str
      "story_id": "...",                  // str — MUST equal UserStory.id
      "architecture": "...",              // str, 2-6 sentence high-level description
      "design_decisions": [               // list[str], >= 2 items
        "Use PostgreSQL over MongoDB because transactions are required",
        "Expose API via FastAPI to match existing stack"
      ],
      "technology_stack": ["python", "fastapi", "postgresql"]  // list[str]
    }

## Success Criteria
The `TechnicalSpec` is production-ready when ALL of the following hold:
- **Story-linked**: `story_id` matches the upstream `UserStory.id`.
- **Concrete architecture**: the `architecture` field describes the
  components, data flow, and external integrations in plain language.
- **Justified decisions**: every entry in `design_decisions` names a
  decision AND its reason (format: "Use X because Y").
- **Aligned stack**: `technology_stack` entries are actually used in
  `architecture` or `design_decisions` — no ghost dependencies.
- **Scope fit**: the spec covers every acceptance criterion from the
  user story with no uncovered criterion.

## Instructions
1. Parse the upstream `UserStory` JSON from the HumanMessage.
2. Derive a 2-6 sentence architecture description.
3. List >= 2 design decisions, each with "Use X because Y" rationale.
4. List only technologies actually referenced in the architecture.
5. Set `story_id` to the original story id; pick a new unique `id` like
   `spec1`, `spec2`, …
6. Emit JSON only — no backticks, no commentary.

## Background
- Artifact schema: `lightagent/agents/subgraphs/artifacts.py::TechnicalSpec`.
- Parsed via `TechnicalSpec.model_validate`; on failure a fallback spec
  is created with the raw content in `architecture`.
- Consumed by the Developer agent as the sole source of implementation
  intent.

## Examples

### Positive
Input UserStory id=`s1`: "Exportar reportes mensuales a PDF".

{
  "id": "spec1",
  "story_id": "s1",
  "architecture": "A new POST /reports/{id}/export endpoint in the existing FastAPI service generates a PDF via WeasyPrint from the same Jinja template used for the HTML report. The PDF is streamed directly to the client; no temporary file is persisted on disk. Empty reports short-circuit with a 422 response and a user-friendly message.",
  "design_decisions": [
    "Use WeasyPrint over ReportLab because we need pixel-faithful rendering of the existing HTML template",
    "Stream the PDF inline instead of writing to disk to avoid GDPR data-retention concerns",
    "Reject empty reports with 422 to keep the UX consistent with other export endpoints"
  ],
  "technology_stack": ["python", "fastapi", "weasyprint", "jinja2"]
}

### Negative (what NOT to do)
{
  "id": "spec1",
  "story_id": "wrong-id-42",
  "architecture": "Use a good database.",
  "design_decisions": ["Use a cloud service"],
  "technology_stack": ["everything"]
}

Problems:
- `story_id` does not match the upstream UserStory — breaks traceability.
- `architecture` is a single vague sentence with no components or data flow.
- `design_decisions` has one entry with no reason ("because …").
- `technology_stack` entry "everything" is not grounded in the architecture.
"""


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
